# OKX 팩트 시트 (조회일 2026-11-03, 컨테이너에서 curl 직접 호출)
출처 표기: [API]=실제 응답 수신, [DOC]=https://www.okx.com/docs-v5/en/ 직접 수신·파싱, [WEB]=WebSearch 결과(2차), [PAGE]=okx.com 페이지 직접 수신

## 1. 최초 캔들 (GET /api/v5/market/history-candles, `after` 이분탐색 → 최소 after에서 반환된 1개 봉) [API]
| 심볼 | 5m | 15m | 1H | 4H | listTime(UTC) |
|---|---|---|---|---|---|
| BTC | 2019-12-16 06:05 | 2019-12-16 06:00 | 2019-12-16 06:00 | 2019-12-16 04:00 | 2019-11-12 11:16 |
| ETH | 2019-12-25 00:00 | 2019-12-25 00:00 | 2019-12-25 00:00 | 2019-12-25 00:00 | 2019-11-12 11:16 |
| SOL | 2021-01-22 06:55 | 2021-01-22 06:45 | 2021-01-22 06:00 | 2021-01-22 04:00 | 2021-01-22 07:00 |
| XRP | 2019-12-31 00:00 | 2019-12-31 00:00 | 2019-12-31 00:00 | 2019-12-31 00:00 | 2019-11-12 11:16 |
| SUI | 2023-05-05 03:55 | 2023-05-05 03:45 | 2023-05-05 03:00 | 2023-05-05 00:00 | 2023-05-05 03:55 |
| HYPE | 2025-02-21 07:00 | 2025-02-21 07:00 | 2025-02-21 07:00 | 2025-02-21 04:00 | 2025-02-21 07:00 |
- 응답 필드: [ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm], 최신→과거 순, limit 최대 100 [API].
- BTC/ETH/XRP: listTime(2019-11-12)보다 약 1~7주 뒤부터 캔들 시작 (캔들 존재 ≠ 상장시각). SOL/SUI/HYPE는 상장 직후부터.
- **갭 검증 불가(미수행)**: 전체 페이지네이션을 하지 않았으므로 중간 결측봉 유무는 확인 안 함. 이분탐색은 "after=t에 데이터 존재" 단조성 가정(중간 대형 갭이 있으면 오탐 가능). 백테스트 전 전체 다운로드 후 연속성 검사 필요.
- 레이트리밋 [DOC]: docs-v5에서 `Rate Limit: 20 requests per 2 seconds` 문구 다수 확인(예: sprd-history-candles는 IP 기준 20/2s). market/history-candles 개별 수치는 별도 추출 못함 → 확인 불가(20/2s는 유사 엔드포인트 값). 내 호출(≈8 req/s, ~900회)에서 50011(rate limit) 응답은 관찰되지 않음(코드상 재시도 처리는 했으나 로그 안 남김 → "관찰 없음" 수준). 응답 헤더에 ratelimit 헤더 없음 [API].

## 2. 펀딩 [API]+[DOC]
| 심볼 | 현재 간격(fundingTime→next) | history 건수 | 최초 fundingTime | 최종 | 간격 분포 | 간격 변화 | min/max FundingRate |
|---|---|---|---|---|---|---|---|
| BTC | 8h | 276 | 2026-08-03 16:00 | 2026-11-03 08:00 | 8h×275 | 없음 | ±0.00375 |
| ETH | 8h | 276 | 동일 | 동일 | 8h×275 | 없음 | ±0.0075 |
| HYPE | 8h | 276 | 동일 | 동일 | 8h×275 | 없음 | ±0.01 |
| SOL | 8h | 276 | 동일 | 동일 | 8h×275 | 없음 | ±0.0075 |
| SUI | 8h | 276 | 동일 | 동일 | 8h×275 | 없음 | ±0.01 |
| XRP | 8h | 276 | 동일 | 동일 | 8h×275 | 없음 | ±0.0075 |
- 정산시각 00/08/16 UTC. 현재 fundingRate 샘플: BTC/ETH/HYPE/SUI/XRP=0.0001(=인터레스트 하한 근처 기본값), SOL=-0.00000204 [API]. method=current_period, nextFundingRate 공란.
- **history는 약 3개월(276건)만 제공**: docs "This endpoint can return data up to three months", 레이트 10 req/2s (IP+InstID) [DOC]. 실측 276건 = 92일 ⇒ 일치. 그 이전 8h→4h 변경 여부는 이 API로 확인 불가(관측 구간 내 변화 없음).
- 장기 펀딩은 OKX 다운로드 페이지 "Funding rate: 2022-03 이후" [PAGE] (아래 4번).

