"""
ortho_main.py — ORTHO 단독 봇 진입점 (가상매매) [TARGET: 15분봉]
════════════════════════════════════════════════════════════════════
GitHub Actions 15분 cron. SINGLE_SYMBOL 환경변수가 있으면 그 심볼만(매트릭스),
없으면 SYMBOLS 전체를 순회한다.

심볼별 흐름:
  1) 맥락 데이터 수집(ls/taker)        — ortho_data.collect_context
  2) ORTHO-3 평가 → 가상 신호 0~2건    — ortho_engine.evaluate
  3) Notion 가상기록 (Status=OPEN)      — ortho_notion.log_signal  (항상)
  4) 텔레그램 알림                       — ortho_notify.notify_signal (ALERT_ENABLED=true 일 때만)

실주문 없음. 채점은 ortho_resolver(별도 5분 cron)가 수행.
"""
import logging
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(__file__))
import ortho_config as oc
import ortho_data as od
import ortho_engine as engine
import ortho_notion as notion
import ortho_notify as notify
import timeutil


def setup_logging():
    logging.basicConfig(level=getattr(logging, oc.LOG_LEVEL, logging.INFO),
                        format="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")
    return logging.getLogger("ortho.main")


def process_symbol(exchange, symbol: str, open_idx: dict, logger) -> int:
    """한 심볼 평가·기록·알림. 발생 신호 수 반환.
    open_idx(현재 OPEN 색인)로 중복 셋업·슬롯 초과를 차단해 과적재를 막는다."""
    try:
        context = od.collect_context(exchange, symbol)
        signals = engine.evaluate(exchange, symbol, context)
        if not signals:
            logger.info(f"[{symbol}] 신호 없음")
            return 0
        logged = 0
        for sig in signals:
            sym = sig["symbol"]
            dr  = (sig.get("direction") or "").lower()
            pol = sig["polarity"]
            # ① 동일 셋업이 이미 OPEN → 해소 전까지 재진입 금지(중복 적재 차단)
            if (sym, pol, dr) in open_idx["keys"]:
                logger.info(f"[{sym}] {pol} {dr.upper()} 이미 OPEN — 중복 스킵")
                continue
            # ② 방향별 동시 슬롯(MAX_POS_DIR) 초과 → 스킵 (심볼·방향 단위)
            if open_idx["dir_count"].get((sym, dr), 0) >= oc.MAX_POS_DIR:
                logger.info(f"[{sym}] {dr.upper()} 슬롯 {oc.MAX_POS_DIR} 초과 — 스킵")
                continue
            # ③ A-3 포트폴리오 방향 노출 캡 — 전 심볼 통틀어 동시 동일방향 ≤ MAX_CONCURRENT_DIR.
            #    크립토 상관(≈BTC 0.8+)으로 동시 다발 동일방향이 함께 무너지는 손실 클러스터를 차단.
            if open_idx["glob_dir"].get(dr, 0) >= oc.MAX_CONCURRENT_DIR:
                logger.info(f"[{sym}] 동시 {dr.upper()} {oc.MAX_CONCURRENT_DIR}개 한도 — 상관 노출 차단")
                continue
            notion.log_signal(sig)        # 기록
            notify.notify_signal(sig)     # ALERT_ENABLED=true 일 때만 발송
            # 같은 실행 내 다른 심볼/폴라리티와의 중복·과밀도 막도록 색인 갱신
            open_idx["keys"].add((sym, pol, dr))
            open_idx["dir_count"][(sym, dr)] = open_idx["dir_count"].get((sym, dr), 0) + 1
            open_idx["glob_dir"][dr] = open_idx["glob_dir"].get(dr, 0) + 1
            logged += 1
        return logged
    except Exception as e:
        logger.error(f"[{symbol}] 처리 오류: {e}\n{traceback.format_exc()}")
        return 0


def main():
    logger = setup_logging()
    logger.info("=" * 55)
    logger.info(f"🤖 ORTHO 봇 시작 — {timeutil.now_kst_str()}")
    logger.info(f"   {oc.summary()}")
    logger.info("=" * 55)

    exchange = od.create_exchange()

    single = os.getenv("SINGLE_SYMBOL", "").strip()
    symbols = [single] if single else oc.SYMBOLS

    # 현재 OPEN 색인 1회 로드 → 중복 셋업·슬롯 초과 진입 차단(과적재 방지)
    open_idx = notion.open_index()
    logger.info(f"   기존 OPEN: {len(open_idx['keys'])}건 (중복 진입 차단 기준)")

    total = 0
    for sym in symbols:
        total += process_symbol(exchange, sym, open_idx, logger)

    mode = "알림ON" if oc.ALERT_ENABLED else "학습기간(알림OFF)"
    logger.info("-" * 55)
    logger.info(f"📊 완료 — 신호 {total}건 | {mode}")
    logger.info("=" * 55)


if __name__ == "__main__":
    main()
