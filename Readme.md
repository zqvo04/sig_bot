# 무빙워칭 (Moving Watching)

OKX 선물 시장 대상 암호화폐 트레이딩 시그널 봇. GitHub Actions 기반 주기 실행, Telegram 알림 출력.

**버전**: v3.3 | **대상**: BTC/USDT, ETH/USDT, HYPE/USDT, SOL/USDT, SUI/USDT | **타임프레임**: 15분봉

---

## 아키텍처

```
GitHub Actions (Matrix Job)
    │
    ├── data_pipeline.py      OKX API 데이터 수집
    ├── analysis_engine.py    기술 지표 분석 + 국면 분류
    ├── microstructure_analyzer.py  마이크로구조 분석 (6개 방안)
    ├── scoring_system.py     멀티레이어 점수 산출
    ├── notification.py       Telegram 알림 빌더
    └── main.py               단일 심볼 진입점 (Matrix 병렬화)
```

각 심볼은 독립 Job으로 병렬 실행되며 `/tmp/bot_state/`에 쿨다운 상태를 공유합니다.

---

## 점수 산출 파이프라인

```
raw_score = Σ(지표점수 × 가중치)          [국면별 가중치]
base_score = raw_score × ema_mult × gate_penalty
bonus_total = min(bonus_cap, Σ보너스)
final_score = (base_score + bonus_total) × soft_penalty + micro_penalty
signal = final_score >= regime_threshold    [국면별 임계값 단독]
```

### 소프트 패널티 체인 (soft_penalty)

| 패널티 | 배율 | 발동 조건 |
|--------|------|-----------|
| MTF RSI 과열 (강) | ×0.85 | 1h≥72 + 4h≥65 (롱) / 1h≤28 + 4h≤35 (숏) |
| MTF RSI 과열 (약) | ×0.92 | 1h≥68 (롱) / 1h≤32 (숏) |
| EXPLOSIVE 소진 | ×0.88 | EXPLOSIVE 국면 + 1h RSI≥70 (롱) / ≤30 (숏) |
| 캔들모멘텀 역방향 | ×0.80~0.90 | 연속음봉 중 롱 / 연속양봉 중 숏 |
| CHoCH 역방향 | ×0.88 | 추세 전환 경고 신호와 진입 방향 충돌 |
| BOS 역방향 | ×0.82 | 추세 구조 확증 신호와 진입 방향 충돌 |

soft_penalty = 해당 패널티 전체 곱산. 마이크로구조 패널티(micro_penalty)는 독립적으로 덧셈 적용.

---

## 시장 국면 분류 및 임계값

| 국면 | 분류 조건 | 임계값 | EMA 역방향 배율 (3/2/1/0 TF) |
|------|-----------|--------|-------------------------------|
| SQUEEZE | BB 스퀴즈 + ADX < 25 | 65pt | ×0.80 / ×0.87 / ×0.95 / ×1.00 |
| TRENDING | ADX ≥ 25 + MA20 교차 적음 | 63pt | ×0.52 / ×0.72 / ×0.88 / ×1.00 |
| RANGING | MA20 교차 ≥ 2 또는 ER < 0.15 | 62pt | ×0.82 / ×0.90 / ×0.96 / ×1.00 |
| EXPLOSIVE | ADX ≥ 40 + BB 확장 | 65pt | ×0.75 / ×0.84 / ×0.93 / ×1.00 |

> **v3.3 변경**: SIGNAL_MIN_SCORE(63) 제거. 기존에는 모든 국면 실질 임계값이 63pt로 수렴했음. 이제 국면별 임계값이 실제로 작동.

---

## 국면별 가중치

| 지표 | RANGING | TRENDING | EXPLOSIVE | SQUEEZE |
|------|---------|----------|-----------|---------|
| RSI | 0.32 | 0.11 | 0.07 | 0.15 |
| 볼린저밴드 | 0.26 | 0.09 | 0.06 | 0.35 |
| 펀딩비 | 0.13 | 0.15 | 0.15 | 0.13 |
| 롱숏비율 | 0.12 | 0.22 | 0.24 | 0.13 |
| Taker | 0.10 | 0.34 | 0.38 | 0.19 |
| 거래량 | 0.07 | 0.09 | 0.10 | 0.05 |

거래량 점수는 `1.0x(평균) = 50pt` 기준으로 정규화. 다른 지표와 동일한 중립 기준 적용.

---

## 게이팅

신호 발화 이전 펀딩비·롱숏비율 조건 검사.

| 조건 | 배율 |
|------|------|
| 펀딩비 또는 롱숏 중 하나만 역풍 | ×0.92 |
| 펀딩비 AND 롱숏 모두 역풍 | ×0.80 |
| 정상 | ×1.00 |

