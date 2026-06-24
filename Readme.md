# ORTHO-3 — 15분봉 가상매매 시그널 봇

> **⚠️ 15분봉(15m entry) 전용.** entry=15m / flow=5m / structure=1h·4h
> 레거시(만능 스코어러·207 파라미터)는 **전면 제거**됨. ORTHO-3 단독 엔진.

---

## 개요

OKX 선물 시장 대상, **직교 3축 합의 엔진**으로 롱/숏 신호를 생성하는 **가상매매(페이퍼)** 봇.
GitHub Actions로 무인 운영, Notion에 기록·채점, 텔레그램 알림(ON/OFF). 실주문 없음 — 승률 검증 후 실매매 도입 예정.

**심볼:** `BTC/USDT` `ETH/USDT` `HYPE/USDT` `SOL/USDT` `SUI/USDT` `XRP/USDT`

운영: **신호 15분 cron + 채점 5분 cron.** 2주 학습기간(알림 OFF)으로 데이터를 모은 뒤,
코호트 분석으로 진입기준을 보정하고 알림을 켠다 → 상세는 [`docs/ORTHO_GUIDE.md`](docs/ORTHO_GUIDE.md).

---

## ORTHO-3 엔진

### 설계 철학
1. 매 판정은 그 코인의 **최근 자기 분포 백분위** 안에서만 (미시 레짐, 매크로 무관)
2. 점수를 **더하지 않는다** — 축은 동의(AND)/거부(VETO)만
3. 절대 숫자 임계 금지 — 모든 컷은 백분위 또는 ATR 배수
4. 롱/숏 **완전 대칭**
5. 파라미터 **12개 하드캡** (레거시 207 → 12)
6. **무상태(stateless)**

### 세 직교축 + 거부권
| 축 | 측정 | 백분위 |
|---|---|---|
| **L 위치** | (close−SMA)/ATR 의 W_L 분포 | `L_pct` |
| **F 흐름** | CVD 프록시(캔들 모멘텀) 슬라이딩 | `F_pct` |
| **S 구조** | 15m/1h/4h EMA 정렬 + 신선 돌파 | 이진 |
| **VETO** | 군중 과밀(LS) · Taker 역방향 · 호가 스프레드 · *(옵션)상위TF 추세지연(M1)* | 차단 전용 |

### 세 폴라리티 (R1 레짐 라우터와 연동)
| 폴라리티 | 국면 | 조건 | 전략 대응 |
|---|---|---|---|
| **REV** (회귀형) | RANGE | L=극단 ∧ F=반전 ∧ ¬S_broken → 평균(SMA) 회귀 | S1 BB+RSI |
| **CONT** (연속형) | TREND | L=눌림 ∧ F=동조 ∧ S=정렬 ∧ EMA초입(R5) → 추세 지속 | S3 정배열 초입 |
| **BREAKOUT** (돌파형) | EXPANSION | VWAP 신선 재탈환 ∧ 거래량 서지(P_VOL) ∧ F=방향 | S2 VWAP+Volume |

**레짐 라우터(R1)는 기본 ON**: 봇이 국면을 자동 판정해 그 국면에 맞는 폴라리티만 평가한다(BREAKOUT 포함).
끄면(`ORTHO_REGIME_ROUTER=false`) `ORTHO_POLARITIES` 환경변수가 평가할 폴라리티를 지정한다(BREAKOUT 제외).

RR < `RR_MIN`이면 진입 스킵. 타임스톱 `T_MAX`=8봉(2h).

---

## 텔레그램 알림 ON/OFF (학습기간)

`ALERT_ENABLED` (GitHub Actions **Variable**):
| 값 | 동작 |
|---|---|
| `false` (기본) | **알림 OFF** — Notion 기록·채점만. **2주 학습기간** 기본값 |
| `true` | **알림 ON** — 신규 신호 시 텔레그램 발송 |

> 알림과 무관하게 Notion 기록·채점은 **항상** 동작(가상매매 데이터 축적).

---

## 파일 구조

