"""
config.py — 전역 설정 (A·E·F·H 개선판 + 동적쿨다운·BB반전·MTF패널티)
신규: A-동적쿨다운 / B-BB반전 / C-MTF RSI패널티 / D-EXPLOSIVE소진체크
"""
import os

# ── API 키: 환경변수에서 읽음 (절대 직접 입력 금지) ──────────────
# GitHub Actions: Settings → Secrets → GITHUB_ACTIONS_SECRET에 등록
# 로컬 실행:      .env 파일 또는 export 명령어로 설정
OKX_API_KEY    = os.getenv("OKX_API_KEY",    "")
OKX_API_SECRET = os.getenv("OKX_API_SECRET", "")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "")

SYMBOLS: list = ["BTC/USDT", "ETH/USDT", "HYPE/USDT"]

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
    "RANGING": EMA_MULTIPLIER_RANGING, "TRENDING": EMA_MULTIPLIER_TRENDING,
    "EXPLOSIVE": EMA_MULTIPLIER_EXPLOSIVE, "SQUEEZE": EMA_MULTIPLIER_SQUEEZE,
    "UNKNOWN": EMA_MULTIPLIER,
}

ADX_PERIOD=14; ADX_NO_TREND=20; ADX_WEAK_TREND=25; ADX_STRONG=50
VOLUME_CONFIRM_LOOKBACK=20; VOLUME_SPIKE_MULTIPLIER=1.5; VOLUME_STRONG_MULTIPLIER=2.5
FUNDING_LONG_STRONG=-0.0005; FUNDING_LONG_MILD=-0.0001
FUNDING_SHORT_MILD=0.0005; FUNDING_SHORT_STRONG=0.001
LS_LONG_EXTREME=0.72; LS_LONG_HIGH=0.65; LS_SHORT_EXTREME=0.62; LS_SHORT_HIGH=0.55
OI_CHANGE_STRONG=0.05; OI_CHANGE_MILD=0.02
TAKER_LOOKBACK=100; TAKER_STRONG_BUY=0.65; TAKER_STRONG_SELL=0.65
LIQ_LOOKBACK_MINUTES=60; LIQ_LARGE_THRESHOLD=500_000; LIQ_SIGNAL_THRESHOLD=1_000_000
REGIME_SQUEEZE_RATIO=0.70; REGIME_TREND_ADX=25; REGIME_STRONG_ADX=40

SCORE_WEIGHTS = {"rsi":0.25,"bollinger":0.20,"funding_rate":0.17,"long_short_ratio":0.13,"taker_volume":0.13,"oi_change":0.07,"volume":0.05}
SCORE_WEIGHTS_RANGING   = {"rsi":0.27,"bollinger":0.22,"funding_rate":0.14,"long_short_ratio":0.13,"taker_volume":0.11,"oi_change":0.05,"volume":0.08}
SCORE_WEIGHTS_TRENDING  = {"rsi":0.11,"bollinger":0.09,"funding_rate":0.15,"long_short_ratio":0.15,"taker_volume":0.21,"oi_change":0.18,"volume":0.11}
SCORE_WEIGHTS_EXPLOSIVE = {"rsi":0.07,"bollinger":0.06,"funding_rate":0.15,"long_short_ratio":0.17,"taker_volume":0.23,"oi_change":0.19,"volume":0.13}
SCORE_WEIGHTS_SQUEEZE   = {"rsi":0.15,"bollinger":0.33,"funding_rate":0.13,"long_short_ratio":0.13,"taker_volume":0.15,"oi_change":0.05,"volume":0.06}
REGIME_SCORE_WEIGHTS = {"RANGING":SCORE_WEIGHTS_RANGING,"TRENDING":SCORE_WEIGHTS_TRENDING,"EXPLOSIVE":SCORE_WEIGHTS_EXPLOSIVE,"SQUEEZE":SCORE_WEIGHTS_SQUEEZE,"UNKNOWN":SCORE_WEIGHTS}

BONUS_BB_RSI_ALIGN=10; BONUS_FUNDING_LS_ALIGN=8; BONUS_OI_TAKER_CONFIRM=7
BONUS_LIQUIDATION=12; BONUS_ADX_STRONG=5; BONUS_RSI_DIVERGENCE=8
BONUS_TREND_STRONG=15; BONUS_TREND_VOLUME=10; BONUS_BAND_WALKING=8
BONUS_PULLBACK_ENTRY=14; BONUS_PULLBACK_ENTRY_WEAK=9; BONUS_PULLBACK_ENTRY_MICRO=5
BONUS_VOLUME_EXPLOSION=7; BONUS_POST_SQUEEZE=10
BONUS_TREND_REVERSAL_WARNING = 18
BONUS_LS_DIRECTION_CONFIRM   =  8
REVERSAL_RSI_1H_OB   = 72
REVERSAL_RSI_1H_OS   = 28
REVERSAL_TAKER_SELL  = 0.55
REVERSAL_TAKER_BUY   = 0.55
REVERSAL_FUNDING_POS =  0.0005
REVERSAL_FUNDING_NEG = -0.0005
# ══════════════════════════════════════════════════════
# 트레이더 업그레이드 — 신규 보너스/파라미터
# ══════════════════════════════════════════════════════

