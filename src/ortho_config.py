"""
ortho_config.py — ORTHO-3 단독 봇 통합 설정 [TARGET: 15분봉]
════════════════════════════════════════════════════════════════════
레거시(config.py 207상수, 스코어러, 레짐분류) 전면 폐기 후의 유일한 설정 파일.

핵심 철학:
  ① 매 판정은 그 코인의 "최근 자기 분포 백분위" 안에서만 (미시 레짐, 매크로 무관)
  ② 점수를 더하지 않는다 — 축은 동의(AND)/거부(VETO)만
  ③ 절대 숫자 임계 금지 — 모든 컷은 백분위
  ④ 롱/숏 완전 대칭
  ⑤ 파라미터 12개 하드캡
  ⑥ 무상태(stateless)

운영 흐름(페이퍼 트레이딩):
  · ortho_main   (15분 cron): 신호 평가 → Notion 가상기록 → (알림 ON이면 텔레그램)
  · ortho_resolver(5분 cron): OPEN 가상신호를 실제 가격으로 채점(WIN/LOSS/TIMEOUT)
  · 2주 학습기간: ALERT_ENABLED=false (기록·채점만, 알림 OFF) → 데이터 축적
  · 학습 후: 진입기준 보정 → ALERT_ENABLED=true 로 알림 ON
"""
import os


def _flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


# ══════════════════════════════════════════════════════════════════
# API / 환경 (GitHub Secrets)
# ══════════════════════════════════════════════════════════════════
OKX_API_KEY    = os.getenv("OKX_API_KEY", "")
OKX_API_SECRET = os.getenv("OKX_API_SECRET", "")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

NOTION_TOKEN       = os.getenv("NOTION_TOKEN", "")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")
NOTION_VERSION     = "2022-06-28"

OKX_BASE  = "https://www.okx.com/api/v5"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# 모니터링 심볼
SYMBOLS = ["BTC/USDT", "ETH/USDT", "HYPE/USDT", "SOL/USDT", "SUI/USDT", "XRP/USDT"]

# ══════════════════════════════════════════════════════════════════
# ★ 텔레그램 알림 ON/OFF (학습기간엔 OFF) ★
# ══════════════════════════════════════════════════════════════════
#   ALERT_ENABLED=false : 알림 OFF — Notion 가상기록·채점만 (2주 학습기간 기본)
#   ALERT_ENABLED=true  : 알림 ON  — 신규 신호 시 텔레그램 발송 (학습 완료 후)
ALERT_ENABLED = _flag("ALERT_ENABLED", "false")

# 기록/채점은 항상 동작(알림과 무관). 끄고 싶으면 false.
NOTION_ENABLED = bool(NOTION_TOKEN and NOTION_DATABASE_ID)

# ══════════════════════════════════════════════════════════════════
# 12개 파라미터 하드캡 (실질 튜닝: W_L·P_EXT·P_FLOW 3개)
# ══════════════════════════════════════════════════════════════════
W_L            = int(os.getenv("ORTHO_W_L", 72))          # #1  위치 정규화 윈도우(15m봉)
P_EXT          = float(os.getenv("ORTHO_P_EXT", 10))      # #2  위치 극단 백분위 컷
N_MEAN         = int(os.getenv("ORTHO_N_MEAN", 20))       # #3  평균(SMA) 기간 = REV TP
W_F            = int(os.getenv("ORTHO_W_F", 6))           # #4  흐름 측정 창(5m봉)
P_FLOW         = float(os.getenv("ORTHO_P_FLOW", 30))     # #5  흐름 반전 백분위 컷
LS_CROWD_VETO  = float(os.getenv("ORTHO_LS_CROWD_VETO", 0.85))  # #6  군중 과밀 거부
TAKER_VETO     = float(os.getenv("ORTHO_TAKER_VETO", 0.65))     # #7  Taker 역방향 거부
SPREAD_MAX_BPS = float(os.getenv("ORTHO_SPREAD_MAX_BPS", 5))    # #8  스프레드 거부(bps)
SL_ATR_BUF     = float(os.getenv("ORTHO_SL_ATR_BUF", 0.25))     # #9  SL 버퍼(ATR배수)
RR_MIN         = float(os.getenv("ORTHO_RR_MIN", 1.0))         # #10 구조 RR 하한
T_MAX          = int(os.getenv("ORTHO_T_MAX", 7))             # #11 타임스톱(15m봉)
MAX_POS_DIR    = int(os.getenv("ORTHO_MAX_POS_DIR", 2))       # #12 방향별 동시 슬롯

# ── 상속 고정 (업계 표준 · 튜닝 금지 · 예산 비산입) ─────────────────
N_ATR        = 14
EMA_FAST     = 9
EMA_SLOW     = 21
TF_ENTRY     = "15m"
TF_FLOW      = "5m"
TF_MID       = "1h"
TF_MACRO     = "4h"
N_15M_FETCH  = 200
N_5M_FETCH   = 48
N_HTF_FETCH  = 60      # 1h/4h 구조축용

# ── 폴라리티: 어떤 셋업을 기록할지 ───────────────────────────────
#   학습기간엔 둘 다 기록해 A/B 비교 → 우위 폴라리티만 남기는 식으로 발전
POLARITIES = tuple(p.strip() for p in os.getenv("ORTHO_POLARITIES", "REV,CONT").split(","))

# ── 데이터 수집 재시도 ────────────────────────────────────────────
MAX_RETRIES = 3
RETRY_DELAY_S = 1.5
TAKER_PERIOD = "5m"
TAKER_LOOKBACK = 12

RESOLVER_MAX_OPEN_PER_RUN = int(os.getenv("ORTHO_RESOLVER_MAX", 100))


def cont_pullback_band():
    """연속형 눌림목 백분위 밴드: (하한, 중심, 상한)."""
    return (P_EXT, 50.0, 100.0 - P_EXT)


def summary() -> str:
    return (f"ALERT={'ON' if ALERT_ENABLED else 'OFF(학습)'} "
            f"| W_L={W_L} P_EXT={P_EXT} W_F={W_F} P_FLOW={P_FLOW} RR_MIN={RR_MIN} "
            f"| POLARITIES={','.join(POLARITIES)} "
            f"| notion={'ON' if NOTION_ENABLED else 'OFF'}")