```
src/
├── ortho_config.py    # 파라미터 · ALERT_ENABLED · 키
├── ortho_data.py      # OKX 수집 (캔들/ls/taker/스프레드)
├── ortho_engine.py    # L·F·S 3축·진리표·거부권·배리어·레짐·BREAKOUT (순수·무상태)
├── ortho_notify.py    # 텔레그램 ON/OFF
├── ortho_notion.py    # Notion 기록/조회/판정
├── ortho_resolver.py  # 채점 (triple-barrier)
├── ortho_main.py      # 진입점 (15분) — 수집→품질정렬(R6)→승인
└── timeutil.py        # KST 시각

scripts/
├── migrate_notion_to_ortho.py   # Notion 양식 전환·삭제 (1회)
└── ortho_report.py              # R-기준 성과 리포트 (Polarity·Regime 코호트)

.github/workflows/
├── ortho_main.yml       # 신호 생성 15분 cron
├── ortho_resolver.yml   # 채점 5분 cron
└── migrate_notion.yml   # Notion 전환 (수동)

docs/
├── ORTHO3.md       # 설계 상세
└── ORTHO_GUIDE.md  # 운영·발전·행동지침
```

**의존성:** `ccxt`, `requests` (pandas/numpy 제거).

---

## 파라미터

### 12개 핵심 파라미터 (환경변수 오버라이드)

| 변수 | 기본 | 역할 |
|---|---|---|
| `ORTHO_W_L` | 72 | 위치 정규화 윈도우 |
| `ORTHO_P_EXT` | 10 | 위치 극단 백분위 컷 |
| `ORTHO_N_MEAN` | 20 | SMA 기간(REV TP) |
| `ORTHO_W_F` | 6 | 흐름 측정 창(5m) |
| `ORTHO_P_FLOW` | 30 | 흐름 반전 컷 |
| `ORTHO_LS_CROWD_VETO` | 0.85 | 군중 과밀 거부 |
| `ORTHO_TAKER_VETO` | 0.65 | Taker 역방향 거부 |
| `ORTHO_SPREAD_MAX_BPS` | 5 | 스프레드 거부 |
| `ORTHO_SL_ATR_BUF` | 0.25 | SL 버퍼 |
| `ORTHO_RR_MIN` | 1.0 | 구조 RR 하한 |
| `ORTHO_T_MAX` | 8 | 타임스톱(15m봉×8=2h) |
| `ORTHO_MAX_POS_DIR` | 2 | 방향별 동시 슬롯(심볼별) |
| `ORTHO_POLARITIES` | `REV,CONT` | 라우터 OFF 시 평가할 폴라리티 |

> 실질 튜닝 대상은 `W_L`·`P_EXT`·`P_FLOW` 3개. 단일 변수 원칙·워크포워드 검증.

### 리스크·집행 레이어 (진입 결정과 분리 — 과적합 표면 아님)

| 변수 | 기본 | 역할 |
|---|---|---|
| `ORTHO_RISK_PER_TRADE` | 100 | 거래당 고정 위험(USDT) — C-1 등가-R 사이징 |
| `ORTHO_BE_TRIGGER_R` | 1.0 | 본전스톱 발동 R (A-1) |
| `ORTHO_BE_LOCK_R` | 0.05 | 본전스톱 고정 R (A-1) |
| `ORTHO_MAX_CONCURRENT_DIR` | 3 | 전 심볼 동시 동일방향 한도 (A-3 상관 노출 캡) |
| `ORTHO_RR_MAX` | 3.0 | TP 상한 RR (A-4 — 타임스톱 내 도달 가능 범위로 TP 축소) |

### 레짐 라우터 + 구조 보정 (R1–R6)

아래 진단에 기반한 구조 보정. 모든 컷은 **백분위·ATR·스케일프리 비율 자기정규화**(절대 가격/거래량 숫자 금지).
**R1 라우터만 기본 ON**(핵심 누수 차단), 나머지(R2/R5/R6)는 기본 OFF로 **한 번에 하나** + 70/30 워크포워드 검증.

