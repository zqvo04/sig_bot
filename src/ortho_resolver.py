"""
ortho_resolver.py — ORTHO 가상신호 채점기 (자립형) [TARGET: 15분봉]
════════════════════════════════════════════════════════════════════
Notion DB의 Status=OPEN 가상신호를 OKX 실제 가격으로 triple-barrier 판정한다.
신호 생성과 독립 실행 — GitHub Actions에서 5분 cron 권장(채점 지연 최소화).

판정(5m 캔들):
  LONG : high>=TP → WIN / low<=SL → LOSS
  SHORT: low<=TP  → WIN / high>=SL → LOSS
  동일 캔들 TP·SL 동시 → 보수적으로 LOSS
  시간 한도(bars_limit=2h) 도달 시 TP·SL 미도달이면 →
     그 시점 종가 기준으로 이익이면 WIN, 손실이면 LOSS (TIMEOUT 없음).
  모든 판정에 PnL%(진입가 대비 손익률)·MFE/MAE(R) 기록.
  시간 미경과 시 OPEN 유지(다음 실행 재시도, 멱등 — 2h 전 TP/SL 도달도 매 5분 포착).
"""
import logging
import math
import sys, os
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
import ortho_config as oc
import ortho_data as od
import ortho_notion as notion
import timeutil

logger = logging.getLogger("ortho.resolver")

_EVAL_TF   = "5m"
_EVAL_MULT = 3          # 15m 1봉 = 5m 3봉


def _parse_signaled_at(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timeutil.KST)
    return dt


def evaluate_outcome(exchange, sig: dict) -> dict:
    res = {"resolved": False, "status": None, "mfe_r": 0.0, "mae_r": 0.0,
           "bars": 0, "pnl_pct": 0.0, "pnl_r": 0.0, "exit_price": None, "exit_reason": None}
    direction, entry = sig.get("direction"), sig.get("entry")
    tp, sl, r_dist = sig.get("tp"), sig.get("sl"), sig.get("r_dist")
    bars_limit = int(sig.get("bars_limit") or oc.T_MAX)
    if not all([direction, entry, tp, sl, r_dist]) or r_dist <= 0 or not sig.get("signaled_at"):
        return res

    since_ms = int(_parse_signaled_at(sig["signaled_at"]).timestamp() * 1000)
    bars_eval = bars_limit * _EVAL_MULT
    try:
        candles = exchange.fetch_ohlcv(od.to_ccxt_swap(sig["symbol"]), _EVAL_TF,
                                       since=since_ms, limit=bars_eval + 5)
    except Exception as e:
        logger.warning(f"[resolver] {sig.get('symbol')} OHLCV 실패: {e}")
        return res
    if not candles:
        return res

    is_long = (direction == "long")
    mfe = mae = 0.0
    used = 0
    exit_price = None
    # A-1 본전스톱: +BE_TRIGGER_R 도달 시 손절을 진입가(+BE_LOCK_R)로 이동.
    #   · 무장(arming)은 봉 끝에서 → 다음 봉부터 적용(동일봉 arm&exit 인트라바 모호성 제거).
    #   · 무장 후엔 BE가 원SL을 대체(더 타이트, 진입가 쪽). lock>0 → BE청산은 소액 WIN(수수료 버퍼).
    be_trig  = (oc.BE_TRIGGER_R * r_dist) if oc.BE_TRIGGER_R else None
    be_lock  = oc.BE_LOCK_R * r_dist
    be_level = None
    armed    = False
    for c in candles:
        ts, high, low, close = c[0], c[2], c[3], c[4]
        if ts <= since_ms:
            continue
        used += 1
        if is_long:
            fav, adv = high - entry, entry - low
            hit_tp, hit_sl = high >= tp, low <= sl
            hit_be = armed and low <= be_level
        else:
            fav, adv = entry - low, high - entry
            hit_tp, hit_sl = low <= tp, high >= sl
            hit_be = armed and high >= be_level
        mfe = max(mfe, fav); mae = max(mae, adv)
        # 우선순위(보수적): 무장 시 BE가 원SL을 흡수 → BE 먼저. 동일봉 TP·스톱 동시는 스톱 우선.
        if hit_be:
            res["resolved"], res["status"] = True, ("WIN" if be_lock > 0 else "LOSS")
            exit_price, res["exit_reason"] = be_level, "BE"; break
        if (not armed) and hit_sl:
            res["resolved"], res["status"], exit_price = True, "LOSS", sl
            res["exit_reason"] = "SL"; break
        if hit_tp:
            res["resolved"], res["status"], exit_price = True, "WIN", tp
            res["exit_reason"] = "TP"; break
        if used >= bars_eval:
            # 시간 한도(2h) 도달 — TP/SL 미도달 → 그 시점 종가로 성패 판정
            gain = (close - entry) if is_long else (entry - close)
            res["resolved"], res["status"] = True, ("WIN" if gain > 0 else "LOSS")
            exit_price, res["exit_reason"] = close, "TIME"; break
        # 봉 끝: 누적 MFE가 무장 임계 도달 → 다음 봉부터 본전스톱 가동
        if (not armed) and be_trig and mfe >= be_trig:
            armed = True
            be_level = (entry + be_lock) if is_long else (entry - be_lock)

    if exit_price is not None and entry:
        pnl = (exit_price - entry) if is_long else (entry - exit_price)
        res["pnl_pct"] = round(pnl / entry * 100.0, 3)
        res["pnl_r"]   = round(pnl / r_dist, 3)      # C-1 실현 R (PnL%가 아닌 R로 평가)
        res["exit_price"] = exit_price
    res["mfe_r"] = round(mfe / r_dist, 3)
    res["mae_r"] = round(mae / r_dist, 3)
    res["bars"]  = math.ceil(used / _EVAL_MULT)
    return res


