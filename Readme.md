# 무빙워칭 (Moving Watching)

> OKX 선물 시장 대상 암호화폐 트레이딩 시그널 봇  
> GitHub Actions 자동 실행 → 텔레그램 알림

---

## 목차

1. [개요](#1-개요)
2. [시스템 아키텍처](#2-시스템-아키텍처)
3. [점수 산출 공식](#3-점수-산출-공식)
4. [패널티 체계](#4-패널티-체계)
5. [보너스 체계](#5-보너스-체계)
6. [국면 분류 시스템](#6-국면-분류-시스템)
7. [쿨다운 시스템](#7-쿨다운-시스템)
8. [신호 발화 조건](#8-신호-발화-조건)
9. [핵심 파라미터](#9-핵심-파라미터)
10. [설치 및 배포](#10-설치-및-배포)
11. [설계 원칙 및 한계](#11-설계-원칙-및-한계)

---

## 1. 개요

| 항목 | 내용 |
|------|------|
| 모니터링 종목 | BTC/USDT · ETH/USDT · HYPE/USDT |
| 타임프레임 | 15분봉 (분석) + 1h/4h (컨텍스트) |
| 거래소 | OKX 선물 (USDT-Margined Perpetual) |
| 실행 방식 | GitHub Actions (cron-job.org 외부 트리거) |
| 알림 | Telegram Bot |
| 언어 | Python 3.11 |

---

## 2. 시스템 아키텍처

```
cron-job.org (15분 주기)
    │
    ▼
GitHub Actions
    │
    ├─ Restore bot state  ← artifact (쿨다운 상태 복원)
    │
    ├─ src/main.py
    │     ├─ data_pipeline.py    데이터 수집 (OKX API)
    │     ├─ analysis_engine.py  기술 지표 분석
    │     ├─ scoring_system.py   점수 산출 + 신호 판정
    │     └─ notification.py     텔레그램 발송
    │
    └─ Save bot state     ← artifact (쿨다운 상태 저장)
```

### 파일 구조

```
.
├── src/
│   ├── main.py              진입점, 루프 제어
│   ├── config.py            전체 파라미터 관리
│   ├── data_pipeline.py     OKX OHLCV/펀딩비/Taker/OI/LS 수집
│   ├── analysis_engine.py   RSI, BB, EMA, ADX, 펀딩비, LS, OI, Taker,
│   │                        청산프록시, ATR, 국면분류, 캔들패턴,
│   │                        시장구조, 거래량다이버전스
│   ├── scoring_system.py    점수 산출, 패널티/보너스, 쿨다운, 신호 판정
│   └── notification.py      텔레그램 메시지 조립 및 발송
├── .github/
│   └── workflows/
│       └── main.yml         GitHub Actions 워크플로
└── requirements.txt
```

---

## 3. 점수 산출 공식

### 최종 공식

```
final = (raw × EMA × ADX × gate + bonus) × MTF × exhaustion × candle_momentum
         ↑____방향성 패널티____↑              ↑_________소프트 패널티_________↑
       보너스 계산 전 적용                   (base+bonus) 전체에 적용
```

> **방향성 패널티** — "신호 방향 자체가 의심스럽다" → raw에만 적용, 보너스는 독립 유효  
> **소프트 패널티** — "진입 시점/상태 자체가 위험하다" → 보너스도 함께 신뢰도 하락

### 7개 기본 지표 가중치

| 지표 | TRENDING | RANGING | EXPLOSIVE |
|------|----------|---------|-----------|
| RSI (멀티TF) | 11% | 27% | 7% |
| 볼린저밴드 | 9% | 22% | 6% |
| 펀딩비 | 15% | 14% | 15% |
| 롱숏비율 | 15% | 13% | 17% |
| Taker Volume | 21% | 11% | 23% |
| OI 변화율 | 18% | 5% | 19% |
| 거래량 | 11% | 8% | 13% |

> TRENDING: 포지션·체결 데이터 중심 / RANGING: RSI·BB 반전 중심 / EXPLOSIVE: 실시간 포지션 데이터 중심

### 롱숏 비율 국면별 해석

| 국면 | 해석 방식 | 근거 |
|------|----------|------|
| TRENDING | Trend-follow (숏 많음 → 숏 추세 확인) | 포지션 쏠림 = 추세 방향 확인 |
| RANGING/EXPLOSIVE | Contrarian (롱 과열 → 숏 신호) | 극단적 쏠림 = 청산 위험 반전 |

---

## 4. 패널티 체계

### 방향성 패널티 (raw에만 적용)

#### EMA 역방향 배율 (국면별 차등)

| 역방향 TF 수 | TRENDING/기본 | RANGING | EXPLOSIVE |
|-------------|--------------|---------|-----------|
| 0개 | ×1.00 | ×1.00 | ×1.00 |
| 1개 | ×0.85 | ×0.93 | ×0.93 |
| 2개 | ×0.65 | ×0.83 | ×0.84 |
| 3개 | ×0.40 | ×0.72 | ×0.75 |

> RANGING/EXPLOSIVE: EMA 부분 역방향도 허용 (횡보/급등락에서 EMA 미정렬은 자연스러움)

#### ADX 횡보 억제 배율

| ADX 구간 | 배율 | 비고 |
|---------|------|------|
| < 20 | ×0.70 | 추세 없음 |
| 20~25 | ×0.85 | 약한 추세 |
| 25+ | ×1.00 | 정상 |

> **RANGING + BB 극단 이탈 보정**: ADX가 낮아도 BB 하/상단 이탈 반전 구간은 ×0.70 → ×0.85 완화

#### Gate 복합 패널티 (×0.80)
펀딩비 **AND** LS비율 **둘 다** 신호 방향에 불리할 때 적용

---

### 소프트 패널티 ((base+bonus) 전체에 적용)

#### C: MTF RSI 극단값 패널티

| 조건 | 배율 | 비고 |
|------|------|------|
| 1h RSI ≤ 24 단독 (숏 방향) | ×0.85 | 극단 과매도 → 숏 반등 위험 |
| 1h RSI ≥ 76 단독 (롱 방향) | ×0.85 | 극단 과매수 → 롱 소진 위험 |
| 1h+4h RSI 모두 극단 | ×0.85 | 강 패널티 |
| 1h RSI만 경계 (≤32/≥68) | ×0.92 | 약 패널티 |

#### D-alt: EXPLOSIVE 소진 패널티 (×0.88)
EXPLOSIVE 국면에서 1h RSI ≤ 30 (숏) 또는 ≥ 70 (롱)일 때  
→ 소진 상태에서 LowerHigh·연속음봉 등 추세확인형 보너스 추가 제거

#### 연속캔들 모멘텀 패널티

| 조건 | 국면 | 배율 |
|------|------|------|
| 연속양봉3 중 숏 진입 | RANGING/SQUEEZE | ×0.75 |
| 연속양봉3 중 숏 진입 | EXPLOSIVE | ×0.80 |
| 연속양봉3 중 숏 진입 | TRENDING | ×0.87 |
| 연속음봉3 중 롱 진입 | (동일 대칭) | (동일) |

> **BB 하단 이탈 면제**: 연속음봉3 + 롱이어도 BB lower_breakout 구간은 페널티 없음  
> (낙폭 과대 반전 = 연속음봉이 오히려 과매도 확인 신호)

---

## 5. 보너스 체계

**보너스 캡: 35pt** (소프트 패널티 적용 전 기준)

### 보너스 목록

| 보너스 | 롱 조건 | 숏 조건 | 점수 |
|--------|---------|---------|------|
| BB극단+RSI다이버전스 | BB 하단 + 강세 다이버전스 | BB 상단 + 약세 다이버전스 | +10 |
| 눌림목 강 (1h+15m) | 1h RSI>58 + 15m RSI<40 | 1h RSI<42 + 15m RSI>60 | +14 |
| 눌림목 약 | 1h RSI>50 + 15m RSI<46 | 1h RSI<50 + 15m RSI>54 | +9 |
| 눌림목 미세 | 1h RSI>45 + 15m RSI<50 | 1h RSI<55 + 15m RSI>50 | +5 |
| Volume Explosion | 거래량 3x+ (EMA 정렬 불필요) | 동일 | +7 |
| Post-Squeeze 돌파 | 이전 SQUEEZE → 현재 방향 돌파 | 동일 | +10 |
| OI+Taker 일치 | OI 증가+가격상승+Taker 매수 | OI 증가+가격하락+Taker 매도 | +7 |
| 추세전환경고 | 1h RSI≤28 + Taker 매수 + Funding 음수 | 1h RSI≥72 + Taker 매도 + Funding 양수 | +18 |
| LS 방향 확인 | LS bias = long_momentum/favorable | LS bias = short_momentum/favorable | +8 |
| BB RANGING 반전 | BB 하단 이탈 (RANGING 국면) | BB 상단 이탈 (RANGING 국면) | +8 |
| **캔들 핀바** | 불리시 핀바 | 베어리시 핀바 | +14 |
| **캔들 인걸핑** | 불리시 인걸핑 | 베어리시 인걸핑 | +12 |
| **연속캔들** | 연속양봉3 | 연속음봉3 | +8 |
| **시장구조 LH/HL** | Higher Low 형성 | Lower High 형성 | +10 |
| **돌파 실패** | 전저점 붕괴 실패 | 전고점 돌파 실패 | +16 |
| **거래량 다이버전스** | 신저가+거래량 증가 | 신고가+거래량 감소 | +14 |
| **펀딩 극단** | 펀딩비 ≤ -0.1% | 펀딩비 ≥ +0.1% | +12 |
| **ATR 모멘텀** | ATR > 평균 1.8배 (방향 확장) | 동일 | +8 |

> **굵게** 표시된 보너스: 트레이더 업그레이드에서 추가 (캔들/구조/거래량/펀딩극단/ATR)

---

## 6. 국면 분류 시스템

### 분류 기준

```
SQUEEZE   : BB 스퀴즈 AND ADX < 25   → 큰 움직임 대기
EXPLOSIVE : ADX ≥ 35 AND BB 확장 1.2x+ → 변동성 폭발
RANGING   : MA20 교차 ≥2회 AND ER < 0.35  → 박스권 횡보
            OR ER < 0.15 (극단 횡보)
TRENDING  : ADX ≥ 25 (위 조건 미해당)  → 추세 진행
```

**ER (Efficiency Ratio)**: 40봉 순변화량 / 40봉 총이동거리  
→ 1에 가까울수록 직선 추세, 0에 가까울수록 횡보

### 국면별 임계값

| 국면 | 임계값 | 절대 최소 | 특징 |
|------|--------|----------|------|
| SQUEEZE | 65pt | 63pt | 방향 불명확, 높은 기준 |
| TRENDING | 60pt | 63pt | 추세 추종 |
| RANGING | 61pt | 63pt | 반전 신호 중심 |
| EXPLOSIVE | 56pt | 63pt | 실시간 포지션 데이터 중심 |

> **SIGNAL_MIN_SCORE 63pt**: 국면 임계값과 무관하게 모든 신호의 절대 하한선

---

## 7. 쿨다운 시스템

### 기본 구조

신호 발화 후 **60분** 기본 쿨다운. 발화 시점의 가격을 state_file에 저장.

### 동적 쿨다운 (A2)

매 15분 실행 시 `(현재가 - 신호발화가) / 신호발화가`를 계산:

| 방향이동 | 쿨다운 | 의미 |
|---------|--------|------|
| ≥ +5% (롱 기준) | **120분** | 급등 후 추격 방지 |
| ≥ +3% | **75분** | 소폭 상승 억제 |
| ≤ -1% (역방향) | **0분 (즉시 해제)** | 방향 반전 시 재진입 허용 |
| 기타 | 60분 | 기본 쿨다운 |

> 숏 방향은 대칭 적용 (하락 시 억제, 반등 시 해제)

### State 영속화

GitHub Actions artifact로 실행 간 상태 보존:

```yaml
- name: Restore bot state
  uses: actions/cache/restore@v4
  with:
    path: /tmp/bot_state
    key: bot-state-${{ github.run_id }}
    restore-keys: bot-state-

- name: Save bot state
  if: always()
  uses: actions/cache/save@v4
  with:
    path: /tmp/bot_state
    key: bot-state-${{ github.run_id }}
```

---

## 8. 신호 발화 조건

모든 조건을 **AND**로 충족해야 신호 발화:

1. `final_score ≥ regime_threshold` (국면별 임계값)
2. `final_score ≥ SIGNAL_MIN_SCORE (63pt)` (절대 하한선)
3. `is_in_cooldown() == False` (쿨다운 해제)
4. 양방향 충돌 없음 (롱/숏 점수 차이 ≥ 5pt)

---

## 9. 핵심 파라미터

### 임계값

```python
REGIME_THRESHOLDS = {
    "SQUEEZE":   65,
    "TRENDING":  60,
    "RANGING":   61,
    "EXPLOSIVE": 56,
}
SIGNAL_MIN_SCORE = 63   # 절대 하한선
```

### 쿨다운

```python
SIGNAL_COOLDOWN_MINUTES    = 60
COOLDOWN_SUPPRESSED_STRONG = 120  # +5% 이상 이동
COOLDOWN_SUPPRESSED_MILD   =  75  # +3% 이상 이동
PRICE_MOVE_SUPPRESS_STRONG = 0.05
PRICE_MOVE_SUPPRESS_MILD   = 0.03
PRICE_MOVE_RESET_THRESHOLD = -0.01  # -1% 역방향 → 해제
```

### 소프트 패널티

```python
MTF_RSI_PENALTY_STRONG          = 0.85
MTF_RSI_PENALTY_MILD            = 0.92
MTF_RSI_OVERSOLD_1H_EXTREME     = 24   # 1h RSI ≤ 24 단독 → STRONG
MTF_RSI_OVERBOUGHT_1H_EXTREME   = 76   # 1h RSI ≥ 76 단독 → STRONG
EXPLOSIVE_EXHAUSTION_PENALTY    = 0.88

CANDLE_MOMENTUM_PENALTY_RANGING   = 0.75
CANDLE_MOMENTUM_PENALTY_EXPLOSIVE = 0.80
CANDLE_MOMENTUM_PENALTY_TRENDING  = 0.87
```

### 보너스 캡

```python
BONUS_CAP = 35  # 소프트 패널티 적용 전 최대 보너스
```

---

## 10. 설치 및 배포

### GitHub Secrets 설정

```
OKX_API_KEY
OKX_API_SECRET
OKX_PASSPHRASE
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

### 외부 트리거 (cron-job.org)

```
URL: https://api.github.com/repos/{owner}/{repo}/dispatches
Method: POST
Headers:
  Authorization: Bearer {GITHUB_TOKEN}
  Content-Type: application/json
Body: {"event_type": "run-signal-bot"}
Schedule: */15 * * * *
```

### requirements.txt 주요 패키지

```
ccxt>=4.0.0
pandas>=2.0.0
numpy>=1.24.0
requests>=2.28.0
```

---

## 11. 설계 원칙 및 한계

### 설계 원칙

1. **롱/숏 완전 대칭** — 모든 지표, 패널티, 보너스가 롱/숏 동일 기준 적용
2. **패널티 2계층** — 방향성(raw만) vs 소프트(base+bonus) 명확히 구분
3. **국면별 차등** — 추세/횡보/폭발 국면마다 지표 해석과 가중치 달리 적용
4. **보너스 순서** — 소프트 패널티 발동 시 보너스도 함께 할인 (패널티 상쇄 방지)
5. **쿨다운 영속화** — GitHub Actions 컨테이너 재시작에도 상태 유지

### 알려진 한계

| 한계 | 내용 |
|------|------|
| 지표 상관관계 | RSI/BB/Volume은 모두 가격 파생 → 독립 정보량 제한 |
| 백테스트 미검증 | 파라미터가 논리적 추론으로 설정됨, 실제 수익성 미검증 |
| 15분봉 노이즈 | 캔들 패턴이 스파이크/일시 이상으로 오감지 가능 |
| 국면 전환 순간 | TRENDING↔RANGING 전환 직후 LS 해석이 즉시 반전 |
| 시장 비대칭 | 암호화폐 구조적 상승 편향 미반영 (롱/숏 동일 기준) |
| 진입가 미제공 | 신호만 발화하며 TP/SL 수준을 계산하지 않음 |

### 권장 사용 방식

> **이 봇의 신호는 참고용**입니다.  
> 최소 1~2개월 신호 기록 후 방향 일치율을 확인한 뒤 단독 사용 여부를 결정하세요.  
> 자동 주문 실행 전에 반드시 백테스트 검증이 선행되어야 합니다.

---

*Last updated: 2026-04*
