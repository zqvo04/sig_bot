# ORTHO-4 패치: 알고리즘 로직 변경 분석

**대상 커밋:** `07be882` — `Implement ORTHO-4 zero-cost paper ledger`

**기준 시점:** 2026-08-15 KST

**현재 전략 ID:** `ORTHO-4.SIM0`

**판정:** V4는 단순한 기록 포맷 변경이 아니다. **닫힌 봉 강제, 스프레드 VETO 제거, BE 제거**라는 세 가지가 실제 신호 또는 청산 결과를 바꾼다. 반면 L/F/S 계산, 레짐 라우터, 폴라리티 진리표, 구조 배리어 산식의 핵심은 유지된다.

> **해석 규칙:** V3와 V4의 실현 R을 하나의 표본으로 합치면 안 된다. V4에는 최소 세 개의 경제적/라벨링 변화가 동시에 들어갔으므로, V4는 새 `Strategy ID`의 독립 가상캠페인으로 취급해야 한다.

---

## 1. 한눈에 보는 변화 분류

<table header-row="true" fit-page-width="true">
	<tr>
		<td>영역</td>
		<td>변경 여부</td>
		<td>V4의 실제 효과</td>
		<td>성과 비교 영향</td>
	</tr>
	<tr>
		<td>L/F/S·레짐·폴라리티</td>
		<td>핵심 산식 유지</td>
		<td>직교 AND, RANGE/REV·TREND/CONT·EXPANSION/BREAKOUT 철학 유지</td>
		<td>직접 변화 없음</td>
	</tr>
	<tr>
		<td>입력 캔들</td>
		<td>변경</td>
		<td>형성 중 봉 제거, 닫힌 봉만 사용</td>
		<td>높음 — entry·축값·레짐·신호 시점이 달라질 수 있음</td>
	</tr>
	<tr>
		<td>스프레드 VETO</td>
		<td>변경</td>
		<td>비용 0 가정에서 스프레드가 신호를 더 이상 차단하지 않음</td>
		<td>높음 — V3에서 제외된 후보가 V4 LIVE가 될 수 있음</td>
	</tr>
	<tr>
		<td>BE 이동</td>
		<td>변경</td>
		<td>동적 BE stop 제거, 고정 TP/SL/시간 배리어만 적용</td>
		<td>높음 — 같은 entry에서도 exit·R 라벨이 달라질 수 있음</td>
	</tr>
	<tr>
		<td>Shadow 분류</td>
		<td>변경</td>
		<td>Alpha VETO, 운영 거절, 경계 탐색을 명시 분리</td>
		<td>간접 — 분석 가능한 표본이 달라짐</td>
	</tr>
	<tr>
		<td>계보·Notion 원장</td>
		<td>변경</td>
		<td>SHA, config hash, Gross/Net R 전용 필드 추가</td>
		<td>직접 알파 변화 없음; 재현성 향상</td>
	</tr>
</table>

---

## 2. 실제로 바뀐 알고리즘 로직

## 2.1 형성 중 봉 사용에서 닫힌 봉 전용으로 변경

V3는 `ORTHO_CLOSED_CANDLES`가 기본 `false`여서, 실행 시점에 형성 중인 15분·5분·1시간·4시간 봉이 L/F/S 계산과 entry에 포함될 수 있었습니다. V4에서는 `V4_ENABLED=true`이면 이 값을 무조건 `true`로 고정합니다.[1]

```text
V3 기본 경로
  현재 진행 중인 봉의 close/high/low/volume → L/F/S·레짐·entry에 사용 가능

V4 경로
  아직 닫히지 않은 마지막 봉을 모든 시간대에서 제거
  → 마지막 확정 봉의 close를 entry와 Snapshot At의 기준으로 사용
```

이 변화는 단순한 “기록 시각 보정”이 아니다. ATR, SMA, EMA, 5분 momentum, 거래량 백분위, VWAP, 레짐 분류가 모두 입력 봉 배열에 의존하므로, V4의 후보 집합은 V3와 달라질 수 있다.[2]

