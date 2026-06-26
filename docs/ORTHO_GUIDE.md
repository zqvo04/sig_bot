# ORTHO-3 운영·발전 가이드 (가상매매 → 실매매)

레거시를 전면 제거한 ORTHO-3 단독 봇의 **① 시스템 구조 · ② 엔진 발전 방법론 · ③ 적용 행동지침**.

---

## 1. 시스템 구조

```
┌─ ortho_main.yml  (15분 cron) ─────────────────────────┐
│  ortho_main.py                                          │
│   심볼별: collect_context(ls/taker)                     │
│        → ortho_engine.evaluate (L·F·S 3축 AND + VETO)   │
│        → ortho_notion.log_signal  (Status=OPEN, 항상)   │
│        → ortho_notify.notify_signal (ALERT_ENABLED만)   │
└─────────────────────────────────────────────────────────┘
┌─ ortho_resolver.yml (5분 cron) ───────────────────────┐
│  ortho_resolver.py                                      │
│   Notion OPEN 조회 → OKX 5m 가격 triple-barrier 판정    │
│   → WIN/LOSS/TIMEOUT + MFE/MAE(R) 기록                  │
└─────────────────────────────────────────────────────────┘
```

| 파일 | 역할 | ccxt |
|---|---|---|
| `ortho_config.py` | 12 파라미터 · ALERT_ENABLED · 키 | — |
| `ortho_data.py` | OKX 수집(캔들/ls/taker/스프레드) | ✅ |
| `ortho_engine.py` | L·F·S 3축·진리표·거부권·배리어 (순수) | (지연) |
| `ortho_notify.py` | 텔레그램 ON/OFF | — |
| `ortho_notion.py` | Notion 기록/조회/판정 | — |
| `ortho_resolver.py` | 채점(triple-barrier) | ✅ |
| `ortho_main.py` | 진입점 | ✅ |
| `timeutil.py` | KST 시각 | — |

> 의존성: `ccxt`, `requests` 둘뿐. 레거시 207상수·스코어러·pandas 전부 제거됨.

---

## 2. 엔진 발전 방법론 (4단계)

> **대원칙:** 파라미터 12개 하드캡 · 백분위 유지(절대숫자 금지) · 단일 변수 변경 · 워크포워드 검증 · "추가하려면 하나 제거". 이걸 어기는 순간 레거시의 과적합 지옥으로 회귀한다.

### Phase 1 — 데이터 축적 (2주, 알림 OFF)
- `ALERT_ENABLED=false`. 봇은 REV·CONT 두 폴라리티 가상신호를 Notion에 쌓고 5분마다 채점.
- 목표: 폴라리티당 **최소 30~60건의 해소(WIN/LOSS)** 표본 확보.
- 절대 이 기간에 파라미터를 만지지 말 것(peek-and-tune = 과적합).

### Phase 2 — 코호트 분석 (Notion 내보내기 → 분석)
Notion DB를 CSV로 내보내 다음 축으로 **승률·기대값(R)**을 분해:
| 분석 축 | 보는 것 |
|---|---|
| **Polarity** (REV vs CONT) | 어느 셋업이 +기대값인가 |
| **L_pct 버킷** (≤5, 5–10, …) | 극단이 깊을수록 좋은가 |
| **F_pct 버킷** | 흐름 반전 강도와 성과 |
| **MacroTag** (UPLEG/DOWNLEG/FLAT) | 특정 국면 편향 여부 |
| **Symbol** | 코인별 엣지 차이 |
| **RR / MFE / MAE** | 손익비·칼날 패턴 |

> 기대값(R) = (Σ WIN의 RR − Σ LOSS의 1) / 해소건수. 이게 양(+)인 코호트가 엣지.

**Stage 0(증거 신뢰화):** `ortho_report.py`가 평균뿐 아니라 **표본수 게이트(n<30=표본부족)·승률 Wilson
CI·기대값 부트스트랩 95% CI·70/30 워크포워드·롱숏 대칭 패널**을 출력한다. **엣지 판정 기준**:
① 기대값 CI 하한 > 0(`엣지+`, 0을 포함하면 노이즈) ∧ ② 70/30 양쪽 +R(`✓두구간+`) ∧ ③ 롱숏 대칭.
세 조건을 모두 만족하는 코호트만 신뢰하고 그 위에서 Phase 3 단일변수 보정을 한다. 표본 <30 코호트엔
절대 반응하지 말 것(소표본 우연 = 과적합의 씨앗).

