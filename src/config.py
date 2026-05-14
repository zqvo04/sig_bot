"""
config.py — 전역 설정
────────────────────────────────────────────────────────────────────[...]
[v3 전면 재조정 — 2026-05]

설계 원칙:
  1. 단순성: 곱셈 패널티를 EMA 배율 하나로 통합 (ADX 멀티플라이어 제거)
  2. 균형:   RANGING 역방향 배율 완화 (반등 신호 포착 허용)
  3. 보수성: 보너스 항목 18개로 정리, 최대 캡 36pt (기존 35pt)
  4. 명확성: 중복/노이즈 보너스 11개 제거
"""
import os

# ══════════════════════════════════════════════════════════════
# API / 환경
# ══════════════════════════════════════════════════════════════
OKX_API_KEY    = os.getenv("OKX_API_KEY",    "")
OKX_API_SECRET = os.getenv("OKX_API_SECRET", "")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "")

SYMBOLS: list = ["BTC/USDT", "ETH/USDT", "HYPE/USDT", "SOL/USDT", "SUI/USDT"]

TIMEFRAMES    = {"entry": "15m", "mid": "1h", "macro": "4h"}
CANDLE_LIMITS = {"15m": 100, "1h": 210, "4h": 210}

# ══════════════════════════════════════════════════════════════
# 지표 파라미터
# ══════════════════════════════════════════════════════════════
RSI_PERIOD     = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD   = 30
BOLLINGER_PERIOD = 20
BOLLINGER_STD    = 2.0
ATR_PERIOD = 14
EMA_FAST = 9
EMA_SLOW = 21

ADX_PERIOD      = 14
ADX_NO_TREND    = 20
ADX_WEAK_TREND  = 25
ADX_STRONG      = 50

VOLUME_CONFIRM_LOOKBACK   = 20
VOLUME_SPIKE_MULTIPLIER   = 1.5
VOLUME_STRONG_MULTIPLIER  = 2.5

# ══════════════════════════════════════════════════════════════
# EMA 배율 — 역방향 TF 수에 따른 억제
# ──────────────────────────────────────────────────────────────
# v3 변경:
#   RANGING:  3역방향 0.72 → 0.82  (반등 신호 포착 허용)
#   TRENDING: 3역방향 0.40 → 0.52  (여전히 패널티, 완화만)
#   ADX 별도 배율 제거 — EMA 배율이 방향 불확실성을 이미 반영
# ══════════════════════════════════════════════════════════════
EMA_MULTIPLIER = {3: 0.52, 2: 0.72, 1: 0.88, 0: 1.00}   # fallback

EMA_MULTIPLIER_RANGING   = {3: 0.82, 2: 0.90, 1: 0.96, 0: 1.00}
EMA_MULTIPLIER_TRENDING  = {3: 0.52, 2: 0.72, 1: 0.88, 0: 1.00}
EMA_MULTIPLIER_EXPLOSIVE = {3: 0.75, 2: 0.84, 1: 0.93, 0: 1.00}
EMA_MULTIPLIER_SQUEEZE   = {3: 0.80, 2: 0.87, 1: 0.95, 0: 1.00}

REGIME_EMA_MULTIPLIERS = {
    "RANGING":   EMA_MULTIPLIER_RANGING,
    "TRENDING":  EMA_MULTIPLIER_TRENDING,
    "EXPLOSIVE": EMA_MULTIPLIER_EXPLOSIVE,
    "SQUEEZE":   EMA_MULTIPLIER_SQUEEZE,
    "UNKNOWN":   EMA_MULTIPLIER,
}

# ══════════════════════════════════════════════════════════════
# 시장 심리 임계값
# ══════════════════════════════════════════════════════════════
FUNDING_LONG_STRONG  = -0.0005
FUNDING_LONG_MILD    = -0.0001
FUNDING_SHORT_MILD   =  0.0005
FUNDING_SHORT_STRONG =  0.001

LS_LONG_EXTREME  = 0.72
LS_LONG_HIGH     = 0.65
LS_SHORT_EXTREME = 0.62
LS_SHORT_HIGH    = 0.55

OI_CHANGE_STRONG = 0.05
OI_CHANGE_MILD   = 0.02

TAKER_LOOKBACK    = 100
TAKER_STRONG_BUY  = 0.65
TAKER_STRONG_SELL = 0.65

LIQ_LOOKBACK_MINUTES  = 60
LIQ_LARGE_THRESHOLD   = 500_000
LIQ_SIGNAL_THRESHOLD  = 1_000_000

REGIME_SQUEEZE_RATIO = 0.70
REGIME_TREND_ADX     = 25
REGIME_STRONG_ADX    = 40

