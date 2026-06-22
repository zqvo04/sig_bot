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
T_MAX          = int(os.getenv("ORTHO_T_MAX", 8))             # #11 보유한도(15m봉×8=2h) → 종가 채점
MAX_POS_DIR    = int(os.getenv("ORTHO_MAX_POS_DIR", 2))       # #12 방향별 동시 슬롯(심볼별)

# ══════════════════════════════════════════════════════════════════
# 리스크·집행 레이어 (★ 진입 결정과 분리 — 엔트리 과적합 표면이 아님 ★)
# ──────────────────────────────────────────────────────────────────
#   위 12개는 "어떤 신호를 낼지"(진입 셋) → 곡선맞춤 위험의 본체.
#   아래는 같은 신호를 "어떻게 사이징·청산·기록·게이팅할지"(집행)만 바꾼다.
#   진입 셋을 일절 건드리지 않고, 전부 자기정규화(R·비율)라 과적합 표면을 늘리지 않음.
# ══════════════════════════════════════════════════════════════════
# C-1 등가-R 사이징: 거래당 고정 위험(USDT). SL 도달 = 정확히 이 금액 손실 → 모든 신호 동일 R.
RISK_PER_TRADE     = float(os.getenv("ORTHO_RISK_PER_TRADE", 100))
# A-1 본전스톱: +BE_TRIGGER_R 도달 시 손절을 진입가(+BE_LOCK_R)로 이동. 0=비활성.
#     BE_LOCK_R 은 수수료/슬리피지 버퍼 겸 WIN/LOSS 이분채점의 0-PnL 모호성 제거용.
BE_TRIGGER_R       = float(os.getenv("ORTHO_BE_TRIGGER_R", 1.0))
BE_LOCK_R          = float(os.getenv("ORTHO_BE_LOCK_R", 0.05))
# A-3 포트폴리오 방향 노출 캡: 전 심볼 통틀어 동시 OPEN 동일방향 한도(상관 바스켓 상한).
#     크립토는 BTC에 ~0.8+ 상관 → 동시 5숏 = 사실상 한 포지션 5배. 큰 값=사실상 무제한.
MAX_CONCURRENT_DIR = int(os.getenv("ORTHO_MAX_CONCURRENT_DIR", 3))
# A-4 TP 상한(RR): 타임스톱(T_MAX봉=2h) 안에 닿을 거리로 먼 목표를 당겨 TP·청산을 정합. 0=비활성.
RR_MAX             = float(os.getenv("ORTHO_RR_MAX", 3.0))

# ══════════════════════════════════════════════════════════════════
# 레짐 라우터 + 도달가능 TP (R1 기본 ON · R2 기본 OFF, 단일변수 A/B)
# ──────────────────────────────────────────────────────────────────
#   R1: 국면(RANGE/TREND/EXPANSION)을 2축(추세효율 ER × 변동성)으로 판정해 맞는 폴라리티만 평가.
#       추세장 역행·혼탁구조 진입(데이터 −41R)을 구조 차단. 롱숏 대칭 불변. ★ 기본 ON.
#   R2: 명목 RR≠실현 R(캡처 52%)의 본체 — TP를 ATR·√T_MAX 도달거리로 상한.
#   둘 다 백분위·ATR 자기정규화 → 곡선맞춤 아님. R2는 켤 때 70/30 워크포워드로 단일검증.
# ══════════════════════════════════════════════════════════════════
REGIME_ROUTER = _flag("ORTHO_REGIME_ROUTER", "true")         # R1 라우터 ON/OFF (★ 기본 ON)
VOL_HI        = float(os.getenv("ORTHO_VOL_HI", 70))         # R1 확장 레짐 변동성 백분위 컷
TREND_ER      = float(os.getenv("ORTHO_TREND_ER", 0.4))      # R1 추세효율(ER) 레벨 컷(0~1, 스케일프리) — 조기 TREND 승격
# R1-S SOFT 라우터(L2): STRICT(기본)=레짐당 폴라리티 1개(현행). SOFT=레짐 경계 모호구간에서만 양폴라리티 평가.
#   경계 깜빡임(ER≈TREND_ER 진동)으로 "경계 반대편" 셋업을 놓치는 누락을 회수. 기본 STRICT라 현행 보존.
#   확장은 ER 모호구간(|ER−TREND_ER|≤ROUTER_SOFT_ER)에서만 → 명확한 추세/레인지는 그대로 단일폴라리티.
ROUTER_MODE    = os.getenv("ORTHO_ROUTER_MODE", "STRICT").strip().upper()  # STRICT | SOFT
ROUTER_SOFT_ER = float(os.getenv("ORTHO_ROUTER_SOFT_ER", 0.1))            # SOFT 모호구간 폭(ER 레벨)
# R2 도달가능 TP 계수: TP거리 ≤ K·ATR·√T_MAX. 0=비활성. 권장 첫 검증값 ≈ 1.2.
TP_REACH_K    = float(os.getenv("ORTHO_TP_REACH_K", 0))
# R4 BREAKOUT(EXPANSION 전용): 거래량 서지 백분위 컷(절대 150% 대신 자기분포 백분위). 라우터 ON일 때만 가동.
P_VOL         = float(os.getenv("ORTHO_P_VOL", 70))
# R5 추격 방지: CONT 진입가-빠른EMA 이격 한도(ATR 배수, 절대% 대신). 0=비활성, 권장 ≈ 1.0.
CHASE_K       = float(os.getenv("ORTHO_CHASE_K", 0))
# R6 상관 디둡: 동일 실행 내 후보를 품질(RR) 우선 정렬 후 방향 캡 적용(그리디→최선순). 기본 OFF.
CORR_DEDUP    = _flag("ORTHO_CORR_DEDUP", "false")
# L3 EXPANSION 돌파 부활: ON 시 BREAKOUT 트리거에 '신선 W_F 신고/신저 레인지 돌파'를 OR로 추가
#   (VWAP 재탈환만으로는 18h 앵커라 거의 안 켜짐 → 고변동 국면 신호 ~0). 게이트(vol서지·flow·L과열)는 동일.
BREAKOUT_RANGE = _flag("ORTHO_BREAKOUT_RANGE", "false")
# L4 흐름축 보강: taker CVD(이미 수집)를 flow 확인 OR로 재사용 → 캔들프록시 F가 늦을 때 늦은-흐름 회수.
#   동조 강도 임계(0.5=중립). 기본 OFF=현행(캔들프록시 단독). 신규 데이터 의존 0.
FLOW_TAKER_CONFIRM = _flag("ORTHO_FLOW_TAKER_CONFIRM", "false")
FLOW_TAKER_MIN     = float(os.getenv("ORTHO_FLOW_TAKER_MIN", 0.55))   # taker 동조로 인정할 매수/매도 비율 하한

