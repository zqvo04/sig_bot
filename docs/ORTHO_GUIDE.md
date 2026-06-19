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

### Phase 3 — 진입기준 보정 (단일 변수 · 워크포워드)
분석 결과로 **한 번에 하나씩** 조정 (환경변수 오버라이드, 코드 수정 불필요):
- 약한 폴라리티 제거: `ORTHO_POLARITIES=REV` (또는 CONT)
- 극단 강화: `ORTHO_P_EXT=7` (신호↓ 질↑) / 완화: `12`
- 흐름 엄격화: `ORTHO_P_FLOW=25`
- 손익비 하한: `ORTHO_RR_MIN=1.2`
- 위치 윈도우: `ORTHO_W_L` (48~96)

각 변경은 **앞 70% 학습 / 뒤 30% 검증**으로 효과가 유지되는지 확인. 표본 전체에 핏하지 말 것.

#### 구조 보정 토글 (R1·R2 — 신호기록 분석 기반, 기본 OFF)
1차 분석에서 **실현 −0.14R**(역행 숏·캡처 52%·RR≥3 손실)이 드러남. 진입 셋을 재작성하지 않고
국면 라우팅·도달가능 TP로 누수를 막는다(둘 다 자기정규화 → 과적합 표면 아님, 롱숏 대칭):
- `ORTHO_REGIME_ROUTER=true` — RANGE→REV / TREND→CONT / EXPANSION→CONT. 한 번에 이것만 켜고 70/30 검증.
- `ORTHO_TP_REACH_K=1.2` — TP를 `K·ATR·√T_MAX` 도달거리로 상한. 캡처효율↑. 별도 단일검증.
- 검증은 신호의 `RG=레짐` 태그로 `ortho_report.py` **Regime·Polarity 코호트**를 보고 +기대값 국면을 확인(R7).

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