# ══════════════════════════════════════════════════════════════
# 국면별 가중치
# ──────────────────────────────────────────────────────────────
# RANGING: RSI·BB 비중 높음 (반전 신호 중심)
# TRENDING: Taker·OI 비중 높음 (모멘텀 중심)
# ══════════════════════════════════════════════════════════════
SCORE_WEIGHTS = {
    "rsi": 0.25, "bollinger": 0.20, "funding_rate": 0.17,
    "long_short_ratio": 0.13, "taker_volume": 0.13, "oi_change": 0.07, "volume": 0.05,
}
SCORE_WEIGHTS_RANGING = {
    "rsi": 0.28, "bollinger": 0.24, "funding_rate": 0.13,
    "long_short_ratio": 0.12, "taker_volume": 0.10, "oi_change": 0.05, "volume": 0.08,
}
SCORE_WEIGHTS_TRENDING = {
    "rsi": 0.11, "bollinger": 0.09, "funding_rate": 0.15,
    "long_short_ratio": 0.15, "taker_volume": 0.22, "oi_change": 0.18, "volume": 0.10,
}
SCORE_WEIGHTS_EXPLOSIVE = {
    "rsi": 0.07, "bollinger": 0.06, "funding_rate": 0.15,
    "long_short_ratio": 0.17, "taker_volume": 0.23, "oi_change": 0.19, "volume": 0.13,
}
SCORE_WEIGHTS_SQUEEZE = {
    "rsi": 0.15, "bollinger": 0.28, "funding_rate": 0.13,
    "long_short_ratio": 0.13, "taker_volume": 0.15, "oi_change": 0.10, "volume": 0.06,
}
REGIME_SCORE_WEIGHTS = {
    "RANGING":   SCORE_WEIGHTS_RANGING,
    "TRENDING":  SCORE_WEIGHTS_TRENDING,
    "EXPLOSIVE": SCORE_WEIGHTS_EXPLOSIVE,
    "SQUEEZE":   SCORE_WEIGHTS_SQUEEZE,
    "UNKNOWN":   SCORE_WEIGHTS,
}

# ══════════════════════════════════════════════════════════════
# 보너스 체계 (v3: 18개로 정리, 값 하향 조정)
# ──────────────────────────────────────────────────────────────
# 제거됨:
#   OI_TAKER_CONFIRM, ADX_STRONG, TREND_VOLUME, BAND_WALKING,
#   LS_DIRECTION_CONFIRM, FUNDING_EXTREME, ATR_MOMENTUM,
#   CANDLE_CONSECUTIVE, TREND_REVERSAL_WARNING, RSI_DIVERGENCE,
#   BB_RANGING_REVERSAL (극단과매도 보너스로 통합)
# ══════════════════════════════════════════════════════════════

# 눌림목 (방향 + 타이밍 동시 확인 → 가장 신뢰도 높은 보너스)
BONUS_PULLBACK_ENTRY       = 12    # 강: 1h RSI>58 + 15m<40
BONUS_PULLBACK_ENTRY_WEAK  = 8     # 약: 1h RSI>52 + 15m<44
BONUS_PULLBACK_ENTRY_MICRO = 4     # 미세

# 추세 지속 (EMA+OI+Taker 삼중 확인)
BONUS_TREND_STRONG = 12            # 핵심 추세 추종 보너스

# 반전 신호
BONUS_BB_RSI_ALIGN         = 8     # BB극단 + RSI다이버전스
BONUS_LIQUIDATION          = 10    # 대규모 청산 꼬리 (반등/되돌림 기대)
BONUS_VOL_PRICE_DIV        = 10    # 거래량-가격 다이버전스 (신저가+거래량증가)
BONUS_FAILED_BREAKOUT      = 14    # 돌파/붕괴 실패 (페이크아웃)
BONUS_EXTREME_OVERSOLD_MTF = 10    # 전 TF RSI 극단 과매도/과매수 동시

# SMC 구조
BONUS_FVG_ENTRY          = 8      # Fair Value Gap 진입
BONUS_FVG_ENTRY_CONFLICTED = 4    # 양방향 FVG 동시 (모호, 반감)
BONUS_BOS_CONFIRM        = 6      # BOS 확증
BONUS_FIB_GOLDEN_POCKET  = 10     # 피보 황금포켓 (61.8~65%)
BONUS_FIB_KEY_LEVEL      = 5      # 피보 주요 레벨

# 캔들 패턴
BONUS_CANDLE_PIN_BAR    = 10      # 핀바
BONUS_CANDLE_ENGULFING  = 8       # 인걸핑

# 기타
BONUS_HIDDEN_DIVERGENCE = 6       # 히든 다이버전스 (추세 지속)
BONUS_VOLUME_EXPLOSION  = 7       # 거래량 폭발 + ADX
BONUS_POST_SQUEEZE      = 10      # Post-Squeeze 모멘텀
BONUS_MARKET_STRUCT_TREND = 8     # 시장구조 (HigherLow / LowerHigh)
BONUS_FUNDING_LS_ALIGN  = 6       # 펀딩비 + 롱숏 동일 방향 (약한 확인)

# ── 티어드 보너스 캡 (v3: 완화) ─────────────────────────────
# base < 36pt: 캡 18pt — 중립 시장, 보너스로 구제 불가
# 36 ≤ base < 44pt: 캡 26pt — 약한 방향성
# base ≥ 44pt: 캡 36pt — 명확한 방향성 (기존 35 → 36)
BONUS_CAP_TIERS = [(36, 18), (44, 26), (9999, 36)]