### Phase 3 — 진입기준 보정 (단일 변수 · 워크포워드)
분석 결과로 **한 번에 하나씩** 조정 (환경변수 오버라이드, 코드 수정 불필요):
- 약한 폴라리티 제거: `ORTHO_POLARITIES=REV` (또는 CONT)
- 극단 강화: `ORTHO_P_EXT=7` (신호↓ 질↑) / 완화: `12`
- 흐름 엄격화: `ORTHO_P_FLOW=25`
- 손익비 하한: `ORTHO_RR_MIN=1.2`
- 위치 윈도우: `ORTHO_W_L` (48~96)

각 변경은 **앞 70% 학습 / 뒤 30% 검증**으로 효과가 유지되는지 확인. 표본 전체에 핏하지 말 것.

#### 구조 보정 토글 (R1 기본 ON · 나머지 OFF — 신호기록 분석 기반)
1차 분석에서 **실현 −0.14R**(역행 숏·캡처 52%·RR≥3 손실)이 드러남. 진입 셋을 재작성하지 않고
국면 라우팅·도달가능 TP로 누수를 막는다(전부 자기정규화 → 과적합 표면 아님, 롱숏 대칭):
- **`ORTHO_REGIME_ROUTER` — 기본 ON.** RANGE→REV / TREND→CONT / EXPANSION→BREAKOUT. 추세장 역행을
  구조 차단(가장 큰 누수). 국면은 **2축(추세효율 ER 레벨 × 변동성 백분위)**으로 판정 —
  `ORTHO_TREND_ER`(0.4, ER 레벨 컷)로 추세 초입(부분정렬 up∈{1,2})을 4h 지연 없이 조기 TREND 승격,
  `ORTHO_VOL_HI`(70)로 저효율 혼탁확장만 EXPANSION 분리. 끄려면 `=false`(→ `ORTHO_POLARITIES` 사용).
- `ORTHO_TP_REACH_K=1.2` — TP를 `K·ATR·√T_MAX` 도달거리로 상한. 캡처효율↑. 기본 OFF, 단일검증.
- `ORTHO_P_VOL=70` — BREAKOUT 거래량 서지 컷(R4, 라우터 ON·EXPANSION 전용).
- `ORTHO_CHASE_K=1.0` — CONT 추격 방지(R5, 연장 추세 진입 차단). 기본 OFF.
- `ORTHO_CORR_DEDUP=true` — 동일 실행 후보를 RR 우선 정렬 후 방향 캡(R6, 상관 클러스터 완화). 기본 OFF.
- 검증은 신호의 `RG=레짐` 태그로 `ortho_report.py` **Regime·Polarity 코호트**를 보고 +기대값 국면을 확인(R7).

#### 누락(미진입) 회수 토글 (L1~L4 — "좋은 순간을 놓침" 전수점검 결과)
스캘핑 false negative를 과적합 없이 메우는 보강. 자기정규화·롱숏 대칭, **기본값=현행 보존**.
- **L1 (코드 반영, 항상 ON):** CONT 눌림 밴드 바닥 `P_EXT→0`. 라우터가 TREND에서 REV를 막는 동안
  **강추세 깊은 눌림(`L_pct<10`)**이 어떤 폴라리티에도 안 걸리던 사각지대 해소(FLOW·구조 AND 가드 유지).
- `ORTHO_ROUTER_MODE=SOFT` — ER 모호구간(`|ER−TREND_ER|≤ORTHO_ROUTER_SOFT_ER`)에서만 REV·CONT 둘 다
  평가(경계 깜빡임 누락 회수). 기본 STRICT=현행. 단일변수 A/B.
- `ORTHO_BREAKOUT_RANGE=true` — BREAKOUT에 신선 W_F 레인지 돌파 트리거 OR 추가(EXPANSION 신호 부활).
- `ORTHO_N_5M_FETCH=72` / `ORTHO_FLOW_TAKER_CONFIRM=true` — 흐름축 표본 확대 + taker CVD 확인 재사용.
- **배선(W2):** 위 노브 전체가 워크플로 env에 연결됨 — GitHub Variables가 실제로 런타임에 반영된다.

#### 분류기 지연제거 토글 (M1 — false-positive 차단, 263건 실현 R 검증 결과)
실현 R 분석에서 `counter-trend`(4h 레그 역행) ExpR −0.20R·PF 0.58, 70/30 양 구간 음 — 특히 `상승장 숏`
한 코호트가 −19.8R(전체 순손실 초과). 6/23 롱 5전패도 4h EMA '교차'가 하락전환을 못 따라잡은 지연 탓.
- `ORTHO_MACRO_FRESH` (★ 기본 ON) — 상위TF(1h·4h) fast-EMA **기울기**가 거래 방향과 명백히 반대면 차단(거부권).
  교차(level) 대신 기울기(slope)로 전환을 조기 포착 → 지연↓. **15m이 아닌 상위TF에만** 적용해 건강한
  눌림목은 보존. 부호만 사용·롱숏 대칭·차단 전용·신규 fetch 0. `ORTHO_MACRO_FRESH_LB`(기본 2)=기울기 룩백.
