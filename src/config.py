"""
config.py — 전역 설정
[이번 변경]
- FVG / Hidden Divergence / BOS·CHoCH / 피보나치 파라미터 추가
- SQUEEZE OI 가중치 5%→10%, BB 33%→28% (방향성 예측 강화)
- 티어드 보너스 캡: 베이스 점수 구간별 보너스 상한 차등 적용
- CHoCH 역방향 패널티 추가
- 거래량다이버전스·시장구조 임계값 강화 (파라미터화)
"""
import os

OKX_API_KEY    = os.getenv("OKX_API_KEY",    "")
OKX_API_SECRET = os.getenv("OKX_API_SECRET", "")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "")

SYMBOLS: list = ["BTC/USDT", "ETH/USDT", "HYPE/USDT", "SOL/USDT", "XRP/USDT", "SUI/USDT"]

TIMEFRAMES    = {"entry": "15m", "mid": "1h", "macro": "4h"}
CANDLE_LIMITS = {"15m": 100, "1h": 210, "4h": 210}

RSI_PERIOD     = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD   = 30
BOLLINGER_PERIOD = 20
BOLLINGER_STD    = 2.0
ATR_PERIOD = 14
EMA_FAST = 9
EMA_SLOW = 21

EMA_MULTIPLIER = {3: 0.40, 2: 0.65, 1: 0.85, 0: 1.00}
EMA_MULTIPLIER_RANGING   = {3: 0.72, 2: 0.83, 1: 0.93, 0: 1.00}
EMA_MULTIPLIER_TRENDING  = {3: 0.40, 2: 0.65, 1: 0.85, 0: 1.00}
EMA_MULTIPLIER_EXPLOSIVE = {3: 0.75, 2: 0.84, 1: 0.93, 0: 1.00}
EMA_MULTIPLIER_SQUEEZE   = {3: 0.80, 2: 0.87, 1: 0.95, 0: 1.00}
REGIME_EMA_MULTIPLIERS = {
    "RANGING":   EMA_MULTIPLIER_RANGING,
    "TRENDING":  EMA_MULTIPLIER_TRENDING,
    "EXPLOSIVE": EMA_MULTIPLIER_EXPLOSIVE,
    "SQUEEZE":   EMA_MULTIPLIER_SQUEEZE,
    "UNKNOWN":   EMA_MULTIPLIER,
}

ADX_PERIOD=14; ADX_NO_TREND=20; ADX_WEAK_TREND=25; ADX_STRONG=50
VOLUME_CONFIRM_LOOKBACK=20; VOLUME_SPIKE_MULTIPLIER=1.5; VOLUME_STRONG_MULTIPLIER=2.5
FUNDING_LONG_STRONG=-0.0005; FUNDING_LONG_MILD=-0.0001
FUNDING_SHORT_MILD=0.0005;   FUNDING_SHORT_STRONG=0.001
LS_LONG_EXTREME=0.72;  LS_LONG_HIGH=0.65
LS_SHORT_EXTREME=0.62; LS_SHORT_HIGH=0.55
OI_CHANGE_STRONG=0.05; OI_CHANGE_MILD=0.02
TAKER_LOOKBACK=100; TAKER_STRONG_BUY=0.65; TAKER_STRONG_SELL=0.65
LIQ_LOOKBACK_MINUTES=60; LIQ_LARGE_THRESHOLD=500_000; LIQ_SIGNAL_THRESHOLD=1_000_000
REGIME_SQUEEZE_RATIO=0.70; REGIME_TREND_ADX=25; REGIME_STRONG_ADX=40

# ── 기본 가중치 (국면 불명확 시 fallback) ──────────────────────
SCORE_WEIGHTS = {
    "rsi":0.25,"bollinger":0.20,"funding_rate":0.17,
    "long_short_ratio":0.13,"taker_volume":0.13,"oi_change":0.07,"volume":0.05
}

