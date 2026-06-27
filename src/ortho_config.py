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


def _env(name: str):
    """원시 환경변수. 미설정 또는 빈/공백 문자열이면 None(=기본값 사용).
    GitHub Actions는 미설정 Variable(`${{ vars.X }}`)을 '빈 문자열'로 주입하므로,
    빈 문자열을 '미설정'과 동일 취급해야 int()/float() 파싱이 깨지지 않는다."""
    v = os.getenv(name)
    return v if (v is not None and v.strip() != "") else None


def _int(name: str, default: int) -> int:
    v = _env(name)
    return int(float(v)) if v is not None else default   # "8"/"8.0" 모두 허용


def _float(name: str, default: float) -> float:
    v = _env(name)
    return float(v) if v is not None else default


def _str(name: str, default: str) -> str:
    v = _env(name)
    return v.strip() if v is not None else default


def _flag(name: str, default: str = "false") -> bool:
    v = _env(name)
    return (v if v is not None else default).strip().lower() in ("1", "true", "yes", "on")


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
LOG_LEVEL = _str("LOG_LEVEL", "INFO").upper()

# 모니터링 심볼 (라이브 — 알림·라이브 DB 적재)
SYMBOLS = ["BTC/USDT", "ETH/USDT", "HYPE/USDT", "SOL/USDT", "SUI/USDT", "XRP/USDT"]

# ── 유니버스 확장 (Tier A — Shadow 적재만, 라이브 미승격) ──────────────
#   심볼 추가는 *독립 표본*을 늘림(기존 심볼 과거에 곡선맞춤 불가) → 빈도↑ 최저 과적합 레버.
#   신규 심볼의 모든 신호는 EXPLORE:UNIVERSE 태그로 Shadow에만 적재 → 코호트 검증 후 라이브 승격.
#   Shadow 비활성(토큰/DB 없음) 시 자동 무시 → 라이브 동작 불변.
EXPLORE_SYMBOLS = [s.strip().upper() for s in _str("ORTHO_EXPLORE_SYMBOLS",
    "DOGE/USDT,ONDO/USDT,WLD/USDT,LIT/USDT,ZEC/USDT,PEPE/USDT,NEAR/USDT,AVAX/USDT").split(",") if s.strip()]

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
W_L            = _int("ORTHO_W_L", 72)            # #1  위치 정규화 윈도우(15m봉)
P_EXT          = _float("ORTHO_P_EXT", 10)        # #2  위치 극단 백분위 컷
N_MEAN         = _int("ORTHO_N_MEAN", 20)         # #3  평균(SMA) 기간 = REV TP
W_F            = _int("ORTHO_W_F", 6)             # #4  흐름 측정 창(5m봉)
P_FLOW         = _float("ORTHO_P_FLOW", 30)       # #5  흐름 반전 백분위 컷
LS_CROWD_VETO  = _float("ORTHO_LS_CROWD_VETO", 0.85)  # #6  군중 과밀 거부
TAKER_VETO     = _float("ORTHO_TAKER_VETO", 0.65)     # #7  Taker 역방향 거부
SPREAD_MAX_BPS = _float("ORTHO_SPREAD_MAX_BPS", 5)    # #8  스프레드 거부(bps)
SL_ATR_BUF     = _float("ORTHO_SL_ATR_BUF", 0.25)     # #9  SL 버퍼(ATR배수)
RR_MIN         = _float("ORTHO_RR_MIN", 1.0)         # #10 구조 RR 하한
T_MAX          = _int("ORTHO_T_MAX", 8)              # #11 보유한도(15m봉×8=2h) → 종가 채점
MAX_POS_DIR    = _int("ORTHO_MAX_POS_DIR", 2)        # #12 방향별 동시 슬롯(심볼별)