- **사전등록 검증규칙:** 라우터 코호트 n≥30에서 ① 실현 R·PF가 70/30 **양 구간 모두** 개선 ∧
  ② 롱·숏 어느 쪽도 비대칭 손상 없음. **★ 기본 ON**(데이터 근거 상시 적용)이며, 미달 확인 시 `ORTHO_MACRO_FRESH=false`로 롤백.

##### FLOW_FLOOR — 흐름-끝물 진입 차단 (★ SHORT측 기본 ON)
309건 백테스트 도입검토 결과: **`SHORT & F_pct<15` 코호트가 누적 −22.76R**(전체 순손실 −20.5R 초과),
taker fix(6/23) 이후에도 17건 −8.44R로 잔존 → taker 버그와 **독립인 별개 누수**. 워크포워드 양 구간
개선·100% SHORT 응집·"끝물 매도 추격→반등 피격" 기전으로 검증 통과(검토한 7개 안 중 유일). 대칭점
`LONG & F_pct>85`는 +1.36R(누수 아님) → 효과가 SHORT 한쪽에만 있는 **실측 비대칭**.
- `ORTHO_FLOW_FLOOR_PCT` (★ 기본 15) — SHORT 진입 시 F_pct가 이 값 미만(끝물 매도 소진)이면 차단(거부권). 0=OFF.
- `ORTHO_FLOW_CEIL_PCT` (기본 0=OFF) — LONG 미러: F_pct>100−CEIL(끝물 매수 소진)이면 차단. 데이터상 LONG측은
  누수 아니라 **증거 대기 상태로 OFF** — Shadow가 측정 후 승격 판단.
- **자기검증:** 차단된 SHORT는 `FLOW_FLOOR`로 Shadow에 적재·채점 → would-be 성과 추적. 막힌 셋업이 오히려
  이겼으면(승자 제거=FN) Shadow가 드러냄 → `ORTHO_FLOW_FLOOR_PCT=0` 한 줄 롤백. 부호 없는 자기정규화 백분위·신규 fetch 0.

#### Shadow 로깅 (FN 측정 인프라 — 별도 DB · 기본 OFF)
거부권(MACRO_FRESH·FLOW_FLOOR·crowd·taker·spread)·추격컷·리스크캡(SLOT/DIRCAP)이 막은 셋업은 그동안
`logger.info`로만 사라져 **"막아서 손해였나(승자 제거=False Negative) 이득이었나(패자 제거)"를
측정할 수 없었다.** 이를 메우는 인프라:
- 차단된 셋업을 **막힌 그 순간의 entry/TP/SL**로 **별도 Notion DB**(`NOTION_SHADOW_DB_ID`)에 적재 →
  resolver가 라이브와 **동일 triple-barrier(BE스톱 포함)**로 채점 → would-be WIN/LOSS·실현R.
- 라이브 DB와 **물리분리** → 라이브 통계 오염 0. 셋(`ORTHO_SHADOW_LOG=true`·토큰·DB id) 중
  하나라도 없으면 **완전 inert**(현행 동작 불변, 신규 fetch 0).
- **판정:** `ortho_report.py`의 **[FN 측정] 패널** — 차단 카테고리별 would-be ExpR. 막힌 코호트가
  n≥30에서 **엣지+(CI 하한>0)**면 그 게이트는 **승자를 거르는 FN 생성기 → 롤백**, **엣지−**면 정당(패자 제거).
  (taker 커밋이 1회 한 "걸러낸 expR < 남긴 expR" 검증의 **상시 제도화**.)
- **★ 기본 ON:** `ORTHO_SHADOW_LOG` 기본 `true`, `NOTION_SHADOW_DB_ID` 기본값=생성된 Shadow DB
  (`36a004b1d5b8490d921bd3ec3e980baf` = "🌑 Sig_Bot Shadow 기록"). **남은 단 하나의 수동 단계:**
  그 DB를 열어 `⋯ → 연결(Connections) → 봇 인티그레이션 추가`(라이브 DB에 연결된 것과 동일).
  연결되는 즉시 다음 cron부터 적재·채점 시작(추가 시크릿/변수 불필요).
