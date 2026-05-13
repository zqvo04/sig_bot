"""
scoring_system_integration.py
──────────────────────────────────────────────────────────────────────────────
scoring_system.py에 적용할 3개 이슈 수정 코드 모음

[Fix Issue 2] FVG 양방향 동시 보너스 → 반감 처리
[Fix Issue 4] 청산 이중처리 → API 패널티 발동 시 캔들 프록시 보너스 억제
[Fix Issue 7] micro_result → pipeline_result에 포함

사용법:
  이 파일의 각 섹션에 있는 코드를 scoring_system.py의 해당 위치에 삽입/교체하세요.
  각 섹션 위에 삽입 위치가 명시되어 있습니다.
──────────────────────────────────────────────────────────────────────────────
"""

# ══════════════════════════════════════════════════════════════════════════════
# [Fix Issue 2] FVG 보너스 계산 함수
# ══════════════════════════════════════════════════════════════════════════════
# 삽입 위치: scoring_system.py 에서 FVG 보너스를 계산하는 부분
#
# 기존 코드 (예시):
#   if fvg.get("in_bullish_fvg") and direction == "long":
#       bonuses.append(("FVG진입", config.BONUS_FVG_ENTRY))  # +10pt
#   if fvg.get("in_bearish_fvg") and direction == "short":
#       bonuses.append(("FVG진입", config.BONUS_FVG_ENTRY))  # +10pt
#
# 교체 코드:

def calc_fvg_bonus(fvg: dict, direction: str) -> tuple:
    """
    [Fix Issue 2] FVG 보너스 계산
    강세+약세 FVG 동시 활성 시 방향별 보너스를 BONUS_FVG_ENTRY_CONFLICTED(5pt)로 반감
    """
    import config as _cfg
    bull_fvg = fvg.get("in_bullish_fvg", False)
    bear_fvg = fvg.get("in_bearish_fvg", False)
    both_active = bull_fvg and bear_fvg

    bonus_value = _cfg.BONUS_FVG_ENTRY_CONFLICTED if both_active else _cfg.BONUS_FVG_ENTRY

    if both_active:
        # 양방향 동시 활성: 신호 방향 보너스만 (반감) + 경고 로그
        import logging
        logging.getLogger(__name__).info(
            f"[FVG] ⚠️ 강세+약세 FVG 동시 활성 — 보너스 반감 "
            f"({_cfg.BONUS_FVG_ENTRY}pt → {bonus_value}pt)"
        )
        if direction == "long":
            return ("FVG진입(모호)", bonus_value)
        elif direction == "short":
            return ("FVG진입(모호)", bonus_value)
    else:
        if bull_fvg and direction == "long":
            return ("FVG강세진입", bonus_value)
        elif bear_fvg and direction == "short":
            return ("FVG약세진입", bonus_value)

    return None   # 보너스 없음


# ── 실제 scoring_system.py 적용 예시 ──────────────────────────
# from scoring_system_integration import calc_fvg_bonus
#
# # 기존 FVG 보너스 코드를 아래로 교체:
# fvg_bonus = calc_fvg_bonus(fvg, direction)
# if fvg_bonus:
#     bonuses.append(fvg_bonus)
#     logger.info(f"[FVG] {fvg_bonus[0]} +{fvg_bonus[1]}pt")


# ══════════════════════════════════════════════════════════════════════════════
# [Fix Issue 4] 청산 보너스 조건부 억제 함수
# ══════════════════════════════════════════════════════════════════════════════
# 삽입 위치: scoring_system.py에서 BONUS_LIQUIDATION을 추가하는 부분
#
# 기존 코드 (예시):
#   if liq.get("signal") == "long_liq_detected" and direction == "short":
#       bonuses.append(("청산반등", config.BONUS_LIQUIDATION))
#   elif liq.get("signal") == "short_liq_detected" and direction == "long":
#       bonuses.append(("청산반등", config.BONUS_LIQUIDATION))
#
# 교체 코드:

def calc_liquidation_bonus(liq: dict, direction: str, micro_result: dict) -> tuple:
    """
    [Fix Issue 4] 청산 보너스 조건부 억제
    - API 청산 데이터(방안 1)가 패널티를 발동했으면 캔들 프록시 보너스 적용 안 함
    - API 데이터 없거나 패널티 미발동 시: 기존 로직 그대로
    """
    import config as _cfg
    import logging
    _log = logging.getLogger(__name__)

    signal    = liq.get("signal", "none")
    is_large  = liq.get("is_large", False)
    bonus_val = _cfg.BONUS_LIQUIDATION

    # API 패널티 발동 여부 확인 (micro_result가 있을 때만)
    liq_api_penalty_fired = False
    if micro_result:
        liq_api_penalty_fired = any(
            name == "LiqCascade" and p < 0
            for name, p, _ in micro_result.get("details", [])
        )

    if liq_api_penalty_fired:
        # API 데이터가 이미 패널티를 부과 → 캔들 프록시 보너스 중복 억제
        _log.info(
            f"[청산프록시] BONUS_LIQUIDATION 억제 — API 청산 패널티({micro_result.get('total_penalty',0):+d}pt) 우선 적용"
        )
        return None

    # API 패널티 없음 → 기존 로직 적용
    if signal == "long_liq_detected":
        if direction == "short":
            label = f"청산반등{'(대규모)' if is_large else ''}"
            return (label, bonus_val)
    elif signal == "short_liq_detected":
        if direction == "long":
            label = f"청산반등{'(대규모)' if is_large else ''}"
            return (label, bonus_val)

    return None


