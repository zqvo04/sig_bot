"""
ortho_notify.py — ORTHO 텔레그램 알림 (ON/OFF 토글) [TARGET: 15분봉]
════════════════════════════════════════════════════════════════════
ALERT_ENABLED 플래그로 알림을 켜고 끈다.
  · 학습기간(ALERT_ENABLED=false): notify_signal()은 즉시 no-op → 알림 안 감.
  · 학습 후(ALERT_ENABLED=true): 신규 가상신호를 텔레그램으로 발송.
알림과 무관하게 Notion 기록/채점은 항상 동작한다(가상매매 데이터 축적).
"""
import logging
import time

import requests

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import ortho_config as oc

logger = logging.getLogger("ortho.notify")
_TG = "https://api.telegram.org/bot{token}/{method}"

_DIR_ICON = {"long": "🟢 LONG", "short": "🔴 SHORT"}


def _send(text: str) -> bool:
    if not oc.TELEGRAM_BOT_TOKEN or not oc.TELEGRAM_CHAT_ID:
        logger.warning("[notify] 텔레그램 토큰/챗ID 미설정 — 발송 스킵")
        return False
    url = _TG.format(token=oc.TELEGRAM_BOT_TOKEN, method="sendMessage")
    payload = {"chat_id": oc.TELEGRAM_CHAT_ID, "text": text,
               "parse_mode": "HTML", "disable_web_page_preview": True}
    for attempt in range(1, 4):
        try:
            r = requests.post(url, json=payload, timeout=10)
            if r.status_code == 200 and r.json().get("ok"):
                return True
            if r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", 5)))
            else:
                time.sleep(2 * attempt)
        except Exception as e:
            logger.error(f"[notify] 텔레그램 오류: {e}")
            time.sleep(2 * attempt)
    return False


def _fmt(p, sym):
    if p is None:
        return "N/A"
    return f"{p:,.2f}" if any(s in sym for s in ("BTC", "ETH")) else f"{p:,.4f}"


def build_message(sig: dict) -> str:
    d = sig["direction"]
    head = _DIR_ICON.get(d, d.upper())
    poly = "회귀형(REV)" if sig["polarity"] == "REV" else "연속형(CONT)"
    sym = sig["symbol"]
    return (
        f"<b>📡 ORTHO 신호 · {sym}</b>\n"
        f"{head}  ·  {poly}  ·  RR {sig.get('rr','?')}\n"
        f"───────────────\n"
        f"진입 <b>{_fmt(sig['entry'], sym)}</b>\n"
        f"TP   {_fmt(sig['tp'], sym)}\n"
        f"SL   {_fmt(sig['sl'], sym)}\n"
        f"───────────────\n"
        f"위치 L={sig.get('l_pct')}  흐름 F={sig.get('f_pct')}  구조 {sig.get('s_state')}\n"
        f"국면 {sig.get('macro_tag')}\n"
        f"💰 수량 <b>{sig.get('size')}</b> (≈{sig.get('notional')} USDT) · "
        f"위험 {sig.get('risk_quote')} USDT = 1R({sig.get('risk_pct')}%)\n"
        f"⏱ 타임스톱 {sig.get('bars_limit')}봉(15m) · BE +{oc.BE_TRIGGER_R}R\n"
        f"<i>※ 가상매매(페이퍼) 신호 — 실주문 아님</i>"
    )


def notify_signal(sig: dict) -> bool:
    """ALERT_ENABLED=true 일 때만 텔레그램 발송. 학습기간엔 no-op."""
    if not oc.ALERT_ENABLED:
        logger.info(f"[notify] 알림 OFF(학습기간) — {sig['symbol']} {sig['polarity']} 미발송")
        return False
    ok = _send(build_message(sig))
    logger.info(f"[notify] {'✅ 발송' if ok else '❌ 실패'} {sig['symbol']} {sig['polarity']} {sig['direction'].upper()}")
    return ok


def send_text(text: str) -> bool:
    """운영 공지/오류 알림 (ALERT_ENABLED 무관 — 시스템 메시지)."""
    return _send(text)