> **v3.3 변경**: 기존에는 둘 다 역풍일 때만 ×0.80. 단일 역풍 패널티(×0.92) 신규 추가.

---

## 보너스 체계

| 보너스 | 포인트 | 조건 |
|--------|--------|------|
| 눌림목 강 | +12 | 1h RSI >58(롱) / <42(숏) + 15m 과매도/과매수 + EMA 2TF 이상 |
| 눌림목 약 | +8 | 1h RSI >52(롱) / <48(숏) + 15m 눌림 + EMA 2TF 이상 |
| 눌림목 미세 | +4 | 1h RSI 최소조건 + 15m 소폭 + EMA 1TF 이상 |
| 추세 지속 (EMA+Taker) | +12 | EMA 3TF 일치 + Taker 강/중 일치 |
| 돌파/붕괴 실패 | +12 | 최근 고점 돌파 후 되돌림 / 저점 붕괴 후 반등 |
| 피보 황금포켓 | +10 | 61.8~65% 되돌림 구간 |
| Post-Squeeze 모멘텀 | +10 | 이전 국면 SQUEEZE + BB 첫 돌파 캔들 |
| 극단 멀티TF 과매도/과매수 | +10 | 15m/1h/4h RSI 동시 극단 + BB 극단 |
| 거래량-가격 다이버전스 | +10 | 신저가+거래량급증 (롱) / 신고가+거래량감소 (숏) |
| 대규모 청산 | +10 | 캔들 꼬리+거래량 기반 청산 프록시 감지 |
| BOS 확증 (방향 일치) | +8 | 스윙 고점/저점 돌파 확증 + 진입 방향 일치 |
| FVG 진입 | +8 | Fair Value Gap 내부 진입 |
| 볼린저 극단 + RSI 다이버전스 | +8 | BB 극단 + RSI 다이버전스 동시 |
| 시장 구조 (LowerHigh/HigherLow) | +8 | 스윙 고점 하락 / 저점 상승 구조 |
| 캔들 핀바 | +10 | 긴 꼬리 역전형 캔들 패턴 |
| 캔들 인걸핑 | +8 | 전캔들 포용형 역전 패턴 |
| 히든 다이버전스 | +6 | 추세 지속형 RSI 히든 다이버전스 |
| 거래량 폭발 | +7 | 2.5x 이상 거래량 + ADX ≥ 22 |
| 펀딩비+롱숏 동방향 | +6 | 두 심리 지표 동시 진입 방향 유리 |
| 피보 주요레벨 | +5 | 38.2 / 50 / 78.6% 근접 |
| FVG 진입 (방향 모호) | +4 | 강세+약세 FVG 동시 활성 |

**보너스 캡 (티어드):**

| base_score | 보너스 상한 |
|------------|------------|
| < 36pt | 18pt |
| 36~44pt | 26pt |
| ≥ 44pt | 36pt |

### 보너스 억제 조건

| 조건 | 억제 대상 | 효과 |
|------|-----------|------|
| EMA 3TF 역방향 + BB 극단 아님 | 거래량·다이버전스 보너스 | ×0.25 |
| Taker 역방향 | 캔들 패턴 보너스 | ×0.40 |
| EXPLOSIVE 소진 패널티 중 | 추세 확인형 보너스 | 제거 |
| **거래량 < 평균의 30%** | **구조·다이버전스 보너스** | **×0.50** |

> **v3.3 신규**: 저유동성 구조 패턴 보너스 억제. 거래량이 평균의 30% 미만이면 LowerHigh구조, HigherLow구조, 돌파실패, 붕괴실패, 거래량다이버전스, 볼린저극단+RSI다이버전스를 50%로 감산. 눌림목·FVG·BOS·피보·펀딩/LS는 억제 제외.

---

## 마이크로구조 분석 (6개 방안)

`microstructure_analyzer.py` — 별도 API 호출로 추가 order flow 데이터 수집.

| 방안 | 이름 | 데이터 소스 | 최대 패널티 | 최대 보너스 |
|------|------|-------------|-------------|-------------|
| 1 | Liquidation Cascade | OKX 청산 주문 API | -15pt | +8pt |
| 2 | Order Book Wall | 호가창 (CCXT) | -12pt | — |
| 3 | OB Volume Imbalance | 호가창 잔량 비율 | -10pt | +7pt |
| 4 | Candle Momentum | 5분봉 OHLCV | -8pt | +6pt |
| 5 | Mark Price + Funding | OKX 마크가격/펀딩비 | -14pt | +5pt |
| 6 | LS Divergence | OKX 계좌 기준 LS 비율 | -10pt | +8pt |

합산 패널티 하한: **-30pt** (캡 적용).