> **진단:** 초기 195건 신호 분석 → 기대값 **−0.14R/거래** (승률 41.5%, 캡처효율 52%).
> 주요 누수: ① 추세장 역행 역포지션(−41R), ② TP가 타임스톱 내 미도달(캡처 52%), ③ 상관 손실 클러스터링.

| 변수 | 기본 | 역할 |
|---|---|---|
| `ORTHO_REGIME_ROUTER` | **`true`** | **R1.** 국면 자동 판정(RANGE→REV / TREND→CONT / EXPANSION→BREAKOUT). 추세장 역행 구조 차단. 롱숏 대칭 불변 |
| `ORTHO_TREND_ER` | 0.4 | R1 추세효율(Kaufman ER) 레벨 컷(0~1, 스케일프리) — 조기 TREND 승격 |
| `ORTHO_VOL_HI` | 70 | R1 EXPANSION 판정 변동성 백분위 컷 |
| `ORTHO_TP_REACH_K` | 0 | **R2.** TP거리 ≤ `K·ATR·√T_MAX` 로 상한(명목RR≠실현R 보정). 0=비활성, 권장 첫값 ≈1.2 |
| `ORTHO_P_VOL` | 70 | **R4.** BREAKOUT 거래량 서지 백분위 컷(절대 150% 대신 자기분포). 라우터 ON·EXPANSION에서만 |
| `ORTHO_CHASE_K` | 0 | **R5.** CONT 추격 방지: `\|진입−EMA_fast\| ≤ K·ATR`. 0=비활성, 권장 ≈1.0 |
| `ORTHO_CORR_DEDUP` | `false` | **R6.** 동일 실행 후보를 RR 우선 정렬 후 방향 캡 적용(그리디→최선순). 상관 클러스터 완화 |
| `ORTHO_ROUTER_MODE` | `STRICT` | **L2.** `SOFT`면 ER 모호구간(`\|ER−TREND_ER\|≤SOFT_ER`)에서 REV·CONT 둘 다 평가(경계 깜빡임 누락 회수). `STRICT`=현행 |
| `ORTHO_ROUTER_SOFT_ER` | 0.1 | L2 SOFT 모호구간 폭(ER 레벨). `ROUTER_MODE=SOFT`에서만 |
| `ORTHO_BREAKOUT_RANGE` | `false` | **L3.** ON 시 BREAKOUT 트리거에 '신선 W_F 신고/신저 레인지 돌파'를 OR 추가(EXPANSION 신호 회복). 게이트 동일 |
| `ORTHO_N_5M_FETCH` | 48 | **L4①.** 흐름 분포 표본 수(5m). 72로 늘리면 F 백분위 노이즈↓(자기정규화 유지) |
| `ORTHO_FLOW_TAKER_CONFIRM` | `false` | **L4②.** ON 시 taker CVD 동조를 흐름축 OR-확인으로 재사용(늦은-흐름 회수). 캔들 F가 명백 역방향이면 무효 |
| `ORTHO_FLOW_TAKER_MIN` | 0.55 | L4② taker 동조 인정 매수/매도 비율 하한 |
| `ORTHO_MACRO_FRESH` | `false` | **M1 분류기 지연제거.** ON 시 상위TF(1h·4h) fast-EMA **기울기**가 거래 방향과 명백히 반대면 차단(차단 전용 거부권). 느린 EMA '교차'의 천장/바닥 지연으로 생기는 stale-side 진입(상승장 막판 롱·하락전환 저점 롱·상승장 숏) 차단. 부호만 사용·롱숏 대칭·신규 fetch 0 |
| `ORTHO_MACRO_FRESH_LB` | 2 | M1 fast-EMA 기울기 룩백(상위TF봉). 작을수록 민감 |

#### L1~L4 — 누락(미진입) 회수 업그레이드 (전수점검 기반)

