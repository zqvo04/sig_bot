"""
ortho_resolver.py — ORTHO 가상신호 채점기 (자립형) [TARGET: 15분봉]
════════════════════════════════════════════════════════════════════
Notion DB의 Status=OPEN 가상신호를 OKX 실제 가격으로 triple-barrier 판정한다.
신호 생성과 독립 실행 — GitHub Actions에서 5분 cron 권장(채점 지연 최소화).

판정(5m 캔들):
  LONG : high>=TP → WIN / low<=SL → LOSS
  SHORT: low<=TP  → WIN / high>=SL → LOSS
  동일 캔들 TP·SL 동시 → 보수적으로 LOSS
  bars_limit(15m) 내 미도달 → TIMEOUT
  진단치 MFE/MAE(R) 기록. 미경과 시 OPEN 유지(다음 실행 재시도, 멱등).
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
    res = {"resolved": False, "status": None, "mfe_r": 0.0, "mae_r": 0.0, "bars": 0}
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
    for c in candles:
        ts, high, low = c[0], c[2], c[3]
        if ts <= since_ms:
            continue
        used += 1
        if is_long:
            fav, adv = high - entry, entry - low
            hit_tp, hit_sl = high >= tp, low <= sl
        else:
            fav, adv = entry - low, high - entry
            hit_tp, hit_sl = low <= tp, high >= sl
        mfe = max(mfe, fav); mae = max(mae, adv)
        if hit_sl:
            res["resolved"], res["status"] = True, "LOSS"; break
        if hit_tp:
            res["resolved"], res["status"] = True, "WIN"; break
        if used >= bars_eval:
            res["resolved"], res["status"] = True, "TIMEOUT"; break

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
    if not open_sigs:
        return

    exchange = od.create_exchange()
    counts = {"WIN": 0, "LOSS": 0, "TIMEOUT": 0, "OPEN": 0, "ERROR": 0}
    for sig in open_sigs:
        try:
            r = evaluate_outcome(exchange, sig)
            if not r["resolved"]:
                counts["OPEN"] += 1; continue
            notion.update_outcome(sig["page_id"], r["status"],
                                  mfe_r=r["mfe_r"], mae_r=r["mae_r"], bars_to_exit=r["bars"])
            counts[r["status"]] = counts.get(r["status"], 0) + 1
            logger.info(f"   {str(sig.get('symbol')):<10}{(sig.get('direction') or '').upper():<6}"
                        f"→ {r['status']:<7} (MFE {r['mfe_r']:+.2f}R / MAE {r['mae_r']:+.2f}R)")
        except Exception as e:
            counts["ERROR"] += 1
            logger.error(f"   ❌ {sig.get('symbol')} 판정 오류: {e}")

    logger.info(f"📊 완료 — WIN:{counts['WIN']} LOSS:{counts['LOSS']} "
                f"TIMEOUT:{counts['TIMEOUT']} 미결:{counts['OPEN']} 오류:{counts['ERROR']}")
    logger.info("=" * 55)


if __name__ == "__main__":
    main()