## 3. 수수료
- https://www.okx.com/fees 는 프록시 지역 때문에 **OKX United States 페이지**로 서빙됨(title "OKX United States"). 내장 JSON은 Spot 탭만 존재(Regular user Lv0 spot maker 0.002/taker 0.0035) [PAGE]. **USDT perp 요율은 해당 페이지에 없음 → 글로벌 공식 요율 확인 불가 (사용자 확인 필요).** 위 US spot 수치는 perp와 무관(사용 금지).
- 2차 출처(미검증) [WEB]: 일반 Lv1 USDT-perp maker 0.02% / taker 0.05% (복수 제3자 글 + okx-api.aperture.finance 헬프 미러가 "lv1 taker 0.05%, maker 0.02%" 기재). 이상치로 taker 0.08% 주장 글 1건 있음(신뢰 낮음). → **라벨: 미검증**.
- `GET /api/v5/account/trade-fee` 는 인증 필요: 무인증 호출 시 `{"code":"50103","msg":"Request header OK-ACCESS-KEY can not be empty."}` [API]. 인증 시도 안 함.
- 왕복(진입+청산) 가정치(미검증 요율 기준): taker/taker 10bp, maker/maker 4bp, 슬리피지 별도.

## 4. 호가/스프레드 이력
- 공개 REST: `/market/books`는 **현재 스냅샷만**. 과거 호가/스프레드 이력 엔드포인트 없음(docs-v5에서 orderbook-history 류 문자열 0건 [DOC]; 서드파티 문서도 동일 주장 [WEB]). 과거 BBO 스프레드는 REST로 복원 불가.
- OKX 공식 다운로드 페이지: `/data-download`는 404 [API]; 실제는 `https://www.okx.com/historical-data` → 302 → `/en-us/historical-data` [PAGE]. 페이지 문구: Trade history "Tick-level... from September 2021", Candlestick "from July 2023", Funding rate "perpetual funding rates from March 2022", Order book "High-resolution L2 data from March 2023", Borrowing rate "from December 2021" [PAGE]. **심볼별 커버리지/SWAP 포함 여부/파일 포맷/크기는 미확인**(다운로드 링크 미접근, JS 렌더). 호가 L2는 2023-03 이후만 → 2019~2023-03 구간 스프레드는 OKX 공식으로도 없음.
- 제3자 [WEB]: Tardis.dev(OKX tick-level, 유료; 매월 1일 CSV 무료), CryptoHFTData(OKX 호가 2025-06-28~). 미검증.
- 현재 스냅샷(sz=1, 3회×5s, 2026-11-03 ~15:18 UTC, **단일 시점·비대표**) [API]:
| 심볼 | bid / ask (3회 중 대표) | 스프레드 bps (3회 모두 동일) |
|---|---|---|
| BTC | 77320.0 / 77320.1 | 0.013 |
| ETH | 2286.65 / 2286.66 | 0.044 |
| HYPE | 86.429 / 86.430 | 0.116 |
| SOL | 93.11 / 93.12 | 1.074 |
| SUI | 0.8948 / 0.8949 | 1.118 |
| XRP | 1.2732 / 1.2733 | 0.785 |
  → 6심볼 × 3회 전부 스프레드 = 정확히 1틱(최소 스프레드로 고정). 스프레드 하한=틱 바닥, 실제 비용은 깊이/체결 슬리피지가 지배(sz=1 최우선호가만 봄, 깊이 미측정).

## 5. 계약 스펙 [API] (instruments?instType=SWAP)
| 심볼 | tickSz | ctVal | lotSz/minSz | ctType/settle | 마지막가(ticker) | tick bps(=tickSz/last) |
|---|---|---|---|---|---|---|
| BTC | 0.1 | 0.01 BTC | 0.01 | linear/USDT | 77284.9 | 0.0129 |
| ETH | 0.01 | 0.1 ETH | 0.01 | linear/USDT | 2285.21 | 0.0438 |
| HYPE | 0.001 | 0.1 HYPE | 1 | linear/USDT | 86.334 | 0.1158 |
| SOL | 0.01 | 1 SOL | 0.01 | linear/USDT | 93.08 | 1.074 |
| SUI | 0.0001 | 1 SUI | 1 | linear/USDT | 0.8946 | 1.118 |
| XRP | 0.0001 | 100 XRP | 0.01 | linear/USDT | 1.2734 | 0.785 |
- state 전부 live. listTime은 1번 표 참조. 가격이 변하면 tick bps도 변함(현재 가격 기준 값).

## 확정 사실
- 15m/5m/1H/4H 최초 캔들 일자(표 1), 6심볼 전부 live linear USDT-SWAP.
- 펀딩 8h 고정(관측 구간), history API는 최근 92일/276건만, 구간 내 간격 변화 없음.
- 호가 이력은 공개 REST에 없음; 공식 다운로드 페이지 존재(L2 2023-03~, 펀딩 2022-03~, 틱체결 2021-09~, 캔들 2023-07~ 라고 페이지에 명시).
- trade-fee는 인증 필요(50103). 현재 스프레드는 6심볼 모두 1틱, tick bps 표.

## 미검증/확인 불가
- USDT-perp VIP0 maker/taker: 공식 확인 불가(/fees가 US 페이지로 서빙). 0.02%/0.05%는 2차 출처, 미검증.
- 캔들 중간 갭 여부(전체 다운로드 미수행); market/history-candles 고유 레이트리밋 수치.
- 2026-08-03 이전 펀딩(8h→4h 변경 이력) 및 다운로드 파일의 심볼·SWAP 커버리지/포맷.
- 스프레드의 시간대별/변동성별 분포, 호가 깊이(sz=1 스냅샷만).
