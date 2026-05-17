"""
config.py — 전역 설정 (v3.3)
────────────────────────────────────────────────────────────────────
[v3.3 개선]

① SIGNAL_MIN_SCORE 제거
   기존: regime_threshold(58/60/62/63)와 SIGNAL_MIN_SCORE(63)의 AND 조건
         → 실질 max(regime,63) = 모든 국면 63pt, 국면별 임계값 무력화
         → 알림 메시지 "임계:58pt"가 실제 동작과 달라 투명성 훼손
   수정: SIGNAL_MIN_SCORE 완전 제거, regime_threshold 단독 사용
   신규 임계값: SQUEEZE 65 / EXPLOSIVE 65 / TRENDING 63 / RANGING 62
   설계 원칙: 리스크 ∝ 임계값
     SQUEEZE/EXPLOSIVE → 65pt (페이크아웃/고변동성)
     TRENDING          → 63pt (구조 확인 수준)
     RANGING           → 62pt (BOS 패널티가 노이즈 필터링)

② Volume 가중치 재조정
   배경: analysis_engine에서 volume 스코어 1.0x=50pt 정규화 후
         평균 거래량이 ~50pt를 기여 → 기존보다 평균 +3~5pt 상승 예상
   대응: volume 가중치 하향, 핵심 지표(taker/rsi/bb)에 재분배
   RANGING:   vol 0.11→0.07  rsi +0.02  bb +0.02
   TRENDING:  vol 0.14→0.09  taker +0.03  ls +0.02
   EXPLOSIVE: vol 0.17→0.10  taker +0.05  ls +0.02
   SQUEEZE:   vol 0.08→0.05  bb +0.02  taker +0.01
   DEFAULT:   vol 0.05→0.04  taker +0.01

③ 보너스 밸런스
   FAILED_BREAKOUT: 14→12pt (단독 최고값 해소, PULLBACK_ENTRY와 동일)
   BOS_CONFIRM:      6→ 8pt (구조 확증 중요도 반영)

④ Gate 단일 패널티 추가 (GATE_PENALTY_SINGLE = 0.92)
   기존: 펀딩비 AND 롱숏 둘 다 불리 → ×0.80
   수정: 하나만 불리 → ×0.92(mild)  /  둘 다 불리 → ×0.80(강)

[v3.2] BOS_CONFLICT_PENALTY = 0.82, PRICE_MOVE_RESET_THRESHOLD = -0.025
[v3.1] OI 완전 제거
[v3]   ADX 배율 통합, EMA 배율 정비, 보너스 18개 정리
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

VOLUME_CONFIRM_LOOKBACK   = 20
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
# 국면별 가중치 — v3.3: volume 가중치 하향 + 핵심 지표 재분배
# ══════════════════════════════════════════════════════════════

# DEFAULT
SCORE_WEIGHTS = {
    "rsi":              0.25,
    "bollinger":        0.20,
    "funding_rate":     0.19,
    "long_short_ratio": 0.14,
    "taker_volume":     0.18,  # +0.01
    "volume":           0.04,  # -0.01
}

# RANGING: 평균 회귀 — RSI/BB 핵심, vol 보조
SCORE_WEIGHTS_RANGING = {
    "rsi":              0.32,  # +0.02
    "bollinger":        0.26,  # +0.02
    "funding_rate":     0.13,
    "long_short_ratio": 0.12,
    "taker_volume":     0.10,
    "volume":           0.07,  # -0.04
}

# TRENDING: 추세 추종 — order flow(Taker/LS) 최우선
SCORE_WEIGHTS_TRENDING = {
    "rsi":              0.11,
    "bollinger":        0.09,
    "funding_rate":     0.15,
    "long_short_ratio": 0.22,  # +0.02
    "taker_volume":     0.34,  # +0.03
    "volume":           0.09,  # -0.05
}

# EXPLOSIVE: 폭발적 움직임 — 실시간 order flow 최우선
SCORE_WEIGHTS_EXPLOSIVE = {
    "rsi":              0.07,
    "bollinger":        0.06,
    "funding_rate":     0.15,
    "long_short_ratio": 0.24,  # +0.02
    "taker_volume":     0.38,  # +0.05
    "volume":           0.10,  # -0.07
}

# SQUEEZE: 스퀴즈 돌파 — BB 최우선, taker 방향 확인
SCORE_WEIGHTS_SQUEEZE = {
    "rsi":              0.15,
    "bollinger":        0.35,  # +0.02
    "funding_rate":     0.13,
    "long_short_ratio": 0.13,
    "taker_volume":     0.19,  # +0.01
    "volume":           0.05,  # -0.03
}

REGIME_SCORE_WEIGHTS = {
    "RANGING":   SCORE_WEIGHTS_RANGING,
    "TRENDING":  SCORE_WEIGHTS_TRENDING,
    "EXPLOSIVE": SCORE_WEIGHTS_EXPLOSIVE,
    "SQUEEZE":   SCORE_WEIGHTS_SQUEEZE,
    "UNKNOWN":   SCORE_WEIGHTS,
}

# ══════════════════════════════════════════════════════════════
# 보너스 체계 (v3.3)
# ══════════════════════════════════════════════════════════════

BONUS_PULLBACK_ENTRY       = 12
BONUS_PULLBACK_ENTRY_WEAK  = 8
BONUS_PULLBACK_ENTRY_MICRO = 4

BONUS_TREND_STRONG = 12

BONUS_BB_RSI_ALIGN         = 8
BONUS_LIQUIDATION          = 10
BONUS_VOL_PRICE_DIV        = 10
BONUS_FAILED_BREAKOUT      = 12   # [v3.3] 14→12
BONUS_EXTREME_OVERSOLD_MTF = 10

BONUS_FVG_ENTRY            = 8
BONUS_FVG_ENTRY_CONFLICTED = 4
BONUS_BOS_CONFIRM          = 8    # [v3.3]  6→8
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

CHOCH_AGAINST_PENALTY = 0.88   # CHoCH: 추세 전환 "경고"
BOS_CONFLICT_PENALTY  = 0.82   # BOS:   추세 방향 "확증" — CHoCH보다 강함

CANDLE_MOMENTUM_PENALTY_RANGING   = 0.80
CANDLE_MOMENTUM_PENALTY_EXPLOSIVE  = 0.85
CANDLE_MOMENTUM_PENALTY_TRENDING   = 0.90

# Gate 패널티 [v3.3]
GATE_PENALTY_SINGLE = 0.92   # 펀딩비 OR 롱숏 하나만 불리
GATE_PENALTY_DUAL   = 0.80   # 둘 다 불리 (기존 유일 패널티)

OI_SPIKE_THRESHOLD     = 0.80  # 하위 호환용
OI_SPIKE_SCORE_PENALTY = 20

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
# 신호 임계값 (v3.3: SIGNAL_MIN_SCORE 제거, 국면별 단독 운용)
# ══════════════════════════════════════════════════════════════
REGIME_THRESHOLDS = {
    "SQUEEZE":   65,
    "TRENDING":  63,
    "RANGING":   62,
    "EXPLOSIVE": 65,
}
# SIGNAL_MIN_SCORE 제거됨 (v3.3) — regime_threshold 단독 사용

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