스캘핑에서 **놓치는 좋은 순간(false negative)**을 과적합 없이 메우는 4개 보강. 전부 자기정규화·롱숏
대칭이며 **기본값=현행 보존**(L1만 즉시 적용, 나머지는 토글 단일변수 A/B).
- **L1 (즉시 적용):** CONT 눌림 밴드 바닥을 `P_EXT`→`0`. 라우터가 TREND에서 REV를 금지하는 동안
  **강추세 속 깊은 눌림(`L_pct<10`, A+ 매수자리)**이 REV=금지·CONT=밴드밖으로 통째 누락되던 사각지대 해소.
  FLOW·구조 AND 가드 유지 → 끝물 오인 방지. 신규 파라미터 0, 완전 대칭.
- **L2 (`ORTHO_ROUTER_MODE=SOFT`):** ER≈TREND_ER 경계 진동으로 폴라리티 적격이 깜빡여 "경계 반대편"
  셋업을 놓치는 누락을, 모호구간에서만 양폴라리티 평가로 회수(최종 판정은 기존 AND축·VETO).
- **L3 (`ORTHO_BREAKOUT_RANGE=true`):** 18h VWAP 앵커 탓에 EXPANSION 신호가 ~0이던 것을 고전
  레인지 돌파 트리거로 부활.
- **L4 (`ORTHO_FLOW_TAKER_CONFIRM=true` / `ORTHO_N_5M_FETCH=72`):** 흐름축 표본 확대 + 이미 수집 중인
  taker CVD를 확인용으로 재사용해 '이미 반전한 흐름'만 요구하던 지각·누락을 보완.

> 워크플로 배선(W2): 위 모든 `ORTHO_*` 노브가 `ortho_main.yml`/`ortho_resolver.yml` env로 연결됨 —
> GitHub Variables 설정이 실제 런타임에 반영된다(이전엔 `ALERT_ENABLED`·`ORTHO_POLARITIES`만 전달됨).

#### M1 — 분류기 지연제거 (false-positive 차단 · L1~L4의 거울쌍)

L1~L4가 "놓친 좋은 진입(false negative)"을 메웠다면, M1은 **"하지 말았어야 할 진입(false positive)"**을 줄인다.
추세 판정의 권위가 느린 EMA **교차**(fast>slow level)에 있어 천장/바닥에서 ~수 시간~일 지연 → 전환
직후에도 분류기가 TREND/UPLEG로 오태깅 → **stale-side 진입**(상승장 막판 롱·하락전환 저점 롱·상승장 숏).
- **진단(263건 실현 R 검증):** `counter-trend`(4h 레그 역행) ExpR −0.20R·PF 0.58, 70/30 양 구간 음(陰).
  특히 `counter SHORT·UPLEG`(상승장 숏) 한 코호트가 **−19.8R**(전체 순손실 −9.0R 초과). 6/23 당일은
  롱 5건 전패(−5R) — 4h가 하락전환을 못 따라잡아 TREND/UPLEG로 오태깅한 막판/저점 롱.
- **교정:** `ORTHO_MACRO_FRESH=true` 시 상위TF(1h·4h) fast-EMA **기울기**(level 아님)가 거래 방향과
  명백히 반대면 차단. slope는 교차보다 전환을 수 봉 내 포착 → 지연↓. **15m이 아닌 상위TF에만** 적용해
  건강한 눌림목 진입은 보존(상위TF가 여전히 거래방향으로 기울면 비차단). 혼조(fresh=0)도 비차단.
- 부호만 사용(절대임계 0=자기정규화) · 롱숏 완전 대칭 · 차단 전용(점수 가산 금지) · 신규 fetch 0.
- **한계(정직히):** EMA 기울기도 전환 '첫 봉'은 못 잡음(지연 0 불가). 효과는 라우터 표본 n≥30·70/30
  검증 전엔 미확정 → **기본 OFF**, 단일변수 A/B로만 켤 것.

#### R1 — 레짐 라우터 상세 (2축 판정: 추세효율 × 변동성)