# ── 국면별 가중치 ────────────────────────────────────────────
SCORE_WEIGHTS_RANGING = {
    "rsi":0.27,"bollinger":0.22,"funding_rate":0.14,
    "long_short_ratio":0.13,"taker_volume":0.11,"oi_change":0.05,"volume":0.08
}
SCORE_WEIGHTS_TRENDING = {
    "rsi":0.11,"bollinger":0.09,"funding_rate":0.15,
    "long_short_ratio":0.15,"taker_volume":0.21,"oi_change":0.18,"volume":0.11
}
SCORE_WEIGHTS_EXPLOSIVE = {
    "rsi":0.07,"bollinger":0.06,"funding_rate":0.15,
    "long_short_ratio":0.17,"taker_volume":0.23,"oi_change":0.19,"volume":0.13
}
# [조정] SQUEEZE: OI 5%→10% (스퀴즈 해소 방향 예측 강화), BB 33%→28%
SCORE_WEIGHTS_SQUEEZE = {
    "rsi":0.15,"bollinger":0.28,"funding_rate":0.13,
    "long_short_ratio":0.13,"taker_volume":0.15,"oi_change":0.10,"volume":0.06
}

REGIME_SCORE_WEIGHTS = {
    "RANGING":   SCORE_WEIGHTS_RANGING,
    "TRENDING":  SCORE_WEIGHTS_TRENDING,
    "EXPLOSIVE": SCORE_WEIGHTS_EXPLOSIVE,
    "SQUEEZE":   SCORE_WEIGHTS_SQUEEZE,
    "UNKNOWN":   SCORE_WEIGHTS,
}

# ── 기존 보너스 ──────────────────────────────────────────────
BONUS_BB_RSI_ALIGN=10;      BONUS_FUNDING_LS_ALIGN=8;   BONUS_OI_TAKER_CONFIRM=7
BONUS_LIQUIDATION=12;       BONUS_ADX_STRONG=5;          BONUS_RSI_DIVERGENCE=8
BONUS_TREND_STRONG=15;      BONUS_TREND_VOLUME=10;       BONUS_BAND_WALKING=8
BONUS_PULLBACK_ENTRY=14;    BONUS_PULLBACK_ENTRY_WEAK=9; BONUS_PULLBACK_ENTRY_MICRO=5
BONUS_VOLUME_EXPLOSION=7;   BONUS_POST_SQUEEZE=10
BONUS_TREND_REVERSAL_WARNING=18; BONUS_LS_DIRECTION_CONFIRM=8

REVERSAL_RSI_1H_OB=72;  REVERSAL_RSI_1H_OS=28
REVERSAL_TAKER_SELL=0.55; REVERSAL_TAKER_BUY=0.55
REVERSAL_FUNDING_POS=0.0005; REVERSAL_FUNDING_NEG=-0.0005

BONUS_CANDLE_PIN_BAR=14;    BONUS_CANDLE_ENGULFING=12;  BONUS_CANDLE_CONSECUTIVE=8
BONUS_MARKET_STRUCT_TREND=10; BONUS_FAILED_BREAKOUT=16; BONUS_VOL_PRICE_DIV=14
BONUS_FUNDING_EXTREME=12;   BONUS_ATR_MOMENTUM=8
FUNDING_EXTREME_SHORT=0.0010; FUNDING_EXTREME_LONG=-0.0010
ATR_MOMENTUM_RATIO = 1.8   # ATR > 평균 1.8배 시 모멘텀 보너스 발동

BONUS_BB_RANGING_REVERSAL=8

CANDLE_MOMENTUM_PENALTY_RANGING=0.75
CANDLE_MOMENTUM_PENALTY_EXPLOSIVE=0.80
CANDLE_MOMENTUM_PENALTY_TRENDING=0.87

OI_SPIKE_THRESHOLD=0.80; OI_SPIKE_SCORE_PENALTY=20

# ══════════════════════════════════════════════════════════════
# [신규] SMC / 피보나치 / 히든다이버전스 보너스
# ══════════════════════════════════════════════════════════════

# FVG (Fair Value Gap) — 기관 미체결 주문 구간 진입
BONUS_FVG_ENTRY = 10

# Hidden Divergence — 추세 지속 확증 (일반 다이버전스의 반전 신호와 구분)
BONUS_HIDDEN_DIVERGENCE = 8

# BOS (Break of Structure) — 구조적 추세 지속 확증
BONUS_BOS_CONFIRM = 8

# 피보나치 황금 포켓 (61.8~65%) — 가장 강력한 반전 구간
BONUS_FIB_GOLDEN_POCKET = 12

# 피보나치 주요 레벨 (38.2%, 50%, 78.6%) 근접
BONUS_FIB_KEY_LEVEL = 6