# ══════════════════════════════════════════════════════════════════
# 리스크·집행 레이어 (★ 진입 결정과 분리 — 엔트리 과적합 표면이 아님 ★)
# ──────────────────────────────────────────────────────────────────
#   위 12개는 "어떤 신호를 낼지"(진입 셋) → 곡선맞춤 위험의 본체.
#   아래는 같은 신호를 "어떻게 사이징·청산·기록·게이팅할지"(집행)만 바꾼다.
#   진입 셋을 일절 건드리지 않고, 전부 자기정규화(R·비율)라 과적합 표면을 늘리지 않음.
# ══════════════════════════════════════════════════════════════════
# C-1 등가-R 사이징: 거래당 고정 위험(USDT). SL 도달 = 정확히 이 금액 손실 → 모든 신호 동일 R.
RISK_PER_TRADE     = _float("ORTHO_RISK_PER_TRADE", 100)
# A-1 본전스톱: +BE_TRIGGER_R 도달 시 손절을 진입가(+BE_LOCK_R)로 이동. 0=비활성.
#     BE_LOCK_R 은 수수료/슬리피지 버퍼 겸 WIN/LOSS 이분채점의 0-PnL 모호성 제거용.
BE_TRIGGER_R       = _float("ORTHO_BE_TRIGGER_R", 1.0)
BE_LOCK_R          = _float("ORTHO_BE_LOCK_R", 0.05)
# A-3 포트폴리오 방향 노출 캡: 전 심볼 통틀어 동시 OPEN 동일방향 한도(상관 바스켓 상한).
#     크립토는 BTC에 ~0.8+ 상관 → 동시 5숏 = 사실상 한 포지션 5배. 큰 값=사실상 무제한.
MAX_CONCURRENT_DIR = _int("ORTHO_MAX_CONCURRENT_DIR", 3)
# A-4 TP 상한(RR): 타임스톱(T_MAX봉=2h) 안에 닿을 거리로 먼 목표를 당겨 TP·청산을 정합. 0=비활성.
RR_MAX             = _float("ORTHO_RR_MAX", 3.0)

