"""
config.py — 전역 설정 (v4.0) [TARGET: 15분봉 시그봇 / 15-MINUTE SIGBOT]
────────────────────────────────────────────────────────────────────
⚠️ 이 코드는 15분봉(15m entry) 시그봇 전용입니다. 1시간봉 버전과 혼동 금지.
   entry=15m / mid=1h / macro=4h — 모든 진입 판정 기준은 15분봉.
────────────────────────────────────────────────────────────────────
[v4.0 추가] ← 과적합 방지 전면 개선 (양방향 설계 강화)

P1. 근거필터 반전형 항목 확장 + 레짐별 최소 기준 분기
    ⑥ 멀티TF극단과매도/수, ⑦ 대규모청산꼬리, ⑧ BB극단+RSI다이버전스 추가
    SQUEEZE: 최소 1개, RANGING: 최소 2개, 기타: 최소 2개
    SIGNAL_MIN_EVIDENCE_RANGING = 2
    SIGNAL_MIN_EVIDENCE_SQUEEZE = 1
    SIGNAL_MIN_EVIDENCE_DEFAULT = 2

P2. ㉛ 필터 bos_same → bos_any 완화
    역방향 BOS라도 구조 확인 상태 → BOS패널티(×0.82)로 소프트 처리
    BOS 자체가 없는 구조 미확인 상태만 차단

P3. RANGING 임계 동적 상향 합산 상한 설정
    RANGING_THRESHOLD_DYNAMIC_CAP = 8  (최대 63+8=71pt)
    BB스퀴즈(+2)+저ADX(+4)+EMA역방향(+3) 중첩 합산 상한
    ADX역추세(EMA3역방향)는 별도 블록 — 독립 적용

P4. 거래량폭발 조건 재설계: ema_same < 3 → ema_same >= 1
    방향 근거 전무(ema_same=0) 시 미지급, 완전정렬(ema_same=3)도 허용

P5. ranging_bos_weak_penalty 통합: BOS_CONFLICT_PENALTY_RANGING 단일화
    기존 0.82×0.90 이중 패널티 → RANGING 전용값 0.76으로 통합
    soft_penalty FLOOR 제거 (이중 패널티 원인 해소)
    BOS_CONFLICT_PENALTY_RANGING = 0.76

P6. TRENDING/EXPLOSIVE LS 가중치 상한 0.18, Taker 상향
    TRENDING:  LS 0.22→0.18, Taker 0.34→0.38
    EXPLOSIVE: LS 0.24→0.18, Taker 0.38→0.44

P7. 보너스 단일 감산 원칙: 중첩 시 가장 강한 감산 1개만 적용
    BONUS_REDUCTION_SINGLE = True  (플래그, scoring_system에서 처리)

[v3.9] RANGING LS 가중치 삭감, BOS필터, EMA역방향 임계+3, soft_floor, 근거필터
[v3.8] LS 극단 임계값 강화, FVG 역방향 패널티, VPD SQUEEZE 감액,
       RANGING 저ADX 임계 상향, SQUEEZE 거래량폭발 EMA 조건, 눌림목 미세 강화
[v3.7] EXPLOSIVE 준과매도/과매수 역방향 패널티, 청산 역방향 소프트 패널티
[v3.6] 히든다이버전스 ADX 가드, SQUEEZE 캔들 감액
[v3.5] 거래량 baseline 1h / 4
[v3.4] EXPLOSIVE+BOS 강화 패널티, ADX 역추세, FVG 모호 차단
[v3.3] Volume 정규화, 거래량 페널티
[v3.2] BOS_CONFLICT_PENALTY = 0.82
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
# [v5.0] 헬퍼 함수로 통합: 16개 독립 값 → 4개 파라미터 세트
#   _ema_table(m3, m2, m1): 역방향 3/2/1개 시 배율, 0개는 항상 1.00
#   읽기 규칙: m3(가장 강한 페널티) > m2 > m1, 모두 ≤ 1.00
#   기존 16개 수치 전부 동일하게 유지 — 동작 변화 없음
def _ema_table(m3: float, m2: float, m1: float) -> dict:
    assert 0 < m3 <= m2 <= m1 <= 1.00, f"EMA 배율 단조성 위반: {m3}/{m2}/{m1}"
    return {0: 1.00, 1: m1, 2: m2, 3: m3}

#                          3역방향  2역방향  1역방향
EMA_MULTIPLIER           = _ema_table(0.52,   0.72,   0.88)  # TRENDING 기본값
EMA_MULTIPLIER_RANGING   = _ema_table(0.82,   0.90,   0.96)  # 횡보: 역추세 허용
EMA_MULTIPLIER_TRENDING  = _ema_table(0.52,   0.72,   0.88)  # 추세: 역추세 강 페널티
EMA_MULTIPLIER_EXPLOSIVE = _ema_table(0.75,   0.84,   0.93)  # 폭발: 중간 페널티
EMA_MULTIPLIER_SQUEEZE   = _ema_table(0.80,   0.87,   0.95)  # 스퀴즈: 방향 불확실, 완화

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

# [v3.9 ㉚] RANGING LS 가중치 삭감: 0.12→0.07, 삭감분 RSI·BB·Taker 분산
SCORE_WEIGHTS_RANGING = {
    "rsi":              0.34,   # 0.32→0.34
    "bollinger":        0.28,   # 0.26→0.28
    "funding_rate":     0.13,
    "long_short_ratio": 0.07,   # 0.12→0.07 ★
    "taker_volume":     0.11,   # 0.10→0.11
    "volume":           0.07,
}

# [v4.0 P6] LS 0.22→0.18, Taker 0.34→0.38 (방향 근거 직접성: Taker>LS)
SCORE_WEIGHTS_TRENDING = {
    "rsi":              0.11,
    "bollinger":        0.09,
    "funding_rate":     0.15,
    "long_short_ratio": 0.18,   # 0.22→0.18
    "taker_volume":     0.38,   # 0.34→0.38
    "volume":           0.09,
}

# [v4.0 P6] LS 0.24→0.18, Taker 0.38→0.44 (EXPLOSIVE: Taker가 핵심 방향 근거)
SCORE_WEIGHTS_EXPLOSIVE = {
    "rsi":              0.07,
    "bollinger":        0.06,
    "funding_rate":     0.15,
    "long_short_ratio": 0.18,   # 0.24→0.18
    "taker_volume":     0.44,   # 0.38→0.44
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
# [v5.0] Fuzzy 레짐 블렌딩 파라미터
# ──────────────────────────────────────────────────────────────
# ADX 경계(25pt) ±BLEND_WIDTH 구간에서 RANGING↔TRENDING 가중치 선형 블렌딩.
# confidence=0(경계 정중앙) 시 임계값 최대 +THRESHOLD_BOOST pt 상향.
REGIME_BLEND_WIDTH_ADX            = 5.0  # ADX ±5 = 20~30 구간에서 블렌딩
REGIME_BLEND_WIDTH_BW             = 0.10 # bw_ratio ±0.10 구간에서 TRENDING↔EXPLOSIVE 블렌딩
REGIME_CONFIDENCE_THRESHOLD_BOOST = 3    # 경계 중심(confidence=0)에서 최대 +3pt 임계 상향

# ══════════════════════════════════════════════════════════════
# [v5.0] RSI-BB 지표 융합 파라미터
# ──────────────────────────────────────────────────────────────
# ① Taker-조건부 RSI/BB 억제: Taker가 역방향으로 강할 때 포지션 기반 점수 약화.
# ② 기하평균 융합: RSI와 BB 두 지표 모두 강할 때만 높은 합산 허용.
# ③ Ensemble 투표: 6개 지표의 방향 합의 카운터로 보너스/패널티.
RSI_BB_TAKER_SUPPRESS_THRESHOLD = 0.60  # Taker 역방향 억제 시작 임계 (매도비율 ≥ 이 값)
RSI_BB_TAKER_SUPPRESS_MAX       = 0.50  # 억제 최소 계수 (50% 중립화 하한)
RSI_BB_INTERACT_THRESHOLD       = 68    # 교호작용 보너스 발동 기준 점수
RSI_BB_INTERACT_MAX             = 6     # 교호작용 보너스 최대값 (pt)

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
# [v4.0] 개선 파라미터 (과적합 방지)
# ══════════════════════════════════════════════════════════════

# P1. 유효 근거 최소 기준 — 레짐별 분기
# 항목: EMA≥2TF / BOS방향 / 히든Div / FVG일치 / 피보황금포켓
#       / 멀티TF극단과매도수 / 대규모청산꼬리 / BB극단+RSI다이버전스 (v4.0 확장)
SIGNAL_MIN_EVIDENCE_RANGING  = 2    # RANGING: 추세·반전 모두 2개
SIGNAL_MIN_EVIDENCE_SQUEEZE  = 1    # SQUEEZE: 방향 불확실 → 1개로 완화
SIGNAL_MIN_EVIDENCE_DEFAULT  = 2    # TRENDING/EXPLOSIVE/UNKNOWN

# P2. ㉛ BOS 필터 — bos_any 방식 (구조 확인만 요구)
RANGING_NO_BOS_EMA_REVERSE_MIN = 2  # EMA역방향 ≥ 이 TF 수 + BOS 자체 없음 → 차단

# P3. RANGING 임계 동적 상향 합산 상한
# BB스퀴즈+저ADX+EMA역방향 합산 최대 +8pt → 임계 최대 71pt
# ADX역추세(EMA3전방향)는 독립 블록 — 이 캡 미적용
RANGING_EMA_REVERSE_THRESHOLD_BOOST = 3   # EMA역방향≥2TF 시 +3pt
RANGING_THRESHOLD_DYNAMIC_CAP = 8         # BB스퀴즈+저ADX+EMA역방향 합산 상한

# P4. 거래량폭발 최소 EMA 일치 요구
VOLUME_EXPLOSION_MIN_EMA_SAME = 1   # ema_same >= 1 필요 (0이면 미지급)

# P5. RANGING BOS역방향 패널티 통합값
# 기존: BOS_CONFLICT_PENALTY(0.82) × ranging_bos_weak(0.90) = 0.738 이중 적용
# 개선: RANGING 전용 단일값 0.76 (0.82와 0.738 사이)
BOS_CONFLICT_PENALTY_RANGING = 0.76   # RANGING에서 BOS역방향 통합 단일 패널티

# P7. 보너스 단일 감산 원칙 플래그
BONUS_REDUCTION_SINGLE = True   # True: 중첩 감산 시 가장 강한 1개만 적용

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