# 캔들 패턴 보너스 (롱/숏 대칭)
BONUS_CANDLE_PIN_BAR        = 14  # 핀바 (강력한 반전 신호)
BONUS_CANDLE_ENGULFING      = 12  # 인걸핑 캔들
BONUS_CANDLE_CONSECUTIVE    =  8  # 연속 3캔들 (방향 모멘텀)

# 시장 구조 보너스 (롱/숏 대칭)
BONUS_MARKET_STRUCT_TREND   = 10  # Lower High / Higher Low 구조 확인
BONUS_FAILED_BREAKOUT       = 16  # 돌파 실패 (매우 강력한 반전 신호)

# 거래량-가격 다이버전스 보너스 (롱/숏 대칭)
BONUS_VOL_PRICE_DIV         = 14  # 거래량-가격 다이버전스

# 펀딩비 극단 보너스 (롱/숏 대칭)
BONUS_FUNDING_EXTREME       = 12  # 펀딩 극단값 (0.1%+/-) — 레버리지 과열/공포
FUNDING_EXTREME_SHORT = 0.0010   # 0.10%+ → 숏 극단 보너스
FUNDING_EXTREME_LONG  = -0.0010  # -0.10%- → 롱 극단 보너스

# ATR 모멘텀 보너스
BONUS_ATR_MOMENTUM          =  8  # ATR 급등 (변동성 방향 확장)
ATR_MOMENTUM_RATIO          = 1.8  # ATR > 평균 1.8배 시 발동

# ── 연속캔들 모멘텀 역방향 페널티 (개선안 3) ──────────────────
# 신호 방향과 현재 캔들 모멘텀이 반대일 때 (타이밍 불량 페널티)
# 롱/숏 완전 대칭, 국면별 차등:
#   TRENDING  : 약 페널티 — 추세 중 반등/눌림은 자연스러운 현상
#   EXPLOSIVE : 중 페널티 — 급등락 중 반대 캔들 위험
#   RANGING/기타: 강 페널티 — 방향 불명확 구간에서 모멘텀 역행
# 단, 롱+연속음봉 시 BB 하단 이탈 구간은 면제 (낙폭과대 반전 신호)
CANDLE_MOMENTUM_PENALTY_RANGING   = 0.75
CANDLE_MOMENTUM_PENALTY_EXPLOSIVE = 0.80
CANDLE_MOMENTUM_PENALTY_TRENDING  = 0.87

OI_SPIKE_THRESHOLD=0.80; OI_SPIKE_SCORE_PENALTY=20

REGIME_THRESHOLDS = {
    "SQUEEZE":   65,   # 유지 — 방향 불명확 구간
    "TRENDING":  60,   # 유지 — 추세 신호 억제 최소화
    "RANGING":   61,   # 58 → 61: 과잉 발화 억제 (63에서 소폭 완화)
    "EXPLOSIVE": 56,   # 55 → 56: 소폭 상향만
}

# B안: WATCH 제거용 절대 하한선 — 국면 무관하게 이 점수 미만은 발화 차단
SIGNAL_MIN_SCORE = 63

# ── A: 가격 변화율 기반 동적 쿨다운 (롱/숏 방향 중립) ──
PRICE_MOVE_SUPPRESS_STRONG  = 0.05   # 5%+ 방향 이동 → 120분
PRICE_MOVE_SUPPRESS_MILD    = 0.03   # 3%+ 방향 이동 → 75분
PRICE_MOVE_RESET_THRESHOLD  = -0.01  # -1% 역방향 → 쿨다운 리셋
COOLDOWN_SUPPRESSED_STRONG  = 120
COOLDOWN_SUPPRESSED_MILD    = 75

# ── B: RANGING BB 극단 반전 보너스 (롱/숏 대칭) ──
BONUS_BB_RANGING_REVERSAL   = 8

# ── C: MTF RSI 극단값 패널티 (롱/숏 대칭) ──
MTF_RSI_OVERBOUGHT_1H      = 72
MTF_RSI_OVERBOUGHT_1H_MILD = 68
MTF_RSI_OVERBOUGHT_4H      = 65
MTF_RSI_OVERSOLD_1H        = 28
MTF_RSI_OVERSOLD_1H_MILD   = 32
MTF_RSI_OVERSOLD_4H        = 35
MTF_RSI_PENALTY_STRONG     = 0.85
# 극단 RSI 단독 강패널티 기준 (4h 조건 없이 1h만으로 STRONG 발동)
MTF_RSI_OVERSOLD_1H_EXTREME  = 24   # 1h RSI ≤ 24 단독 → 숏 STRONG 패널티
MTF_RSI_OVERBOUGHT_1H_EXTREME = 76  # 1h RSI ≥ 76 단독 → 롱 STRONG 패널티
MTF_RSI_PENALTY_MILD       = 0.92

# ── D-alt: EXPLOSIVE 모멘텀 소진 체크 (롱/숏 대칭) ──
EXPLOSIVE_EXHAUSTION_RSI_LONG  = 70
EXPLOSIVE_EXHAUSTION_RSI_SHORT = 30
EXPLOSIVE_EXHAUSTION_PENALTY   = 0.88

MAX_RETRIES=3; RETRY_DELAY_S=5; SIGNAL_COOLDOWN_MINUTES=60
SIGNAL_STATE_FILE="/tmp/bot_state/signal_state.json"
ORDERBOOK_DEPTH=20; LOG_LEVEL="INFO"; LOG_FILE="logs/bot.log"
