# ORTHO-3 — 직교 3축 합의 엔진 (설계 + 구현)

레거시 만능 스코어러(가법 6팩터 + 59 노브, 207 상수)를 대체하는 의사결정 엔진.
**점수를 더하지 않는다.** 세 직교축이 모두 동의(AND)하고 어떤 거부권(VETO)도
없을 때만 신호. 모든 임계는 그 코인의 최근 자기 분포 백분위 → 매크로 과적합 차단.

## 진단 요약 (왜 갈아엎나)
- `rsi`≈`bollinger` 상관 **0.87**, 61% 완전 동일 → 같은 축 이중계산.
- `funding/LS/taker`는 신호 집단에서 거의 상수(포화) → 노이즈 가중치.
- 유효 차원 2~3인데 파라미터 207개 → 차원 대비 과매개변수화(과적합 본체).
- 가법 합산은 **지연-선행 다이버전스를 상쇄**(거부권 표현 불가) → 칼날잡기(RSI<30 숏 31%).

## 3축 정의
| 축 | 측정 | 자기정규화 | 흡수한 레거시 |
|---|---|---|---|
| **L 위치** | (close−SMA)/ATR 의 W_L 분포 백분위 | `L_pct` | RSI·BB·과매도 보너스 |
| **F 흐름** | CVD 프록시(캔들모멘텀) 슬라이딩 백분위 | `F_pct` | taker·candle_mom·volume |
| **S 구조** | 다TF EMA 정렬수 + 신선 돌파 | 이진 판정 | BOS/CHoCH/EMA배율/레짐분류 |

**VETO(차단 전용):** 군중 과밀(LS) · Taker 역방향 · 호가 스프레드. (점수 가산 영구 금지)

## 두 폴라리티 (3단계 A/B 대상)
- **REV 회귀형:** L=극단 ∧ F=반전 ∧ ¬S_broken → 평균(SMA) 회귀. SL=흐름창 극단±buf.
- **CONT 연속형:** L=눌림(중립대) ∧ F=동조 ∧ S=정렬 → 직전 스윙(측정이동). SL=눌림 극단±buf.
- RR < `RR_MIN` 이면 진입 스킵(조정이 아니라 선별). 타임스톱 `T_MAX`=7봉(1h45m).

## 파라미터 대장 (12개 하드캡, 실질 튜닝 3개: W_L·P_EXT·P_FLOW)
`ortho_config.py` 참조. 전부 `ORTHO_*` 환경변수로 오버라이드 가능.

## 리스크·집행 레이어 (진입 결정과 분리 — 과적합 표면 아님)
12개 진입 파라미터는 "어떤 신호를 낼지"를 정한다(곡선맞춤 위험의 본체). 아래 5개는
**같은 신호를 어떻게 사이징·청산·게이팅·기록할지**만 바꾼다. 진입 셋을 건드리지 않고
전부 자기정규화(R·비율)라 엔트리 과적합 표면을 늘리지 않는다.

| 키 | 기본 | 효과 |
|---|---|---|
| `RISK_PER_TRADE` | 100 | **C-1 등가-R 사이징.** SL 거리(=1R)로 수량 역산 → 모든 신호 동일 금액 위험. 평가·기록도 R 우선(실현 R·청산사유를 Note에 기록). |
| `BE_TRIGGER_R` / `BE_LOCK_R` | 1.0 / 0.05 | **A-1 본전스톱.** +1R 도달 시 손절을 진입가(+0.05R)로 이동. 되돌림 풀손실 방지·수수료 버퍼. 무장은 봉 끝(다음 봉부터 적용). |
| `MAX_CONCURRENT_DIR` | 3 | **A-3 포트폴리오 방향 캡.** 전 심볼 통틀어 동시 동일방향 한도. 상관 바스켓이 함께 무너지는 손실 클러스터 차단. |
| `RR_MAX` | 3.0 | **A-4 TP 상한.** 타임스톱(2h) 안에 닿을 거리로 먼 목표를 당겨 TP·청산 정합. SL=구조 그대로(리스크 불변). |

`scripts/ortho_report.py` — Notion CSV를 R-기준으로 재집계(기대값·캡처효율·코호트·클러스터링).

## 파일 구성 (ORTHO 단독 · 레거시 제거됨)
| 파일 | 역할 |
|---|---|
| `src/ortho_config.py` | 12 파라미터 + `ALERT_ENABLED`(텔레그램 ON/OFF) + 키 |
| `src/ortho_data.py` | OKX 수집 (캔들/ls/taker/스프레드) — 자립형 |
| `src/ortho_engine.py` | L/F/S 계산·진리표·거부권·구조 배리어 (순수, 무상태) |
| `src/ortho_notify.py` | 텔레그램 ON/OFF |
| `src/ortho_notion.py` | Notion 기록/조회/판정 (기존 DB ORTHO 양식) |
| `src/ortho_resolver.py` | 채점 (triple-barrier, 자립형) |
| `src/ortho_main.py` | 진입점 (15분) |
| `scripts/migrate_notion_to_ortho.py` | Notion 양식 전환·삭제 (1회) |
| `.github/workflows/ortho_main.yml` | 신호 생성 cron */15 |
| `.github/workflows/ortho_resolver.yml` | 채점 cron */5 |

## 동작 (텔레그램 `ALERT_ENABLED` Variable)
- `false`(기본·학습기간): 알림 OFF — Notion 기록·채점만.
- `true`: 알림 ON — 신규 신호 텔레그램 발송. 기록·채점은 항상 동작.

## 발전·검증 (요약)
- 2주 학습(알림 OFF) → 폴라리티당 30~60건 해소 표본 축적.
- 코호트 분석(Polarity·L_pct·F_pct·MacroTag·Symbol)으로 +기대값 셋업 식별.
- 단일 변수 보정 + 70/30 워크포워드. 파라미터 12개 하드캡 유지(peek-and-tune 금지).
- 상세: `docs/ORTHO_GUIDE.md`