# ══════════════════════════════════════════════════════════════════
# 레짐 라우터 + 도달가능 TP (R1 기본 ON · R2 기본 OFF, 단일변수 A/B)
# ──────────────────────────────────────────────────────────────────
#   R1: 국면(RANGE/TREND/EXPANSION)을 2축(추세효율 ER × 변동성)으로 판정해 맞는 폴라리티만 평가.
#       추세장 역행·혼탁구조 진입(데이터 −41R)을 구조 차단. 롱숏 대칭 불변. ★ 기본 ON.
#   R2: 명목 RR≠실현 R(캡처 52%)의 본체 — TP를 ATR·√T_MAX 도달거리로 상한.
#   둘 다 백분위·ATR 자기정규화 → 곡선맞춤 아님. R2는 켤 때 70/30 워크포워드로 단일검증.
# ══════════════════════════════════════════════════════════════════
REGIME_ROUTER = _flag("ORTHO_REGIME_ROUTER", "true")         # R1 라우터 ON/OFF (★ 기본 ON)
VOL_HI        = _float("ORTHO_VOL_HI", 70)          # R1 확장 레짐 변동성 백분위 컷
TREND_ER      = _float("ORTHO_TREND_ER", 0.4)       # R1 추세효율(ER) 레벨 컷(0~1, 스케일프리) — 조기 TREND 승격
# R1-S SOFT 라우터(L2): STRICT(기본)=레짐당 폴라리티 1개(현행). SOFT=레짐 경계 모호구간에서만 양폴라리티 평가.
#   경계 깜빡임(ER≈TREND_ER 진동)으로 "경계 반대편" 셋업을 놓치는 누락을 회수. 기본 STRICT라 현행 보존.
#   확장은 ER 모호구간(|ER−TREND_ER|≤ROUTER_SOFT_ER)에서만 → 명확한 추세/레인지는 그대로 단일폴라리티.
ROUTER_MODE    = _str("ORTHO_ROUTER_MODE", "STRICT").upper()    # STRICT | SOFT
ROUTER_SOFT_ER = _float("ORTHO_ROUTER_SOFT_ER", 0.1)           # SOFT 모호구간 폭(ER 레벨)
# R2 도달가능 TP 계수: TP거리 ≤ K·ATR·√T_MAX. 0=비활성. 권장 첫 검증값 ≈ 1.2.
TP_REACH_K    = _float("ORTHO_TP_REACH_K", 0)
# R4 BREAKOUT(EXPANSION 전용): 거래량 서지 백분위 컷(절대 150% 대신 자기분포 백분위). 라우터 ON일 때만 가동.
P_VOL         = _float("ORTHO_P_VOL", 70)
# R5 추격 방지: CONT 진입가-빠른EMA 이격 한도(ATR 배수, 절대% 대신). 0=비활성, 권장 ≈ 1.0.
CHASE_K       = _float("ORTHO_CHASE_K", 0)
# R6 상관 디둡: 동일 실행 내 후보를 품질(RR) 우선 정렬 후 방향 캡 적용(그리디→최선순). 기본 OFF.
CORR_DEDUP    = _flag("ORTHO_CORR_DEDUP", "false")
# L3 EXPANSION 돌파 부활: ON 시 BREAKOUT 트리거에 '신선 W_F 신고/신저 레인지 돌파'를 OR로 추가
#   (VWAP 재탈환만으로는 18h 앵커라 거의 안 켜짐 → 고변동 국면 신호 ~0). 게이트(vol서지·flow·L과열)는 동일.
BREAKOUT_RANGE = _flag("ORTHO_BREAKOUT_RANGE", "false")
# L4 흐름축 보강: taker CVD(이미 수집)를 flow 확인 OR로 재사용 → 캔들프록시 F가 늦을 때 늦은-흐름 회수.
#   동조 강도 임계(0.5=중립). 기본 OFF=현행(캔들프록시 단독). 신규 데이터 의존 0.
FLOW_TAKER_CONFIRM = _flag("ORTHO_FLOW_TAKER_CONFIRM", "false")
FLOW_TAKER_MIN     = _float("ORTHO_FLOW_TAKER_MIN", 0.55)   # taker 동조로 인정할 매수/매도 비율 하한
# ── 분류기 지연제거 (MACRO_FRESH — 차단 전용 거부권, ★ 기본 ON) ──────────
#   진단: 추세 판정의 권위가 느린 EMA '교차'(4h EMA9>EMA21)에 있어 천장/바닥에서 ~수 시간~일
#   지연 → 전환 직후에도 분류기가 TREND/UPLEG로 오태깅 → stale-side 진입(상승장 막판 롱·하락전환
#   저점 롱·상승장 숏). 데이터: '상승장 숏'(counter SHORT·UPLEG) 한 코호트가 −19.8R(전체 순손실 초과).
#   교정: 상위TF(1h·4h) fast-EMA '기울기'(level 아닌 slope)가 거래 방향과 명백히 반대면 차단.
#   slope는 교차보다 전환을 수 봉 내 포착(지연↓). 15m이 아닌 상위TF에만 적용 → 건강한 눌림목 보존
#   (상위TF가 여전히 거래방향으로 기울면 비차단). 부호만 사용(절대임계 0=자기정규화)·롱숏 완전 대칭.
#   ★ 기본 ON(데이터 근거 상시 적용). 끄려면 ORTHO_MACRO_FRESH=false. 라우터 코호트 n≥30 검증서
#   70/30 양구간 개선·롱숏 대칭이 미달이면 false로 롤백(설계상 안전: 1h/4h 결측 시 fresh=0→비차단).
MACRO_FRESH    = _flag("ORTHO_MACRO_FRESH", "true")
MACRO_FRESH_LB = _int("ORTHO_MACRO_FRESH_LB", 2)   # fast-EMA 기울기 룩백(상위TF봉). 작을수록 민감
# ── 흐름-끝물 진입 차단 (FLOW_FLOOR — 차단 전용 거부권, ★ SHORT측 기본 ON) ──────
#   진단: F_pct(흐름 백분위)가 진입 방향으로 이미 극단까지 소진된 자리 = 끝물 추격 진입.
#   데이터(309건 백테스트): SHORT & F_pct<15 코호트 누적 −22.76R(전체 순손실 −20.5R 초과).
#   taker fix(6/23) 이후에도 17건 −8.44R로 잔존 → taker 버그와 독립인 별개 누수.
#   워크포워드 양구간 개선·100% SHORT 응집·"끝물 매도 추격→반등 피격" 기전 → 도입 검증 통과.
#   대칭점 LONG & F_pct>85는 +1.36R(누수 아님) → 효과가 SHORT 한쪽에만 있는 실측 비대칭.
#   교정: SHORT는 F_pct<FLOW_FLOOR_PCT(끝물 매도), LONG은 F_pct>100−FLOW_CEIL_PCT(끝물 매수)면 차단.
#     비대칭 반영: SHORT 바닥(15)만 기본 ON, LONG 천장은 0=OFF(증거 대기 — Shadow 측정 후 승격).
#   안전: 차단된 셋업은 Shadow(FLOW_FLOOR)로 적재·채점 → would-be 성과로 자기검증.
#     만약 막힌 SHORT가 오히려 이겼으면(승자 제거=FN) Shadow가 드러냄 → ORTHO_FLOW_FLOOR_PCT=0 롤백.
#   부호 없는 자기정규화 백분위라 곡선맞춤 아님. 끄려면 ORTHO_FLOW_FLOOR_PCT=0. 미러는 ORTHO_FLOW_CEIL_PCT>0.
FLOW_FLOOR_PCT = _float("ORTHO_FLOW_FLOOR_PCT", 15)   # SHORT 끝물 매도 차단(F_pct 하한). 0=OFF
FLOW_CEIL_PCT  = _float("ORTHO_FLOW_CEIL_PCT", 0)     # LONG 끝물 매수 차단(F_pct 천장 대칭). 0=OFF(증거대기)

