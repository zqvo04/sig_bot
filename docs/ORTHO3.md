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