# ── 실제 scoring_system.py 적용 예시 ──────────────────────────
# from scoring_system_integration import calc_liquidation_bonus
#
# # micro_result는 이미 계산되어 있어야 함 (Fix Issue 7 선행)
# liq_bonus = calc_liquidation_bonus(liq, direction, micro_long if direction=="long" else micro_short)
# if liq_bonus:
#     bonuses.append(liq_bonus)
#     logger.info(f"[청산] {liq_bonus[0]} +{liq_bonus[1]}pt")


# ══════════════════════════════════════════════════════════════════════════════
# [Fix Issue 7] pipeline_result에 micro_result 포함
# ══════════════════════════════════════════════════════════════════════════════
# 삽입 위치: scoring_system.py에서 pipeline_result dict를 구성하는 부분
#
# 기존 코드 (예시):
#   pipeline_result = {
#       "symbol":        symbol,
#       "direction":     signal_direction,
#       "score":         final_score,
#       "signal_result": signals,
#       "regime":        regime_info,
#       "should_notify": True,
#   }
#
# 교체 코드:
#
#   # micro_result는 scoring 과정에서 이미 계산됨 (아래 참조)
#   pipeline_result = build_pipeline_result(
#       symbol, signal_direction, final_score, signals,
#       regime_info, micro_long, micro_short
#   )

def build_pipeline_result(
    symbol:           str,
    direction:        str,      # "long" or "short"
    score:            float,
    signal_result:    dict,
    regime_info:      dict,
    micro_long:       dict,     # compute_microstructure_penalties 결과 (LONG)
    micro_short:      dict,     # compute_microstructure_penalties 결과 (SHORT)
    should_notify:    bool = True,
    extra:            dict = None,
) -> dict:
    """
    [Fix Issue 7] pipeline_result 표준 구성 함수
    - 신호 방향에 맞는 micro_result를 'micro_result' 키로 포함
    - notification.py의 마이크로구조 섹션이 이 키를 읽음
    """
    micro_result = micro_long if direction == "long" else micro_short

    result = {
        "symbol":        symbol,
        "direction":     direction,
        "score":         score,
        "signal_result": signal_result,
        "regime":        regime_info,
        "should_notify": should_notify,
        "micro_result":  micro_result,   # [Fix Issue 7] 신규 추가
    }
    if extra:
        result.update(extra)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# scoring_system.py 전체 통합 흐름 (참고용 의사코드)
# ══════════════════════════════════════════════════════════════════════════════
"""
def calculate_scores(symbol, market_data, analysis):
    from microstructure_analyzer_v2 import compute_microstructure_penalties
    from scoring_system_integration import (
        calc_fvg_bonus, calc_liquidation_bonus, build_pipeline_result
    )

    direction   = ...  # "long" or "short"
    regime      = analysis["regime"]["regime"]
    fvg         = analysis["fvg"]
    liq         = analysis["liquidations"]
    percent_b   = analysis["bollinger"]["pct_b"]
    taker_buy   = market_data["taker"]["buy_pct"]
    pos_long    = market_data["ls_ratio"]["long_pct"] / 100.0
    price       = market_data["price"]

    # ── Step 1: 기존 raw score 계산 (변경 없음) ──────────────
    raw_long  = ...
    raw_short = ...

    # ── Step 2: 마이크로구조 패널티 계산 ──────────────────────
    micro_data = market_data.get("microstructure", {})

    micro_long = compute_microstructure_penalties(
        micro_data=micro_data, current_price=price,
        direction="long", regime=regime,   # 소문자도 허용 (Fix Issue 6)
        percent_b=percent_b, taker_buy_pct=taker_buy,
        position_long_pct=pos_long,
    )
    micro_short = compute_microstructure_penalties(
        micro_data=micro_data, current_price=price,
        direction="short", regime=regime,
        percent_b=percent_b, taker_buy_pct=taker_buy,
        position_long_pct=pos_long,
    )

    # ── Step 3: 보너스 계산 (FVG 수정 + 청산 억제) ────────────
    bonuses_long  = []
    bonuses_short = []

    # [Fix Issue 2] FVG 보너스 (양방향 동시 반감)
    fvg_b_long  = calc_fvg_bonus(fvg, "long")
    fvg_b_short = calc_fvg_bonus(fvg, "short")
    if fvg_b_long:  bonuses_long.append(fvg_b_long)
    if fvg_b_short: bonuses_short.append(fvg_b_short)

    # [Fix Issue 4] 청산 보너스 (API 패널티 발동 시 억제)
    liq_b_long  = calc_liquidation_bonus(liq, "long",  micro_long)
    liq_b_short = calc_liquidation_bonus(liq, "short", micro_short)
    if liq_b_long:  bonuses_long.append(liq_b_long)
    if liq_b_short: bonuses_short.append(liq_b_short)

    # ... 나머지 기존 보너스들 ...

    # ── Step 4: 최종 점수 = raw + 보너스(캡 적용) + 마이크로 패널티
    bonus_total_long  = min(sum(v for _,v in bonuses_long),  bonus_cap_long)
    bonus_total_short = min(sum(v for _,v in bonuses_short), bonus_cap_short)

    final_long  = raw_long  + bonus_total_long  + micro_long["total_penalty"]
    final_short = raw_short + bonus_total_short + micro_short["total_penalty"]

    # ── Step 5: 신호 판정 ──────────────────────────────────────
    threshold = regime_info["threshold"]
    if final_long >= threshold:
        signal_dir, final_score = "long", final_long
    elif final_short >= threshold:
        signal_dir, final_score = "short", final_short
    else:
        return None   # 신호 없음

    # ── Step 6: pipeline_result 구성 (micro_result 포함) ────
    pipeline_result = build_pipeline_result(
        symbol=symbol,
        direction=signal_dir,
        score=final_score,
        signal_result={"long": {...}, "short": {...}},
        regime_info=regime_info,
        micro_long=micro_long,
        micro_short=micro_short,
    )
    return pipeline_result
"""