# CHoCH (Change of Character) — 추세 전환 경고 패널티
# 신호 방향과 CHoCH 방향이 반대일 때 전체 점수에 적용
CHOCH_AGAINST_PENALTY = 0.88

# ── 피보나치 설정 ────────────────────────────────────────────
FIB_LOOKBACK  = 50     # 스윙 고점/저점 탐색 lookback (캔들 수)
FIB_TOLERANCE = 0.015  # 주요 레벨 ±1.5% 허용 범위
FIB_MIN_SWING_PCT = 0.03  # 의미 있는 스윙 최소 크기 (가격 대비 3%)

# ── 거래량다이버전스 임계값 (강화) ─────────────────────────────
# 기존: 0.3% 가격 이동 + 30% 거래량 차이 → 너무 민감
# 변경: 0.5% + 50% → 노이즈 차단
VOL_DIV_PRICE_THRESHOLD = 0.005   # 신고/저가 기준: ±0.5%
VOL_DIV_BULL_VOLUME_RATIO = 1.50  # 신저가 시 거래량 1.5배 이상 (불리시)
VOL_DIV_BEAR_VOLUME_RATIO = 0.67  # 신고가 시 거래량 0.67배 이하 (베어리시)

# ── 시장구조 스윙 임계값 (강화) ─────────────────────────────
# 기존: 0.2% → 크립토 노이즈에 너무 민감
# 변경: 0.5% → 의미 있는 구조만 인식
MARKET_STRUCT_SWING_THRESHOLD = 0.005  # Higher Low / Lower High 최소 변화율 0.5%

# ══════════════════════════════════════════════════════════════
# [신규] 티어드 보너스 캡 (베이스 점수 기반 차등)
# ══════════════════════════════════════════════════════════════
# 문제: 모든 지표 중립(베이스 ~33pt)에서도 보너스 35pt 풀 적용 가능
#       → 지표 품질 없이 보너스만으로 임계값 통과
# 해결: 베이스가 낮을수록 보너스 상한도 낮춤 → 품질 보장
#
# base < 38pt:  보너스 최대 20pt (중립 시장, 보너스로 구제 불가)
# 38 ≤ base < 43pt: 보너스 최대 28pt (약한 방향성)
# base ≥ 43pt: 보너스 최대 35pt (명확한 방향성 — 정상)
BONUS_CAP_TIERS = [(38, 20), (43, 28), (9999, 35)]

# ── 기존 임계값 / 동적쿨다운 / MTF RSI / EXPLOSIVE ──────────
REGIME_THRESHOLDS = {
    "SQUEEZE":   65,
    "TRENDING":  60,
    "RANGING":   61,
    "EXPLOSIVE": 56,
}
SIGNAL_MIN_SCORE = 63

PRICE_MOVE_SUPPRESS_STRONG=0.05; PRICE_MOVE_SUPPRESS_MILD=0.03
PRICE_MOVE_RESET_THRESHOLD=-0.01
COOLDOWN_SUPPRESSED_STRONG=120; COOLDOWN_SUPPRESSED_MILD=75

MTF_RSI_OVERBOUGHT_1H=72; MTF_RSI_OVERBOUGHT_1H_MILD=68; MTF_RSI_OVERBOUGHT_4H=65
MTF_RSI_OVERSOLD_1H=28;   MTF_RSI_OVERSOLD_1H_MILD=32;  MTF_RSI_OVERSOLD_4H=35
MTF_RSI_PENALTY_STRONG=0.85; MTF_RSI_PENALTY_MILD=0.92
MTF_RSI_OVERSOLD_1H_EXTREME=24; MTF_RSI_OVERBOUGHT_1H_EXTREME=76

EXPLOSIVE_EXHAUSTION_RSI_LONG=70;  EXPLOSIVE_EXHAUSTION_RSI_SHORT=30
EXPLOSIVE_EXHAUSTION_PENALTY=0.88

MAX_RETRIES=3; RETRY_DELAY_S=5; SIGNAL_COOLDOWN_MINUTES=60
SIGNAL_STATE_FILE="/tmp/bot_state/signal_state.json"
ORDERBOOK_DEPTH=20; LOG_LEVEL="INFO"; LOG_FILE="logs/bot.log"