다TF EMA `up` 카운트만으로는 부족하다 — 3TF에서 부분정렬(up∈{1,2})이 가장 길고, "추세 초입
(15m·1h 정렬, 4h 지연)"과 "혼탁 레인지"를 구분하지 못한다(4h EMA21은 ~3.5일 지연 → 전 TF
만장일치는 너무 늦음). 그래서 두 자기정규화 축을 교차한다:

- **추세효율** = Kaufman ER `|순변화|/Σ|봉간변화|` (0=노이즈, 1=깨끗한 추세). [0,1] 스케일프리
  비율이라 RR처럼 **레벨**을 직접 임계(백분위化 금지 — 지속추세는 항상 高ER이라 백분위가 50으로 붕괴).
- **변동성** = 정규화 변동폭 `(H−L)/C` 의 W_L 분포 백분위.

| 국면 | 판정 조건 | 폴라리티 | 전략 |
|---|---|---|---|
| **TREND** | 전 TF 만장일치(`up==n\|0`) **또는** `ER≥TREND_ER` ∧ 다수TF가 순변화 방향과 일치 | CONT | S3 정배열 |
| **EXPANSION** | (TREND 아님) ∧ `vol≥VOL_HI` ∧ 저효율 = 방향 없이 크게 흔들리는 혼탁확장 | BREAKOUT | S2 VWAP+Volume |
| **RANGE** | 그 외(저효율·저변동) — 평균회귀 토양 | REV | S1 BB+RSI |

- **조기 추세 포착:** ER이 높고 EMA 다수 방향이 순변화 부호와 **일치**할 때만 up∈{1,2}를 TREND로
  승격 → 4h 지연 없이 추세 초입을 잡으면서, 역추세 반등(다수는 하락인데 단기 급반등)은 승격 거부.
- 국면 판정은 기존 데이터만 사용(신규 fetch 0). 고효율 방향성 확장은 TREND(CONT)로, 저효율 혼탁
  확장만 EXPANSION(BREAKOUT)으로 분리 → 깨끗한 추세를 돌파셋업으로 오인하지 않음.

#### R4 — BREAKOUT 폴라리티 상세
EXPANSION 국면 전용. "거래량 동반 VWAP 신선 재탈환"으로 저확신 확장 진입을 대체:
- 롱: `직전종가 < VWAP ≤ 현재종가` ∧ `거래량서지 ≥ P_VOL 백분위` ∧ `F=FLOW_UP` ∧ `L≠EXT_HIGH`
- 숏: 거울쌍
- SL = VWAP±buf(정적 무효화 — VWAP 재이탈=돌파 실패). TP = 직전 스윙(R2 상한 적용).
- 거래량 임계는 절대 150%가 아닌 그 코인 자기분포 백분위(P_VOL) → 자기정규화.

#### R5 — 추격 방지 상세
CONT 진입가와 빠른 EMA(EMA_FAST=9)의 이격을 ATR 배수로 제한:
- `|entry − EMA_fast| ≤ CHASE_K · ATR` 를 만족해야 진입 허용
- 성숙·연장 추세(데이터: up3/3 −0.21R)를 ATR 자기정규화 이격으로 차단. 절대 % 임계 없음.

#### R6 — 상관 디둡 상세
동일 cron 실행 내 후보를 품질(RR) 기준 내림차순 정렬 후 방향 캡(`MAX_CONCURRENT_DIR`) 적용:
- 기존 그리디(심볼 알파벳순) → 평범한 후보가 슬롯을 선점하고 고품질 후보가 잘림
- CORR_DEDUP ON → 최선(高RR) 후보부터 방향 슬롯을 채움 → 동질 배치·연속 손실 완화

#### R7 — 레짐 기록 (자동, 신호마다)
- 신호 dict의 `"regime"` 필드에 `RANGE/TREND/EXPANSION/OFF` 기록
- Reason 문자열에 `RG=레짐` 태그 포함
- `scripts/ortho_report.py`가 CSV 내보내기를 읽어 **Polarity·Regime·Regime×Direction** 코호트 분해

---

## Notion 스키마 (ORTHO 양식)