# ── 넓은 조리개 (A+B+C — Shadow 학습/평가 전용, 라이브 불변) ──────────
#   목적: 표본 *수*가 아니라 *정보밀도*↑. 밴드를 옮기지 않고 near-miss를 기록 →
#   (A) 연속 축값 벡터 적재 → 오프라인 임계 스윕 / (B) 2-of-3 단일축 절제 → 어느 축이 과필터인지 /
#   (C) 경계(|마진|≤δ) 집중 표집 → 쿼터 효율. 막힌 게 아니라 '안 만든' 셋업을 EXPLORE:DROP_* 태그로 기록.
#   ★규율: 분석 시 argmax 최적화 금지 — 워크포워드 양구간+롱숏 대칭+사전등록일 때만 밴드 이동.
APERTURE_EXPLORE = _flag("ORTHO_APERTURE_EXPLORE", "true")    # A+B+C 넓은 조리개 적재 ON/OFF
APERTURE_DELTA   = _float("ORTHO_APERTURE_DELTA", 12)         # 경계 표집 폭(백분위 포인트) — 클수록 넓게

# ── 스캘핑 미시구조 피처 (모든 신호에 컬럼 저장 — 게이트 아님, 측정 후 게이트 원칙) ──
#   OBI(호가 불균형)·Taker 기울기(CVD 가속)·Funding 백분위. 자기정규화(부호/백분위)·롱숏 대칭.
#   지금은 *기록만* — 코호트가 워크포워드 양구간+대칭으로 엣지 입증 시에만 게이트로 승격(과적합 차단).
SCALP_FEATS    = _flag("ORTHO_SCALP_FEATS", "true")          # 피처 수집·컬럼 적재 ON/OFF
OBI_DEPTH      = _int("ORTHO_OBI_DEPTH", 10)                 # OBI 호가 깊이 레벨(상위 N단)
TAKER_SLOPE_LB = _int("ORTHO_TAKER_SLOPE_LB", 8)            # taker 매수비율 기울기 룩백(5m봉)
FUNDING_HIST   = _int("ORTHO_FUNDING_HIST", 60)            # funding 백분위 표본 길이(과거 펀딩 횟수)

# ══════════════════════════════════════════════════════════════════
# Shadow 로깅 (FN 측정 인프라 — 별도 Notion DB · ★ 기본 ON) ──────────
#   문제: 거부권(MACRO_FRESH·crowd·taker·spread)·추격컷·리스크캡이 막은 셋업은
#   logger.info로만 남고 어디에도 적재되지 않아 "막아서 손해였나(승자 제거=FN)
#   이득이었나(패자 제거)"를 사후 측정할 수 없다(False Negative 사각지대).
#   해법: 차단된 셋업을 '막힌 그 순간의 entry/TP/SL'로 별도 Shadow DB에 적재 →
#   resolver가 라이브와 *동일* triple-barrier(BE스톱 포함)로 채점 → would-be
#   WIN/LOSS·실현R. 거부권별 코호트에서 막힌 ExpR > 남긴(라이브) ExpR 이면
#   그 거부권은 승자를 거르는 FN 생성기 → 롤백 근거(taker 커밋이 1회 한 검증의 제도화).
#   안전: 라이브와 *물리분리된 별도 DB*(NOTION_SHADOW_DB_ID) → 라이브 통계 오염 0.
#         셋(플래그·토큰·DB id) 중 하나라도 없으면 완전 inert → 현행 동작 불변.
# ══════════════════════════════════════════════════════════════════
SHADOW_LOG          = _flag("ORTHO_SHADOW_LOG", "true")    # ★ 기본 ON (단, DB id+토큰 있어야 실제 활성)
# 기본 Shadow DB(=생성한 "🌑 Sig_Bot Shadow 기록"). 시크릿 NOTION_SHADOW_DB_ID로 덮어쓸 수 있음.
#   ※ 이 DB에 봇 인티그레이션(NOTION_TOKEN)을 'Add connections'로 연결해야 실제 적재됨(Notion 권한).
NOTION_SHADOW_DB_ID = _str("NOTION_SHADOW_DB_ID", "36a004b1d5b8490d921bd3ec3e980baf")
# 측정 대상 차단 카테고리(쉼표구분). 좁히면 캠페인 집중(예: "MACRO_FRESH"만).
SHADOW_REASONS      = tuple(r.strip().upper() for r in _str(
    "ORTHO_SHADOW_REASONS",
    "MACRO_FRESH,FLOW_FLOOR,CHASE,CROWD,TAKER,SPREAD,SLOT,DIRCAP,EXPLORE").split(",") if r.strip())
