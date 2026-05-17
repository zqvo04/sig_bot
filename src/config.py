"""
config.py — 전역 설정 (v3.3)
────────────────────────────────────────────────────────────────────
[v3.3 개선]

① SIGNAL_MIN_SCORE 제거
   기존: regime_threshold와 SIGNAL_MIN_SCORE(63)의 AND 조건
         → 실질 max(regime,63), 국면별 임계값 무력화
   수정: SIGNAL_MIN_SCORE 완전 제거, regime_threshold 단독 사용
   신규 임계값: SQUEEZE 65 / EXPLOSIVE 65 / TRENDING 63 / RANGING 62

② Volume 가중치 재조정
   volume 가중치 하향, 핵심 지표(taker/rsi/bb)에 재분배
   RANGING: vol 0.11→0.07 / TRENDING: vol 0.14→0.09
   EXPLOSIVE: vol 0.17→0.10 / SQUEEZE: vol 0.08→0.05

③ 보너스 밸런스
   FAILED_BREAKOUT: 14→12pt / BOS_CONFIRM: 6→8pt

④ Gate 단일 패널티 추가 (GATE_PENALTY_SINGLE = 0.92)
   하나만 불리 → ×0.92 / 둘 다 불리 → ×0.80

⑤ 거래량 페널티 추가 (v3.3 patch)
   volume 가중치 5~9%로 낮아 보너스 하나로 상쇄 가능.
   vol score 기준 명시적 덧셈 페널티 추가:
     score <  5pt (ratio <  10%) → -7pt
     score < 15pt (ratio <  30%) → -3pt
     score ≥ 15pt                →  0pt

⑥ Volume 기준 캔들 및 lookback 개선 (v3.3 patch)
   문제 ①: 신호는 항상 새 15분봉 시작 시점 → iloc[-1]은 방금 열린 캔들
            (거래량 ≈ 0) → ratio가 구조적으로 낮게 산출되는 버그
   문제 ②: lookback 20개(5시간)는 스파이크 오염 취약 + 주말/평일 패턴 미반영
   수정:
     - 비교 기준: iloc[-1](진행 중) → iloc[-2](직전 완성 캔들)
     - lookback: 20 → 48 (5시간 → 12시간)
     - CANDLE_LIMITS["15m"]=100 기준 버퍼 50개 확보 → 안전

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

# [v3.3 patch ⑥] 20(5시간) → 48(12시간)
# 비교 기준: 직전 완성 캔들(iloc[-2]) vs 직전 48개 완성 캔들 평균(iloc[-50:-2])
# CANDLE_LIMITS["15m"]=100 → 버퍼 50개 확보
VOLUME_CONFIRM_LOOKBACK   = 48
VOLUME_SPIKE_MULTIPLIER   = 1.5   # confirmed 기준 → 점수 70pt+
VOLUME_STRONG_MULTIPLIER  = 2.5   # strong 기준    → 점수 90pt+

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

LS_LONG_EXTREME  = 0.72
LS_LONG_HIGH     = 0.65
LS_SHORT_EXTREME = 0.62
LS_SHORT_HIGH    = 0.55

OI_CHANGE_STRONG = 0.05  # 하위 호환용
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
BONUS_FVG_ENTRY_CONFLICTED = 4
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

# Gate 패널티
GATE_PENALTY_SINGLE = 0.92
GATE_PENALTY_DUAL   = 0.80

OI_SPIKE_THRESHOLD     = 0.80  # 하위 호환용
OI_SPIKE_SCORE_PENALTY = 20

# 거래량 페널티 [v3.3 patch ⑤]
VOLUME_PENALTY_LOW_THRESHOLD = 5    # score <  5pt → -7pt
VOLUME_PENALTY_MID_THRESHOLD = 15   # score < 15pt → -3pt
VOLUME_PENALTY_LOW = -7
VOLUME_PENALTY_MID = -3

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
    "SQUEEZE":   65,
    "TRENDING":  63,
    "RANGING":   62,
    "EXPLOSIVE": 65,
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