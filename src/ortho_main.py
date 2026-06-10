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


def process_symbol(exchange, symbol: str, logger) -> int:
    """한 심볼 평가·기록·알림. 발생 신호 수 반환."""
    try:
        context = od.collect_context(exchange, symbol)
        signals = engine.evaluate(exchange, symbol, context)
        if not signals:
            logger.info(f"[{symbol}] 신호 없음")
            return 0
        for sig in signals:
            notion.log_signal(sig)        # 항상 기록
            notify.notify_signal(sig)     # ALERT_ENABLED=true 일 때만 발송
        return len(signals)
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

    total = 0
    for sym in symbols:
        total += process_symbol(exchange, sym, logger)

    mode = "알림ON" if oc.ALERT_ENABLED else "학습기간(알림OFF)"
    logger.info("-" * 55)
    logger.info(f"📊 완료 — 신호 {total}건 | {mode}")
    logger.info("=" * 55)


if __name__ == "__main__":
    main()