`Signal` `Status`(OPEN/WIN/LOSS/TIMEOUT) `Engine`(ORTHO-REV/CONT/BREAKOUT) `Polarity`(REV/CONT/BREAKOUT)
`Symbol` `Direction`(LONG/SHORT) `Entry` `TP` `SL` `R Dist` `Bars Limit` `RR`
`L_pct` `F_pct` `S_state` `MacroTag`(UPLEG/DOWNLEG/FLAT) `Reason`(`RG=레짐` 포함)
`MFE R` `MAE R` `Bars To Exit` `Signaled At` `Resolved At` `Note`

---

## 환경변수

| 키 | 종류 | 용도 |
|---|---|---|
| `OKX_API_KEY/SECRET/PASSPHRASE` | Secret | OKX 데이터 |
| `TELEGRAM_BOT_TOKEN/CHAT_ID` | Secret | 알림 |
| `NOTION_TOKEN/DATABASE_ID` | Secret | 기록·채점 |
| `ALERT_ENABLED` | **Variable** | `false`(학습)/`true`(알림) |
| `ORTHO_*` | Variable | 파라미터 오버라이드(선택) |

---

## 성과 리포트

```bash
python3 scripts/ortho_report.py <notion_export.csv>
```

Notion DB를 CSV로 내보낸 후 실행. 의존성 없음(표준 라이브러리). 출력:
- 전체 기대값(R/거래) + **부트스트랩 95% CI**, 승률 + **Wilson 95% CI**, 손익비, Profit Factor
- 캡처 효율(MFE 대비 실현 R), 타임스톱 비율
- **워크포워드 70/30**(시간순 앞70/뒤30 분할 — 양쪽 +R 인 코호트만 신뢰)
- **롱숏 대칭 패널**(방향별 기대값 — 부호반대/격차>0.3R 비대칭 경고)
- 코호트 분해: **Polarity / Regime / Regime×Direction / Direction / MacroTag / S_state / Symbol / RR bucket**
  — 각 라인에 **n게이트(n<30=표본부족)·CI·판정(엣지+ / 노이즈 / 엣지-)** 내장
- 손실 클러스터링: 동시배치 동질비율, 최장 연속손실

> **Stage 0(증거 신뢰화):** 평균만으로는 우연과 엣지를 구분 못 한다. CI 하한이 0을 넘고(`엣지+`),
> 70/30 양쪽에서 +R 이며, 롱숏 대칭인 코호트만 신뢰 → 그 위에서 단일변수 보정. 표본 <30 코호트엔 반응 금지.

---

## 빠른 시작

1. push 후 Actions 탭에서 `ORTHO Signal Bot (15m)` · `ORTHO Resolver (채점 5m)` 확인
2. Secrets 확인 + Variable `ALERT_ENABLED=false`
3. Notion 기록 삭제(스키마는 이미 ORTHO 양식)
4. 2주 학습 → Notion CSV 내보내기 → `ortho_report.py` 코호트 분석 → 파라미터 보정 → `ALERT_ENABLED=true`

### R2/R5/R6 단계적 활성화 (학습 데이터 확보 후)

> **R1 라우터는 기본 ON**(추세장 역행 차단 — 가장 큰 누수). 나머지는 기본 OFF →
> 켤 때는 **한 번에 하나** + 70/30 워크포워드 검증.

```
# R1(레짐 라우터)은 이미 ON. 데이터 60건+ 확보 후 아래를 순차 활성화:
1. ORTHO_TP_REACH_K=1.2         ← TP 도달가능 상한 (캡처효율 개선)
2. ORTHO_CHASE_K=1.0            ← CONT 연장추세 진입 차단
3. ORTHO_CORR_DEDUP=true        ← 상관 배치 품질 정렬

# 라우터를 끄고 옛 동작(REV·CONT 무국면)으로 되돌리려면:
#   ORTHO_REGIME_ROUTER=false   (그러면 ORTHO_POLARITIES 가 폴라리티 지정)
```

자세한 절차·발전 방법론: [`docs/ORTHO_GUIDE.md`](docs/ORTHO_GUIDE.md)