def _setup_logging():
    logging.basicConfig(level=getattr(logging, oc.LOG_LEVEL, logging.INFO),
                        format="%(asctime)s [%(levelname)-7s] %(name)s — %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")


def main():
    _setup_logging()
    logger.info("=" * 55)
    logger.info(f"🔎 ORTHO 채점기 — {timeutil.now_kst_str()} | {oc.summary()}")
    if not notion.enabled():
        logger.error("❌ Notion 미설정(NOTION_TOKEN/NOTION_DATABASE_ID) — 종료")
        sys.exit(1)

    open_sigs = notion.query_open()
    logger.info(f"   OPEN: {len(open_sigs)}건")
    # FN 측정: Shadow DB의 OPEN도 *같은* triple-barrier로 채점(별도 DB → 라이브 통계 불변).
    #   page_id로 PATCH하므로 어느 DB 소속이든 update_outcome이 그대로 동작.
    if oc.SHADOW_ENABLED:
        shadow_open = notion.query_open(database_id=oc.NOTION_SHADOW_DB_ID)
        logger.info(f"   🌑 Shadow OPEN: {len(shadow_open)}건")
        open_sigs += shadow_open
    if not open_sigs:
        return

    exchange = od.create_exchange()
    counts = {"WIN": 0, "LOSS": 0, "OPEN": 0, "ERROR": 0}
    for sig in open_sigs:
        try:
            r = evaluate_outcome(exchange, sig)
            if not r["resolved"]:
                counts["OPEN"] += 1; continue
            notion.update_outcome(sig["page_id"], r["status"],
                                  mfe_r=r["mfe_r"], mae_r=r["mae_r"],
                                  bars_to_exit=r["bars"], pnl_pct=r["pnl_pct"],
                                  pnl_r=r["pnl_r"], exit_reason=r["exit_reason"])
            counts[r["status"]] = counts.get(r["status"], 0) + 1
            logger.info(f"   {str(sig.get('symbol')):<10}{(sig.get('direction') or '').upper():<6}"
                        f"→ {r['status']:<5} {str(r['exit_reason'] or ''):<4} "
                        f"PnL {r['pnl_pct']:+.2f}% / R {r['pnl_r']:+.2f} "
                        f"(MFE {r['mfe_r']:+.2f} / MAE {r['mae_r']:+.2f})")
        except Exception as e:
            counts["ERROR"] += 1
            logger.error(f"   ❌ {sig.get('symbol')} 판정 오류: {e}")

    logger.info(f"📊 완료 — WIN:{counts['WIN']} LOSS:{counts['LOSS']} "
                f"미결:{counts['OPEN']} 오류:{counts['ERROR']}")
    logger.info("=" * 55)


if __name__ == "__main__":
    main()