# ── 상속 고정 (업계 표준 · 튜닝 금지 · 예산 비산입) ─────────────────
N_ATR        = 14
EMA_FAST     = 9
EMA_SLOW     = 21
TF_ENTRY     = "15m"
TF_FLOW      = "5m"
TF_MID       = "1h"
TF_MACRO     = "4h"
N_15M_FETCH  = 200
# L4① 흐름 분포 안정화: 5m fetch 수를 env화(기본 48=현행). F 백분위 표본이 ~24로 점프하므로
#   72로 늘리면 분포가 매끄러워져 노이즈성 누락↓. 자기정규화 유지(절대값 아님) → 단일변수 A/B.
N_5M_FETCH   = int(os.getenv("ORTHO_N_5M_FETCH", 48))
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
    """연속형 눌림목 백분위 밴드: (하한, 중심, 상한).

    L1 사각지대 해소: 하한=0·상한=100. 기존엔 (P_EXT,50,100-P_EXT)라 CONT 롱이 L_pct∈[10,50)만
    인정 → L_pct<10(EXT_LOW)은 REV 담당이었으나, 라우터가 TREND에서 REV를 금지해 **강추세 속 깊은
    눌림(A+ 매수자리)**이 어떤 폴라리티에도 안 걸려 누락됐다. 바닥을 0으로 내려 깊은 눌림까지 CONT가
    담당(FLOW=동조·구조=정렬 AND 가드가 끝물 오인을 막음). 롱숏 완전 대칭. 신규 파라미터 0.
    ※ 비라우터(POLARITIES=REV,CONT) 모드 한정으로 극단에서 REV·CONT가 동시 성립(2중 확인) 가능 —
       드물고 MAX_POS_DIR로 통제. 라우터 기본 ON에선 폴라리티 배타라 중복 없음.
    """
    return (0.0, 50.0, 100.0)


def summary() -> str:
    return (f"ALERT={'ON' if ALERT_ENABLED else 'OFF(학습)'} "
            f"| W_L={W_L} P_EXT={P_EXT} W_F={W_F} P_FLOW={P_FLOW} RR_MIN={RR_MIN} "
            f"| POLARITIES={','.join(POLARITIES)} "
            f"| regime={'ON('+ROUTER_MODE+',ER'+format(TREND_ER,'g')+'/vol'+str(int(VOL_HI))+'/pvol'+str(int(P_VOL))+')' if REGIME_ROUTER else 'OFF'} "
            f"reachK={TP_REACH_K:g} chaseK={CHASE_K:g} dedup={'ON' if CORR_DEDUP else 'OFF'} "
            f"brkRange={'ON' if BREAKOUT_RANGE else 'OFF'} "
            f"takerF={'ON' if FLOW_TAKER_CONFIRM else 'OFF'} n5m={N_5M_FETCH} "
            f"| risk={RISK_PER_TRADE:g}U BE@{BE_TRIGGER_R}R/+{BE_LOCK_R}R "
            f"maxDir={MAX_CONCURRENT_DIR} RR_MAX={RR_MAX} "
            f"| notion={'ON' if NOTION_ENABLED else 'OFF'}")