# EXPLORE = 넓은 조리개(A+B+C)·유니버스 확장 적재 카테고리. 빼면 그 campaign만 정지.
SHADOW_MAX_PER_RUN  = _int("ORTHO_SHADOW_MAX_PER_RUN", 0)    # 런당 write 상한(0=무제한, 기본). 코인별 OPEN 중복차단이 자연 상한 → 런당 진입 제한 없음. >0 설정 시에만 API 보호용 상한
# 활성 조건: 플래그 ON ∧ 토큰 ∧ 별도 DB id. 하나라도 없으면 비활성(코드 기본 OFF).
SHADOW_ENABLED      = bool(SHADOW_LOG and NOTION_TOKEN and NOTION_SHADOW_DB_ID)

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
N_5M_FETCH   = _int("ORTHO_N_5M_FETCH", 48)
N_HTF_FETCH  = 60      # 1h/4h 구조축용
# 닫힌 캔들만 사용(기록 무결성): OKX fetch_ohlcv는 형성 중(미완성) 캔들을 마지막 원소로 반환한다.
#   ON(기본)이면 미완성 봉을 드롭 → entry·전축·Signaled At이 '마지막 닫힌 봉'에 앵커링되어
#   차트와 정확히 일치하고 재현 가능. OFF면 레거시(미완성 봉 종가=일시적 스냅샷). 단일변수 롤백.
CLOSED_CANDLES = _flag("ORTHO_CLOSED_CANDLES", "true")

# ── 폴라리티: 어떤 셋업을 기록할지 ───────────────────────────────
#   학습기간엔 둘 다 기록해 A/B 비교 → 우위 폴라리티만 남기는 식으로 발전
POLARITIES = tuple(p.strip() for p in _str("ORTHO_POLARITIES", "REV,CONT").split(",") if p.strip())

# ── 데이터 수집 재시도 ────────────────────────────────────────────
MAX_RETRIES = 3
RETRY_DELAY_S = 1.5
TAKER_PERIOD = "5m"
TAKER_LOOKBACK = 12

RESOLVER_MAX_OPEN_PER_RUN = _int("ORTHO_RESOLVER_MAX", 100)


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
            f"closedC={'ON' if CLOSED_CANDLES else 'OFF'} "
            f"fresh={'ON(lb'+str(MACRO_FRESH_LB)+')' if MACRO_FRESH else 'OFF'} "
            f"floor={'S<'+format(FLOW_FLOOR_PCT,'g') if FLOW_FLOOR_PCT>0 else 'OFF'}"
            f"{('/L>'+format(100-FLOW_CEIL_PCT,'g')) if FLOW_CEIL_PCT>0 else ''} "
            f"| risk={RISK_PER_TRADE:g}U BE@{BE_TRIGGER_R}R/+{BE_LOCK_R}R "
            f"maxDir={MAX_CONCURRENT_DIR} RR_MAX={RR_MAX} "
            f"| notion={'ON' if NOTION_ENABLED else 'OFF'} "
            f"shadow={'ON(q'+(str(SHADOW_MAX_PER_RUN) if SHADOW_MAX_PER_RUN>0 else '∞')+')' if SHADOW_ENABLED else 'OFF'} "
            f"aperture={'ON(δ'+format(APERTURE_DELTA,'g')+')' if APERTURE_EXPLORE else 'OFF'} "
            f"explore={len(EXPLORE_SYMBOLS)}sym scalp={'ON' if SCALP_FEATS else 'OFF'}")
