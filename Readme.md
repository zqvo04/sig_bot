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
3. 절대 숫자 임계 금지 — 모든 컷은 백분위
4. 롱/숏 **완전 대칭**
5. 파라미터 **12개 하드캡** (레거시 207 → 12)
6. **무상태(stateless)**

### 세 직교축 + 거부권
| 축 | 측정 | 백분위 |
|---|---|---|
| **L 위치** | (close−SMA)/ATR 의 W_L 분포 | `L_pct` |
| **F 흐름** | CVD 프록시(캔들 모멘텀) 슬라이딩 | `F_pct` |
| **S 구조** | 15m/1h/4h EMA 정렬 + 신선 돌파 | 이진 |
| **VETO** | 군중 과밀(LS) · Taker 역방향 · 호가 스프레드 | 차단 전용 |

### 두 폴라리티
- **REV(회귀형):** L=극단 ∧ F=반전 ∧ ¬S_broken → 평균(SMA) 회귀
- **CONT(연속형):** L=눌림 ∧ F=동조 ∧ S=정렬 → 직전 스윙(측정이동)

RR < `RR_MIN`이면 진입 스킵. 타임스톱 `T_MAX`=7봉(1h45m).

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
├── ortho_config.py    # 12 파라미터 · ALERT_ENABLED · 키
├── ortho_data.py      # OKX 수집 (캔들/ls/taker/스프레드)
├── ortho_engine.py    # L·F·S 3축·진리표·거부권·배리어 (순수·무상태)
├── ortho_notify.py    # 텔레그램 ON/OFF
├── ortho_notion.py    # Notion 기록/조회/판정
├── ortho_resolver.py  # 채점 (triple-barrier)
├── ortho_main.py      # 진입점 (15분)
└── timeutil.py        # KST 시각

scripts/migrate_notion_to_ortho.py   # Notion 양식 전환·삭제 (1회)

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

## 12개 파라미터 (환경변수 오버라이드)

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
| `ORTHO_T_MAX` | 7 | 타임스톱 |
| `ORTHO_MAX_POS_DIR` | 2 | 방향별 슬롯 |
| `ORTHO_POLARITIES` | `REV,CONT` | 기록할 폴라리티 |

> 실질 튜닝 대상은 `W_L`·`P_EXT`·`P_FLOW` 3개. 단일 변수 원칙·워크포워드 검증.

---

## Notion 스키마 (ORTHO 양식)

`Signal` `Status`(OPEN/WIN/LOSS/TIMEOUT) `Engine`(ORTHO-REV/CONT) `Polarity`(REV/CONT)
`Symbol` `Direction`(LONG/SHORT) `Entry` `TP` `SL` `R Dist` `Bars Limit` `RR`
`L_pct` `F_pct` `S_state` `MacroTag`(UPLEG/DOWNLEG/FLAT) `Reason`
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

## 빠른 시작

1. zip 적용 후 push (기존 `src/`·workflows 통째 교체)
2. Secrets 확인 + Variable `ALERT_ENABLED=false`
3. Notion 기록 삭제(스키마는 이미 ORTHO 양식)
4. 2주 학습 → Notion 데이터 코호트 분석 → 파라미터 보정 → `ALERT_ENABLED=true`

자세한 절차·발전 방법론: [`docs/ORTHO_GUIDE.md`](docs/ORTHO_GUIDE.md)