| 항목 | V3 기본 | V4 | 실무적 의미 |
|---|---|---|---|
| 15분 entry | 실행 순간 형성 봉 종가 가능 | 마지막 닫힌 15분 봉 종가 | 차트 재현성과 entry 가정 일관 |
| 5분 F | 진행 중 5분 candle 영향 가능 | 닫힌 5분 candle만 | 단기 흐름 깜빡임 감소 |
| 1h/4h S | 진행 중 상위TF EMA 영향 가능 | 닫힌 상위TF만 | 구조 상태의 사후 변경 방지 |
| `Signaled At` | 벽시계 fallback 가능 | 15분 봉 종료시각에 앵커 | resolver 재생 시작점 고정 |

**알고리즘적 대가:** 더 빠른 장중 반응은 포기한다. V4는 신호 확정이 15분 봉 마감까지 지연될 수 있지만, 대가로 같은 데이터에서 같은 판단을 다시 계산할 수 있다.

---

## 2.2 스프레드 VETO 제거: 후보 집합이 넓어진다

V3에서는 `spread_bps > SPREAD_MAX_BPS`이면 `context_veto()`가 `spread(...)`를 반환하고 후보가 Shadow로 밀려날 수 있었습니다. V4 비용 0 기준선에서는 이 분기를 V3 호환 모드에서만 실행하도록 제한했습니다.[2]

```text
V3
  L/F/S 통과 → spread > max → 본선 제외 / Shadow 가능

V4.SIM0
  L/F/S 통과 → spread는 본선 차단에 사용하지 않음 → 나머지 VETO만 평가
```

이는 **알파 개선이라고 주장할 수 없는 변화**다. 사용자가 정한 비용 0 가정과 일관성을 맞춘 것뿐이다. V4에서 새로 LIVE가 된 신호는 “비용·체결을 감안해도 실행 가능했다”가 아니라, “비용이 없는 가상 세계에서 다른 논리가 통과했다”는 뜻이다.

| 효과 | 방향 | 주의점 |
|---|---|---|
| LIVE 표본 수 | 증가 가능 | 넓어진 표본의 손익 개선은 비용 없는 선택 편향일 수 있음 |
| OKX 호가 조회 | V4에서 불필요하므로 생략 | 미래 `REAL_COST` 캠페인에서는 다시 필요 |
| `SPREAD` Shadow | V4 본선에서는 새로 발생하지 않음 | 기존 V3 Shadow와 V4 Shadow를 혼합하지 않음 |
| 구조·방향 논리 | 불변 | spread는 L/F/S의 일부가 아님 |

---

## 2.3 BE 이동 제거: 청산 경로가 바뀐다

V3 채점기는 MFE가 `BE_TRIGGER_R`에 도달하면 다음 5분 봉부터 stop을 entry 근처의 `BE_LOCK_R`로 옮겼습니다. V4 설정은 `BE_TRIGGER_R=0`, `BE_LOCK_R=0`으로 강제하여 이 분기를 비활성화합니다.[1] [`src/ortho_resolver.py`](../src/ortho_resolver.py)

| 청산 단계 | V3 기본 | V4.SIM0 |
|---|---|---|
| 최초 stop | 구조 SL | 구조 SL |
| MFE가 BE 기준 도달 후 | 다음 봉부터 BE stop 사용 | 계속 원래 구조 SL 사용 |
| TP/SL 동시 터치 | 보수적으로 LOSS | 동일 |
| 시간 한도 | 종가 기준 WIN/LOSS | 동일 |

**결과:** V3에서 BE로 잠겼던 작은 WIN/LOSS가 V4에서는 이후 TP·원래 SL·시간청산 중 하나로 바뀐다. 따라서 V4의 win rate, 평균 WIN R, 평균 LOSS R, MFE 대비 capture efficiency는 모두 달라질 수 있다. 이 역시 단순한 데이터 필드 변경이 아니라 **exit algorithm 변경**이다.

