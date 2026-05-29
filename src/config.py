"""
config.py — 전역 설정 (v3.8) [TARGET: 15분봉 시그봇 / 15-MINUTE SIGBOT]
────────────────────────────────────────────────────────────────────
⚠️ 이 코드는 15분봉(15m entry) 시그봇 전용입니다. 1시간봉 버전과 혼동 금지.
   entry=15m / mid=1h / macro=4h — 모든 진입 판정 기준은 15분봉.
────────────────────────────────────────────────────────────────────
[v3.8 추가] ← 어제(2026-05-28~29) 불량신호 대응 업그레이드

㉑ LS 극단 임계값 강화 (불량신호 대응)
   LS_LONG_EXTREME  : 0.72 → 0.75 (롱 75% 이상에서만 숏 극단 90pt)
   LS_SHORT_EXTREME : 0.62 → 0.65 (숏 65% 이상에서만 롱 극단 90pt)
   근거: XRP/HYPE 불량신호 5건 전부 LS 90pt가 점수 견인.
         롱 73.5%가 90pt를 받던 임계를 상향 → 경계값 신호 차단.

㉒ RANGING 저ADX 임계값 동적 상향 파라미터 (scoring_system.py에서 사용)
   RANGING_LOW_ADX_THRESHOLD = 20
   RANGING_LOW_ADX_BOOST_CAP = 4
   RANGING_LOW_ADX_DIVISOR   = 1.5
   근거: 15m 레짐 분류가 ADX값 무관 동일 임계(63pt) 적용.
         ADX 15(노이즈)와 ADX 24(약추세)를 동일 취급하던 문제 보정.

㉓ FVG 역방향 패널티는 BONUS_FVG_ENTRY_CONFLICTED(4) 재사용 — 신규 상수 불필요

[v3.7] EXPLOSIVE 준과매도/과매수 역방향 패널티, 청산 역방향 소프트 패널티
[v3.6] 히든다이버전스 ADX 가드, SQUEEZE 캔들 감액
[v3.5] 거래량 baseline 1h 캔들 / 4
[v3.4] EXPLOSIVE+BOS 강화 패널티, ADX 역추세 임계값, 역추세 보너스 캡, FVG 모호 차단
[v3.3] SIGNAL_MIN_SCORE 제거, Volume 정규화, 거래량 페널티, lookback iloc[-2]
[v3.2] BOS_CONFLICT_PENALTY = 0.82
[v3.1] OI 완전 제거
[v3]   ADX 배율 통합, EMA 배율 정비
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

# 모니터링 대상 — 코드 기존값 그대로 유지 (사용자 지시)
SYMBOLS: list = ["BTC/USDT", "ETH/USDT", "HYPE/USDT", "SOL/USDT", "SUI/USDT", "XRP/USDT"]

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

# ── 거래량 설정 (v3.5) ────────────────────────────────────────
VOLUME_1H_BASELINE_CANDLES = 120
VOLUME_CONFIRM_LOOKBACK   = 48
VOLUME_SPIKE_MULTIPLIER   = 1.5
VOLUME_STRONG_MULTIPLIER  = 2.5
VOLUME_EXPLOSION_MULTIPLIER = 2.0

# ══════════════════════════════════════════════════════════════
# EMA 배율
# ══════════════════════════════════════════════════════════════
EMA_MULTIPLIER = {3: 0.52, 2: 0.72, 1: 0.88, 0: 1.00}

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

# [v3.8 ㉑] LS 극단 임계값 강화
# 기존: LONG_EXTREME=0.72, SHORT_EXTREME=0.62
# 변경: 롱 73.5%가 90pt를 받던 구조 → 75%로 상향, 숏도 대칭 상향
LS_LONG_EXTREME  = 0.75   # (구 0.72)
LS_LONG_HIGH     = 0.65
LS_SHORT_EXTREME = 0.65   # (구 0.62)
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
# ══════════════════════════════════════════════════════════════

SCORE_WEIGHTS = {
    "rsi":              0.25,
    "bollinger":        0.20,
    "funding_rate":     0.19,
    "long_short_ratio": 0.14,
    "taker_volume":     0.18,
    "volume":           0.04,
}

SCORE_WEIGHTS_RANGING = {
    "rsi":              0.32,
    "bollinger":        0.26,
    "funding_rate":     0.13,
    "long_short_ratio": 0.12,
    "taker_volume":     0.10,
    "volume":           0.07,
}

SCORE_WEIGHTS_TRENDING = {
    "rsi":              0.11,
    "bollinger":        0.09,
    "funding_rate":     0.15,
    "long_short_ratio": 0.22,
    "taker_volume":     0.34,
    "volume":           0.09,
}

SCORE_WEIGHTS_EXPLOSIVE = {
    "rsi":              0.07,
    "bollinger":        0.06,
    "funding_rate":     0.15,
    "long_short_ratio": 0.24,
    "taker_volume":     0.38,
    "volume":           0.10,
}

SCORE_WEIGHTS_SQUEEZE = {
    "rsi":              0.15,
    "bollinger":        0.35,
    "funding_rate":     0.13,
    "long_short_ratio": 0.13,
    "taker_volume":     0.19,
    "volume":           0.05,
}

REGIME_SCORE_WEIGHTS = {
    "RANGING":   SCORE_WEIGHTS_RANGING,
    "TRENDING":  SCORE_WEIGHTS_TRENDING,
    "EXPLOSIVE": SCORE_WEIGHTS_EXPLOSIVE,
    "SQUEEZE":   SCORE_WEIGHTS_SQUEEZE,
    "UNKNOWN":   SCORE_WEIGHTS,
}

# ══════════════════════════════════════════════════════════════
# 보너스 체계
# ══════════════════════════════════════════════════════════════

BONUS_PULLBACK_ENTRY       = 12
BONUS_PULLBACK_ENTRY_WEAK  = 8
BONUS_PULLBACK_ENTRY_MICRO = 4

BONUS_TREND_STRONG = 12

BONUS_BB_RSI_ALIGN         = 8
BONUS_LIQUIDATION          = 10
BONUS_VOL_PRICE_DIV        = 10
BONUS_FAILED_BREAKOUT      = 12
BONUS_EXTREME_OVERSOLD_MTF = 10

BONUS_FVG_ENTRY            = 8
BONUS_FVG_ENTRY_CONFLICTED = 4   # [v3.8 ㉓] FVG 역방향 패널티에도 재사용 (-4pt)
BONUS_BOS_CONFIRM          = 8
BONUS_FIB_GOLDEN_POCKET    = 10
BONUS_FIB_KEY_LEVEL        = 5

BONUS_CANDLE_PIN_BAR    = 10
BONUS_CANDLE_ENGULFING  = 8

BONUS_HIDDEN_DIVERGENCE   = 6
BONUS_VOLUME_EXPLOSION    = 7
BONUS_POST_SQUEEZE        = 10
BONUS_MARKET_STRUCT_TREND = 8
BONUS_FUNDING_LS_ALIGN    = 6

BONUS_CAP_TIERS = [(36, 18), (44, 26), (9999, 36)]

# ══════════════════════════════════════════════════════════════
# 극단 과매도/과매수
# ══════════════════════════════════════════════════════════════
EXTREME_OVERSOLD_15M = 32
EXTREME_OVERSOLD_1H  = 32
EXTREME_OVERSOLD_4H  = 35

EXTREME_OVERBOUGHT_15M = 68
EXTREME_OVERBOUGHT_1H  = 68
EXTREME_OVERBOUGHT_4H  = 65

BB_STREAK_SUPPRESS_RSI_EXEMPT = 28

# ══════════════════════════════════════════════════════════════
# 페널티 파라미터
# ══════════════════════════════════════════════════════════════
MTF_RSI_OVERBOUGHT_1H        = 72
MTF_RSI_OVERBOUGHT_1H_MILD   = 68
MTF_RSI_OVERBOUGHT_4H        = 65
MTF_RSI_OVERSOLD_1H          = 28
MTF_RSI_OVERSOLD_1H_MILD     = 32
MTF_RSI_OVERSOLD_4H          = 35
MTF_RSI_PENALTY_STRONG       = 0.85
MTF_RSI_PENALTY_MILD         = 0.92
MTF_RSI_OVERSOLD_1H_EXTREME  = 24
MTF_RSI_OVERBOUGHT_1H_EXTREME = 76

EXPLOSIVE_EXHAUSTION_RSI_LONG  = 70
EXPLOSIVE_EXHAUSTION_RSI_SHORT = 30
EXPLOSIVE_EXHAUSTION_PENALTY   = 0.88

CHOCH_AGAINST_PENALTY = 0.88
BOS_CONFLICT_PENALTY  = 0.82

CANDLE_MOMENTUM_PENALTY_RANGING   = 0.80
CANDLE_MOMENTUM_PENALTY_EXPLOSIVE  = 0.85
CANDLE_MOMENTUM_PENALTY_TRENDING   = 0.90

# [v3.6] SQUEEZE 국면 캔들 패턴 보너스 감액 배율
SQUEEZE_CANDLE_BONUS_MULT = 0.50

# Gate 패널티
GATE_PENALTY_SINGLE = 0.92
GATE_PENALTY_DUAL   = 0.80

OI_SPIKE_THRESHOLD     = 0.80
OI_SPIKE_SCORE_PENALTY = 20

# 거래량 페널티 [v3.3 patch]
VOLUME_PENALTY_LOW_THRESHOLD = 20
VOLUME_PENALTY_MID_THRESHOLD = 35
VOLUME_PENALTY_LOW = -8
VOLUME_PENALTY_MID = -5

# [v3.4] EXPLOSIVE + BOS 역방향 강화 패널티
EXPLOSIVE_BOS_CONFLICT_PENALTY = 0.85

# [v3.4] ADX 연동 역추세 임계값
ADX_COUNTER_TREND_THRESHOLD_STRONG = 45
ADX_COUNTER_TREND_THRESHOLD_MID    = 35
ADX_COUNTER_TREND_THRESHOLD_WEAK   = 25
ADX_COUNTER_TREND_BOOST_STRONG     = 15
ADX_COUNTER_TREND_BOOST_MID        = 10
ADX_COUNTER_TREND_BOOST_WEAK       = 5

# [v3.4] 역추세 보너스 캡
COUNTER_TREND_BONUS_CAP = 14

# [v3.5 B] BOS역방향 단독 보너스 캡
BOS_ONLY_BONUS_CAP = 22

# [v3.5 C강화] 저ADX+BOS역방향 억제 ADX 임계값
ADX_BOS_COUNTER_THRESHOLD = 30

# [v3.4] FVG 양방향 모호 + 저거래량 신호 차단
FVG_AMBIGUOUS_VOL_THRESHOLD = 30.0

# [v3.7 P1] EXPLOSIVE 준과매도/과매수 역방향 패널티
EXPLOSIVE_OVERSOLD_GUARD_RSI   = 45
EXPLOSIVE_OVERSOLD_GUARD_BB    = 0.25
EXPLOSIVE_OVERBOUGHT_GUARD_RSI = 60
EXPLOSIVE_OVERBOUGHT_GUARD_BB  = 0.75
EXPLOSIVE_OVERSOLD_PENALTY     = 0.80

# [v3.7 P3] 청산 역방향 소프트 패널티
LIQ_REVERSE_PENALTY = 0.92

# [v3.6] 히든 다이버전스 최소 ADX
HIDDEN_DIV_MIN_ADX = 18

# ══════════════════════════════════════════════════════════════
# [v3.8 ㉒] RANGING 저ADX 임계값 동적 상향 (15분봉 노이즈 대응)
# ──────────────────────────────────────────────────────────────
# 15m RANGING 레짐은 ADX값과 무관하게 동일 임계(63pt)를 적용해왔음.
# ADX 15(노이즈성 횡보)와 ADX 24(약추세 횡보)를 동일 취급 → 보정.
# boost = min(CAP, int((THRESHOLD - adx) / DIVISOR))
#   ADX 18 → +1pt, ADX 16 → +2pt, ADX 14 → +4pt (최대 +4)
RANGING_LOW_ADX_THRESHOLD = 20      # 이 값 미만일 때 임계 상향 발동
RANGING_LOW_ADX_BOOST_CAP = 4       # 최대 부스트 pt
RANGING_LOW_ADX_DIVISOR   = 1.5     # 부스트 강도 (작을수록 강함)
RANGING_LOW_ADX_MAX_THRESHOLD = 67  # 부스트 후 임계 상한

# ══════════════════════════════════════════════════════════════
# [v3.8 ㉔] 거래량-가격 다이버전스 SQUEEZE 감액 배율
# ──────────────────────────────────────────────────────────────
# 기존: RANGING만 ×0.60, SQUEEZE는 ×1.0 (감액 없음)
# 변경: SQUEEZE도 방향 미결정 구간 → 캔들 패턴과 동일하게 ×0.50 감액
VPD_MULT_RANGING = 0.60
VPD_MULT_SQUEEZE = 0.50

# ══════════════════════════════════════════════════════════════
# SMC / 피보나치
# ══════════════════════════════════════════════════════════════
FIB_LOOKBACK      = 50
FIB_TOLERANCE     = 0.015
FIB_MIN_SWING_PCT = 0.03

VOL_DIV_PRICE_THRESHOLD   = 0.005
VOL_DIV_BULL_VOLUME_RATIO = 1.50
VOL_DIV_BEAR_VOLUME_RATIO = 0.67

MARKET_STRUCT_SWING_THRESHOLD = 0.005

# ══════════════════════════════════════════════════════════════
# 신호 임계값
# ══════════════════════════════════════════════════════════════
REGIME_THRESHOLDS = {
    "SQUEEZE":   66,
    "TRENDING":  64,
    "RANGING":   63,
    "EXPLOSIVE": 66,
}

# ══════════════════════════════════════════════════════════════
# 동적 쿨다운
# ══════════════════════════════════════════════════════════════
PRICE_MOVE_SUPPRESS_STRONG  = 0.05
PRICE_MOVE_SUPPRESS_MILD    = 0.03
PRICE_MOVE_RESET_THRESHOLD  = -0.025
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