마이크로구조 패널티는 소프트 패널티 이후 **덧셈** 방식으로 적용:
```
final_score = (base + bonus) × soft_penalty + micro_penalty
```
독립적 order flow 근거로 BOS 패널티를 partial offset/reinforce 허용 (의도적 설계).

> **v3.3 변경**: OI Velocity (방안 3) 완전 제거. OKX API 오류 지속 + 폴백 시 항상 중립 반환으로 실효성 없음. `oi_history` 키 → `ohlcv_micro` 리네임.

---

## 동적 쿨다운

같은 심볼·방향으로 연속 신호 발화 방지.

| 조건 | 쿨다운 |
|------|--------|
| 기본 | 60분 |
| 직전 신호 이후 +3% 이상 상승 (롱) | 75분 |
| 직전 신호 이후 +5% 이상 상승 (롱) | 120분 |
| 직전 신호 이후 -2.5% 이상 하락 | 0분 (즉시 리셋) |

---

## v3.x 변경 이력

### v3.3

- **SIGNAL_MIN_SCORE 제거**: 기존 63pt 하한이 국면별 임계값을 무력화하던 문제 해소. 알림에 표시되는 임계값과 실제 동작 일치.
- **거래량 스코어 정규화**: `1.0x(평균) = 50pt`. 기존 20pt 기준에서 다른 지표와 동일한 중립 기준으로 변경.
- **국면별 가중치 재분배**: 정규화에 맞게 거래량 가중치 하향, 핵심 지표(Taker/RSI/BB) 재분배.
- **Gate 단일 패널티 추가**: 펀딩비 또는 롱숏 하나만 역풍이어도 ×0.92 적용.
- **저유동성 구조 패턴 보너스 억제**: vol_ratio < 0.30 시 구조·다이버전스 보너스 50% 감산.
- **base_score 유령 계산 제거**: `base_before_soft` 별칭 제거, 단일 변수 통합.
- **보너스 재조정**: FAILED_BREAKOUT 14→12pt, BOS_CONFIRM 6→8pt.
- **OI Velocity 완전 제거**: 마이크로구조 방안 7개 → 6개, `ohlcv_micro` 키 리네임.
- **notification**: BOS 역방향 패널티 표시 추가, adx_multiplier 데드코드 제거.

### v3.2

- **BOS 역방향 패널티**: 하락 BOS 확증 중 롱 / 상승 BOS 확증 중 숏 진입 시 ×0.82.
- **쿨다운 리셋 임계값**: -1% → -2.5%. 소폭 하락으로 쿨다운 리셋되던 문제 수정.

### v3.1

- **OI 관련 섹션 제거**: API 400 오류 지속으로 전면 제거.

### v3.0

- **멀티레이어 스코어링 시스템**: 시장 국면 분류 + 국면별 가중치/임계값/EMA 배율.
- **3단계 눌림목 감지**: Strong/Weak/Micro (롱/숏 대칭).
- **Volume Explosion 보너스**: +7pt, EMA 정렬 불필요.
- **Post-Squeeze Momentum**: +10pt, 이전 국면 추적 기반.

---

## 설치 및 실행

### 요구사항

```
Python 3.10+
ccxt
pandas
numpy
requests
```

### 환경변수

```
OKX_API_KEY
OKX_API_SECRET
OKX_PASSPHRASE
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

### GitHub Actions 설정

```yaml
jobs:
  signal:
    strategy:
      matrix:
        symbol: ["BTC/USDT", "ETH/USDT", "HYPE/USDT", "SOL/USDT", "SUI/USDT"]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install ccxt pandas numpy requests
      - run: python src/main.py
        env:
          SINGLE_SYMBOL: ${{ matrix.symbol }}
          OKX_API_KEY: ${{ secrets.OKX_API_KEY }}
          OKX_API_SECRET: ${{ secrets.OKX_API_SECRET }}
          OKX_PASSPHRASE: ${{ secrets.OKX_PASSPHRASE }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
```

### 로컬 실행

```bash
SINGLE_SYMBOL="BTC/USDT" python src/main.py
```

---

## 신호 등급

| 등급 | 점수 | 설명 |
|------|------|------|
| 🔥🔥 STRONG | ≥ 85pt | 매우 강한 신호 — 즉시 대응 권장 |
| 🔥 GOOD | ≥ 72pt | 좋은 신호 — 표준 진입 |
| 📊 WATCH | ≥ 임계값 | 기준 통과 — 확인 후 진입 |

마이크로구조 경고(패널티 ≤ -10pt)가 존재하면 STRONG/GOOD 등급에 ⚠️ 표시.

---

## 주의사항

본 시스템은 참고용 신호 생성 도구입니다. 모든 투자 결정과 그에 따른 손익은 사용자 본인의 책임입니다. 자동 주문 실행 전 반드시 백테스팅과 수익성 검증이 선행되어야 합니다.