---

## 2.4 Alpha VETO와 운영 거절의 분리

V3에서는 차단 이유가 대부분 `Blocked By` 문자열에만 남았고, Shadow 행은 “차단된 신호”라는 넓은 의미로 묶였습니다. V4는 이를 아래처럼 분류합니다.[3]

<table header-row="true" fit-page-width="true">
	<tr>
		<td>원인</td>
		<td>V4 Stage</td>
		<td>Veto Class</td>
		<td>성과 해석</td>
	</tr>
	<tr>
		<td>통과</td>
		<td>LIVE</td>
		<td>NONE</td>
		<td>기준선 비용 0 성과</td>
	</tr>
	<tr>
		<td>MACRO_FRESH, FLOW_FLOOR, CROWD, TAKER</td>
		<td>ALPHA_SHADOW</td>
		<td>ALPHA</td>
		<td>VETO가 패자를 제거했는지, 승자를 누락했는지 검증 가능</td>
	</tr>
	<tr>
		<td>SLOT, DIRCAP 및 향후 실행 제약</td>
		<td>EXEC_REJECT</td>
		<td>EXECUTION</td>
		<td>포트폴리오·운영 제약의 기회비용; Alpha VETO와 직접 비교 금지</td>
	</tr>
	<tr>
		<td>EXPLORE:DROP_L/F/S</td>
		<td>APERTURE</td>
		<td>APERTURE</td>
		<td>다음 사전등록 가설의 탐색 데이터; 즉시 본선 승격 금지</td>
	</tr>
</table>

이 변화는 당장 본선 진입 여부를 바꾸는 경우가 제한적이다. `SLOT`과 `DIRCAP`은 이전에도 차단됐으며, 이제 그 차단이 어떤 종류였는지 정형화된 필드로 남는다. 반면 분석 결과는 크게 달라진다. 예를 들어 `SLOT` 거절의 가상 수익이 높아도, 그것은 방향 위험 한도를 무시해야 했다는 의미이지 MACRO VETO가 잘못됐다는 증거가 아니다.

---

## 2.5 원장 실패 시 승인 중단

V3의 `admit()`는 Notion 기록 성공 여부를 확인하지 않고 알림과 OPEN 인덱스 갱신을 진행할 수 있었습니다. V4는 `log_signal()`이 page ID를 반환하지 않으면 알림과 OPEN 반영을 중단합니다.[4]

```text
V3: Notion write 실패 → 알림/메모리 OPEN은 진행 가능
V4: Notion write 실패 → 후보를 승인하지 않음
```

이것은 가격 알파를 바꾸지 않지만, 원장 없는 신호가 채점에서 사라져 성과 통계가 왜곡되는 운영 리스크를 줄입니다.

---

## 3. 유지된 핵심 알고리즘

아래는 V4 패치에서 의도적으로 바꾸지 않은 영역이다.

| 컴포넌트 | 유지 내용 |
|---|---|
| L 위치축 | SMA/ATR 정규화 편차와 자기분포 백분위의 계산 철학 |
| F 흐름축 | 5분 candle momentum의 백분위 기반 확인 |
| S 구조축 | 15분·1시간·4시간 EMA 정렬과 구조 붕괴 판단 |
| 레짐 라우터 | RANGE→REV, TREND→CONT, EXPANSION→BREAKOUT의 라우팅 |
| 폴라리티 진리표 | REV/CONT의 롱·숏 미러 조건과 BREAKOUT 기본 구조 |
| TP/SL 구조 | 스윙·ATR 버퍼·VWAP 문맥·RR 최소/최대 제한 |
| 동시 노출 제한 | 심볼·방향 슬롯과 전역 동일방향 한도 |
| 보수적 동시 터치 | 동일 5분 봉 TP/SL 동시 도달 시 LOSS 우선 |
| Shadow의 존재 | 차단 후보에 동일 배리어를 적용해 반사실을 기록하는 철학 |