# ══════════════════════════════════════════════════════════════
# 극단 과매도/과매수 역전 신호 파라미터
# ══════════════════════════════════════════════════════════════
EXTREME_OVERSOLD_15M = 32
EXTREME_OVERSOLD_1H  = 32
EXTREME_OVERSOLD_4H  = 35

EXTREME_OVERBOUGHT_15M = 68
EXTREME_OVERBOUGHT_1H  = 68
EXTREME_OVERBOUGHT_4H  = 65

# BB streak 억제 면제: RSI < 이 값이면 클라이맥스 셀링 판단 → 억제 해제
BB_STREAK_SUPPRESS_RSI_EXEMPT = 28

# ══════════════════════════════════════════════════════════════
# 페널티 파라미터
# ══════════════════════════════════════════════════════════════
# MTF RSI 과열 패널티 (soft)
MTF_RSI_OVERBOUGHT_1H       = 72
MTF_RSI_OVERBOUGHT_1H_MILD  = 68
MTF_RSI_OVERBOUGHT_4H       = 65
MTF_RSI_OVERSOLD_1H         = 28
MTF_RSI_OVERSOLD_1H_MILD    = 32
MTF_RSI_OVERSOLD_4H         = 35
MTF_RSI_PENALTY_STRONG      = 0.85
MTF_RSI_PENALTY_MILD        = 0.92
MTF_RSI_OVERSOLD_1H_EXTREME  = 24
MTF_RSI_OVERBOUGHT_1H_EXTREME = 76

# EXPLOSIVE 소진 패널티
EXPLOSIVE_EXHAUSTION_RSI_LONG  = 70
EXPLOSIVE_EXHAUSTION_RSI_SHORT = 30
EXPLOSIVE_EXHAUSTION_PENALTY   = 0.88

# CHoCH 역방향 패널티
CHOCH_AGAINST_PENALTY = 0.88

# 캔들 모멘텀 역방향 패널티
CANDLE_MOMENTUM_PENALTY_RANGING   = 0.80
CANDLE_MOMENTUM_PENALTY_EXPLOSIVE  = 0.85
CANDLE_MOMENTUM_PENALTY_TRENDING   = 0.90

# OI 스파이크 필터
OI_SPIKE_THRESHOLD    = 0.80
OI_SPIKE_SCORE_PENALTY = 20

# ══════════════════════════════════════════════════════════════
# 반전 신호 파라미터 (추세 전환 경고용 — 현재 코드에서 미사용)
# ══════════════════════════════════════════════════════════════
REVERSAL_RSI_1H_OB     = 72
REVERSAL_RSI_1H_OS     = 28
REVERSAL_TAKER_SELL    = 0.55
REVERSAL_TAKER_BUY     = 0.55
REVERSAL_FUNDING_POS   = 0.0005
REVERSAL_FUNDING_NEG   = -0.0005

# ══════════════════════════════════════════════════════════════
# SMC / 피보나치 파라미터
# ══════════════════════════════════════════════════════════════
FIB_LOOKBACK      = 50
FIB_TOLERANCE     = 0.015
FIB_MIN_SWING_PCT = 0.03

VOL_DIV_PRICE_THRESHOLD  = 0.005
VOL_DIV_BULL_VOLUME_RATIO = 1.50
VOL_DIV_BEAR_VOLUME_RATIO = 0.67

MARKET_STRUCT_SWING_THRESHOLD = 0.005

# ══════════════════════════════════════════════════════════════
# 신호 임계값 (v3: RANGING 완화 60, TRENDING 강화 62)
# ══════════════════════════════════════════════════════════════
REGIME_THRESHOLDS = {
    "SQUEEZE":   63,
    "TRENDING":  62,   # 기존 60 → 62 (ADX 제거로 점수 상승 보정)
    "RANGING":   60,   # 기존 61 → 60 (반등 신호 완화)
    "EXPLOSIVE": 58,
}
SIGNAL_MIN_SCORE = 63

# ══════════════════════════════════════════════════════════════
# 동적 쿨다운
# ══════════════════════════════════════════════════════════════
PRICE_MOVE_SUPPRESS_STRONG  = 0.05
PRICE_MOVE_SUPPRESS_MILD    = 0.03
PRICE_MOVE_RESET_THRESHOLD  = -0.01
COOLDOWN_SUPPRESSED_STRONG  = 120
COOLDOWN_SUPPRESSED_MILD    = 75

# ══════════════════════════════════════════════════════════════
# 시스템
# ══════════════════════════════════════════════════════════════
MAX_RETRIES             = 3
RETRY_DELAY_S           = 5
SIGNAL_COOLDOWN_MINUTES = 60
SIGNAL_STATE_FILE       = "/tmp/bot_state/signal_state.json"
ORDERBOOK_DEPTH         = 20
LOG_LEVEL               = "INFO"
LOG_FILE                = "logs/bot.log"