- **재생성(선택):** `python scripts/create_shadow_db.py <PARENT_PAGE_ID>` (봇 토큰으로 실행 → 자동 쓰기권한).
- **노브:** `ORTHO_SHADOW_REASONS`(측정 카테고리, 캠페인 집중 시 `MACRO_FRESH`)·
  `ORTHO_SHADOW_MAX_PER_RUN`(런당 write 쿼터, 기본 20)·`NOTION_SHADOW_DB_ID`(다른 DB로 교체 시).

> 과적합 경계: 신규 컷(`TREND_ER`/`VOL_HI`/`P_VOL`/`CHASE_K`/`TP_REACH_K`)은 전부 **백분위·ATR·
> 스케일프리 비율 자기정규화**(절대 150%·0.5%·고정가격 금지). R1 외엔 **기본 OFF**, 변경은
> **한 번에 하나** + 70/30. ER은 [0,1] 비율이라 RR처럼 레벨을 직접 임계(백분위化 금지). 롱숏 대칭 유지.

### Phase 4 — 알림 ON → 실매매
1. 검증된 설정으로 `ALERT_ENABLED=true` → 텔레그램 알림 시작 (여전히 가상매매).
2. 알림 기준으로 1~2주 추가 관찰(라이브 신호 품질 체감).
3. 실매매 도입 시: 본 가이드 밖. 포지션 사이징·동시 슬롯(`MAX_POS_DIR`)·상관 통제 레이어를 추가하고 소액부터.

### 향후 엔진 확장 아이디어 (예산 내에서, 검증 후)
- **틱 CVD**로 흐름 축(F) 측정 정밀화 (차원 추가 아님, 측정 교체).
- **유니버스 확대**(6→15코인)로 신호 빈도↑ — 코드 무변경(`SYMBOLS`).
- veto에 **펀딩 극단** 추가 시 반드시 기존 veto 하나 제거(예산 유지).

---

## 3. 적용 행동지침 (Step-by-Step)

### Step 0. 코드 적용
- zip 압축 해제 → 기존 `src/`·`.github/workflows/` 를 **통째로 교체**(레거시 파일은 zip에 없음 = 삭제 의도).
- 최종 `src/`: `ortho_*.py` 7개 + `timeutil.py`. 그 외 .py가 남아 있으면 삭제.

### Step 1. GitHub Secrets 확인 (Settings → Secrets and variables → Actions → Secrets)
`OKX_API_KEY` `OKX_API_SECRET` `OKX_PASSPHRASE` `TELEGRAM_BOT_TOKEN` `TELEGRAM_CHAT_ID` `NOTION_TOKEN` `NOTION_DATABASE_ID` — 기존 값 재사용.

### Step 2. Variables 설정 (같은 화면 → Variables 탭)
- `ALERT_ENABLED` = `false`  ← **학습기간 핵심**
- (선택) `ORTHO_POLARITIES` = `REV,CONT`

### Step 3. Notion 준비
- 스키마는 이미 ORTHO 양식으로 전환됨. 기존 기록 삭제는 직접 진행(또는 `migrate_notion.yml`).

### Step 4. push & 가동 확인
- designated 브랜치로 push.
- Actions 탭에서 `ORTHO Signal Bot (15m)` · `ORTHO Resolver (채점 5m)` 가 도는지 확인.
- 첫 실행 로그에 `ALERT=OFF(학습)` 표시 → 정상. Notion에 OPEN 행이 쌓이기 시작.

### Step 5. 2주 관찰
- 텔레그램은 조용함(정상 — 알림 OFF). Notion DB만 채워짐.
- 1주차에 폴라리티별 신호 빈도 점검(너무 적으면 `P_EXT`/`P_FLOW` 완화 1회).

### Step 6. 분석 → 보정 → 알림 ON
- Phase 2~3 수행 후 Variables 에서 `ALERT_ENABLED=true`.
- 다음 cron부터 텔레그램 알림 시작.

### 롤백 / 안전
- 알림만 끄기: `ALERT_ENABLED=false`.
- 전체 정지: 두 워크플로 Disable.
- 파라미터 되돌리기: 해당 `ORTHO_*` Variable 삭제 → 기본값 복귀.

---

## 부록: 채점 주기

- 신호 생성 15분(`ortho_main.yml`), 채점 5분(`ortho_resolver.yml`).
- 채점이 5분인 이유: 15m 봉 내부(5m×3)에서 TP/SL 도달을 더 빨리 포착 → WIN/LOSS 라벨 지연 최소화. GitHub Actions cron은 정확도 보장은 없으나(분 단위 지연 가능) triple-barrier는 멱등이라 다음 실행에서 보정됨.
