"""
main.py — Matrix 전략 연동 버전
각 Job은 단일 심볼만 처리 (GitHub Actions가 병렬화)
"""

import json
import logging
import os
import sys
import traceback
from datetime import datetime, timezone

# 소스 경로 추가
sys.path.insert(0, os.path.dirname(__file__))

import config
from data_pipeline   import create_exchange, collect_all_data
from analysis_engine import run_full_analysis
from scoring_system  import run_scoring_pipeline
from notification    import notify_signal, send_error_alert


# ══════════════════════════════════════════════
# 로깅 초기화
# ══════════════════════════════════════════════

def setup_logging() -> logging.Logger:
    """콘솔 + 파일 동시 출력 로거"""
    log_dir = os.path.join(os.path.dirname(__file__), '..', 'logs')
    os.makedirs(log_dir, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    try:
        log_path = os.path.join(log_dir, 'bot.log')
        fh = logging.FileHandler(log_path, encoding='utf-8')
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except Exception:
        pass

    return logging.getLogger("main")


# ══════════════════════════════════════════════
# 실행 카운터
# ══════════════════════════════════════════════

_COUNTER_FILE = "/tmp/bot_state/bot_run_counter.json"

def _load_counter() -> dict:
    try:
        if os.path.exists(_COUNTER_FILE):
            with open(_COUNTER_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {"runs": 0, "signals": 0}


def _save_counter(data: dict) -> None:
    try:
        with open(_COUNTER_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


# ══════════════════════════════════════════════
# 메인 실행 (단일 심볼 처리)
# ══════════════════════════════════════════════

def main():
    logger = setup_logging()
    start_time = datetime.now(timezone.utc)

    # ── Matrix에서 전달된 심볼 읽기 ──
    single_symbol = os.getenv("SINGLE_SYMBOL")
    if not single_symbol:
        logger.error("❌ SINGLE_SYMBOL 환경변수 없음")
        sys.exit(1)

    logger.info("=" * 55)
    logger.info(f"🤖 코인 신호 봇 실행 시작 — {start_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    logger.info(f"   심볼: {single_symbol} (Matrix Job)")
    logger.info("=" * 55)

    counter = _load_counter()
    counter["runs"] += 1
    run_num = counter["runs"]

    # OKX 클라이언트 생성
    try:
        exchange = create_exchange()
    except Exception as e:
        msg = f"OKX 클라이언트 생성 실패: {e}"
        logger.critical(msg)
        send_error_alert(msg, context="create_exchange()")
        sys.exit(1)

    # ── 단일 심볼 처리 ──
    result = {
        "symbol":    single_symbol,
        "success":   False,
        "notified":  False,
        "direction": None,
        "score":     0.0,
        "error":     None,
    }

    try:
        logger.info(f"\n{'─'*50}")
        logger.info(f"🔄 처리 중: {single_symbol}")
        logger.info(f"{'─'*50}\n")

        # 1. 데이터 수집
        collected = collect_all_data(exchange, single_symbol)

        if collected["ticker"] is None or collected["ticker"].get("last") is None:
            logger.warning(f"[{single_symbol}] 티커 수집 실패 — 스킵")
            result["error"] = "티커 수집 실패"
        else:
            # 2. 기술적 분석
            analysis = run_full_analysis(single_symbol, collected)

            # 3. 점수 산출
            pipeline = run_scoring_pipeline(single_symbol, analysis)
            result["score"]     = pipeline["score"]
            result["direction"] = pipeline["direction"]
            result["success"]   = True

            # 4. 알림 발송
            if pipeline["should_notify"]:
                sent = notify_signal(pipeline, analysis)
                result["notified"] = sent
                if sent:
                    logger.info(
                        f"[{single_symbol}] 🚨 {pipeline['direction'].upper()} "
                        f"{pipeline['score']:.1f}pt — 알림 발송 완료"
                    )
                    counter["signals"] += 1
            else:
                long_s  = pipeline["signal_result"]["long"]["final_score"]
                short_s = pipeline["signal_result"]["short"]["final_score"]
                logger.info(
                    f"[{single_symbol}] 신호 없음 — "
                    f"롱:{long_s:.1f}pt / 숏:{short_s:.1f}pt"
                )

    except Exception as e:
        err_msg = traceback.format_exc()
        logger.error(f"[{single_symbol}] 처리 오류:\n{err_msg}")
        result["error"] = str(e)

    # 실행 요약
    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()

    logger.info("\n" + "=" * 55)
    logger.info(f"📊 실행 완료 — {elapsed:.1f}초")
    status = "✅" if result["success"] else "❌"
    notif  = f"🚨{result['direction'].upper()}" if result["notified"] else "—"
    score  = f"{result['score']:.1f}pt" if result["success"] else result.get("error","?")
    logger.info(f"   {status} {single_symbol:<12} {score:<10} {notif}")
    logger.info(f"   누적 신호: {counter['signals']}건")
    logger.info("=" * 55)

    # 오류 알림
    if result["error"]:
        send_error_alert(
            f"{single_symbol}: {result['error']}",
            context=f"run #{run_num}"
        )

    _save_counter(counter)

    # 오류 있으면 exit code 1
    if result["error"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