따라서 V4는 “새로운 신호 알파”가 아니라, 기존 ORTHO의 신호 철학을 **더 느리지만 확정적인 입력**, **비용 0의 명시적 가정**, **고정 배리어 청산**, **분리된 검증 원장** 위에서 측정하는 버전이다.

---

## 4. V3와 V4를 어떻게 비교해야 하는가

V3→V4에는 세 개의 동시 변화가 있으므로, 아래 표처럼 비교 단위를 분리한다.

<table header-row="true" fit-page-width="true">
	<tr>
		<td>질문</td>
		<td>가능한가?</td>
		<td>올바른 비교 방법</td>
	</tr>
	<tr>
		<td>V4가 V3보다 좋은가?</td>
		<td>즉시 판단 불가</td>
		<td>다른 entry timing·spread filter·exit rule이 섞였으므로 단순 평균 R 비교 금지</td>
	</tr>
	<tr>
		<td>비용 0에서 ORTHO 신호가 양의 기대값인가?</td>
		<td>가능</td>
		<td>V4 LIVE만으로 고유 Snapshot At 배치 단위의 순차 walk-forward와 군집 부트스트랩 수행</td>
	</tr>
	<tr>
		<td>Alpha VETO가 유효한가?</td>
		<td>가능</td>
		<td>같은 Strategy ID·레짐·심볼·방향의 LIVE와 ALPHA_SHADOW를 분리 비교</td>
	</tr>
	<tr>
		<td>실체결 가능성이 있는가?</td>
		<td>불가</td>
		<td>별도 REAL_COST 캠페인에서 bid/ask·fee·slippage·partial fill을 포함해 재검증</td>
	</tr>
</table>

### 권고 리포트 순서

1. `Strategy ID=ORTHO-4.SIM0`과 `Cost Mode=SIM_COST_0`만 필터링한다.
2. `LIVE`와 `ALPHA_SHADOW`를 혼합하지 않는다.
3. `Gross R=Net R`을 비용 후 성과처럼 해석하지 않는다.
4. 동일 `Snapshot At`의 동시 후보를 하나의 배치로 묶어 군집 의존성을 반영한다.
5. 심볼·방향·레짐 세분화는 다중검정 보정 전까지 탐색적 결과로만 본다.
6. 하나의 새 가설만 사전등록한 다음 `Strategy ID`를 올려 다음 캠페인으로 분리한다.

---

## 5. 테스트로 확인한 계약

V4 단위 테스트는 다음 계약을 검증합니다.[5]

- Alpha VETO, 운영 거절, 경계 탐색이 서로 다른 stage/class로 나뉩니다.
- 비용 0 결과에서 `Gross R=Net R`과 `Realized Cost R=0`이 유지됩니다.
- V4에서 스프레드만으로 신호가 차단되지 않고, V3 호환 모드에서는 기존 차단이 보존됩니다.
- Notion 신호 기록 payload가 V4 stage, Strategy ID, hash, 비용, Gross/Net R, VETO 필드를 포함합니다.
- Notion 결과 업데이트가 `Gross R`, `Net R`, `Realized Cost R`을 전용 필드에 기록합니다.

```bash
python3 -m py_compile src/*.py scripts/create_shadow_db.py
python3 -m unittest discover -s tests -v
```

## References

[1]: [ORTHO-4 설정 강제 규칙](../src/ortho_config.py)
[2]: [신호 엔진의 닫힌 봉·스프레드 VETO 경로](../src/ortho_engine.py)
[3]: [V4 stage·VETO 분류 계약](../src/ortho_v4.py)
[4]: [승인·Notion 기록 실패 처리](../src/ortho_main.py)
[5]: [V4 회귀 테스트](../tests/test_ortho_v4.py)

**면책:** This is research and analysis only, not personalized financial advice.
