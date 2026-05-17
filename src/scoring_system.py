"""
scoring_system.py — 점수 산출 (v3.3)
────────────────────────────────────────────────────────────────────
[v3.3 개선]

① base_score 유령 계산 제거
   기존: base_score에 mtf_penalty, exhaustion_mult를 중간에 곱산 적용했지만
         최종 공식은 base_before_soft를 사용 → 수정된 base_score는 사실상 미사용
         (로깅에만 사용, 최종 결과에 미영향)
   수정: base_score 단일 변수로 통합 (base_before_soft 별칭 제거)
         중간 수정 없이 soft_penalty 체인에서 일괄 적용
   효과: 변수 흐름 명확, "수정됐지만 무시됨" 코드 패턴 제거

② SIGNAL_MIN_SCORE 제거
   기존: signal = (score >= regime_threshold) AND (score >= SIGNAL_MIN_SCORE=63)
         → 실질 max(regime, 63), 국면별 임계값 무의미
   수정: signal = (score >= regime_threshold) 단독
         국면별 임계값이 실제로 작동 (SQUEEZE/EXPLOSIVE 65, TRENDING 63, RANGING 62)

③ 거래량 페널티 추가 (v3.3 patch)
   배경: volume 가중치 5~9%로 낮아 0pt여도 raw_score 억제 ~2.5~4.5pt에 불과.
         보너스 하나(+4pt~)로 쉽게 상쇄 → 저거래량(주말 등) 신호 과다 발생.
   수정: vol score 기준 명시적 덧셈 페널티 추가.
     score <  5pt (ratio <  10%) → -7pt  (사실상 거래 없음)
     score < 15pt (ratio <  30%) → -3pt  (평균 30% 미달)
     score ≥ 15pt                →  0pt  (정상 범위)
   적용 공식:
     final_score = (base + bonus) × soft_penalty + micro_penalty + volume_penalty

[v3.2] BOS_CONFLICT_PENALTY ×0.82 추가
[v3.1] OI 제거
[v3]   ADX 배율 제거, EMA 배율 통합
────────────────────────────────────────────────────────────────────
"""
import json, logging, os
from datetime import datetime, timezone, timedelta
import config

logger = logging.getLogger(__name__)


def _get_tiered_bonus_cap(base_score: float) -> int:
    for threshold, cap in config.BONUS_CAP_TIERS:
        if base_score < threshold:
            return cap
    return 36


def calculate_entry_score(analysis: dict, direction: str,
                           micro_result: dict = None) -> dict:
    d    = direction
    gate = analysis.get(f"gate_{d}", {})
    gate_penalty = gate.get("funding_penalty", 1.0)

    rsi     = analysis.get("rsi",         {})
    bb      = analysis.get("bollinger",    {})
    funding = analysis.get("funding_rate", {})
    ls      = analysis.get("ls_ratio",     {})
    taker   = analysis.get("taker_volume", {})
    liq     = analysis.get("liquidations", {})
    vol     = analysis.get("volume",       {})
    adx_15m = analysis.get("adx_15m",     {})
    regime  = analysis.get("regime",       {})

    ema_info      = analysis.get(f"ema_{d}", {})
    reverse_count = ema_info.get("reverse_count", 0)

    rsi_val_15m  = rsi.get("value",    50.0)
    rsi_val_1h   = rsi.get("value_1h", 50.0)
    rsi_val_4h   = rsi.get("value_4h", 50.0)
    bb_state_str = bb.get("state", "")

    # ── 가중합 ───────────────────────────────────────────────────
    scores = {
        "rsi":              rsi.get(f"{d}_score",     50.0),
        "bollinger":        bb.get(f"{d}_score",      50.0),
        "funding_rate":     funding.get(f"{d}_score", 50.0),
        "long_short_ratio": ls.get(f"{d}_score",      50.0),
        "taker_volume":     taker.get(f"{d}_score",   50.0),
        "volume":           vol.get("score",          50.0),
    }

    regime_name = regime.get("regime", "UNKNOWN")
    weights     = config.REGIME_SCORE_WEIGHTS.get(regime_name, config.SCORE_WEIGHTS)

    # EMA 3역방향 시 LS 중립화
    bb_reversal_exempt = (
        (d == "long"  and bb_state_str == "lower_breakout") or
        (d == "short" and bb_state_str == "upper_breakout")
    )
    ema_all_reverse = (reverse_count == 3)

    if ema_all_reverse and not bb_reversal_exempt:
        ls_raw_before = scores["long_short_ratio"]
        scores["long_short_ratio"] = 50.0
        logger.info(f"[EMA3역방향] LS 중립화: {ls_raw_before:.0f}→50pt [{d.upper()}]")

    raw_score = sum(scores[k] * weights[k] for k in weights)

    # ── EMA 배율 ─────────────────────────────────────────────────
    ema_table = config.REGIME_EMA_MULTIPLIERS.get(regime_name, config.EMA_MULTIPLIER)
    ema_mult  = ema_table.get(reverse_count, 1.0)
    logger.info(f"[EMA배율/{d.upper()}] {ema_info.get('tf_signals',{})} → ×{ema_mult:.2f}  [{regime_name}]")

    # ── 극단 과매도/과매수 판정 ──────────────────────────────────
    is_extreme_oversold = (
        d == "long" and
        rsi_val_15m <= config.EXTREME_OVERSOLD_15M and
        rsi_val_1h  <= config.EXTREME_OVERSOLD_1H  and
        rsi_val_4h  <= config.EXTREME_OVERSOLD_4H  and
        bb_state_str in ("lower_breakout", "near_lower", "lower_zone")
    )
    is_extreme_overbought = (
        d == "short" and
        rsi_val_15m >= config.EXTREME_OVERBOUGHT_15M and
        rsi_val_1h  >= config.EXTREME_OVERBOUGHT_1H  and
        rsi_val_4h  >= config.EXTREME_OVERBOUGHT_4H  and
        bb_state_str in ("upper_breakout", "near_upper", "upper_zone")
    )

    if is_extreme_oversold:
        logger.info(
            f"[극단과매도/{d.upper()}] 전TF RSI 극단 "
            f"15m:{rsi_val_15m:.0f} 1h:{rsi_val_1h:.0f} 4h:{rsi_val_4h:.0f} BB:{bb_state_str}"
        )
    if is_extreme_overbought:
        logger.info(
            f"[극단과매수/{d.upper()}] 전TF RSI 극단 "
            f"15m:{rsi_val_15m:.0f} 1h:{rsi_val_1h:.0f} 4h:{rsi_val_4h:.0f} BB:{bb_state_str}"
        )

    # ── base_score (v3.3: 단일 변수, 중간 수정 없음) ──────────────
    base_score = raw_score * ema_mult * gate_penalty

    # ── soft 패널티 계산 ──────────────────────────────────────────
    rsi_1h = rsi_val_1h
    rsi_4h = rsi_val_4h
    mtf_penalty = 1.0; mtf_penalty_reason = None

    if d == "long":
        if rsi_1h >= config.MTF_RSI_OVERBOUGHT_1H_EXTREME:
            mtf_penalty = config.MTF_RSI_PENALTY_STRONG
            mtf_penalty_reason = f"MTF RSI 극단과매수(1h:{rsi_1h:.1f}) → 롱 ×{mtf_penalty}"
        elif rsi_1h >= config.MTF_RSI_OVERBOUGHT_1H and rsi_4h >= config.MTF_RSI_OVERBOUGHT_4H:
            mtf_penalty = config.MTF_RSI_PENALTY_STRONG
            mtf_penalty_reason = f"MTF RSI 강과매수(1h:{rsi_1h:.1f} 4h:{rsi_4h:.1f}) → 롱 ×{mtf_penalty}"
        elif rsi_1h >= config.MTF_RSI_OVERBOUGHT_1H_MILD:
            mtf_penalty = config.MTF_RSI_PENALTY_MILD
            mtf_penalty_reason = f"MTF RSI 약과매수(1h:{rsi_1h:.1f}) → 롱 ×{mtf_penalty}"
    elif d == "short":
        if rsi_1h <= config.MTF_RSI_OVERSOLD_1H_EXTREME:
            mtf_penalty = config.MTF_RSI_PENALTY_STRONG
            mtf_penalty_reason = f"MTF RSI 극단과매도(1h:{rsi_1h:.1f}) → 숏 ×{mtf_penalty}"
        elif rsi_1h <= config.MTF_RSI_OVERSOLD_1H and rsi_4h <= config.MTF_RSI_OVERSOLD_4H:
            mtf_penalty = config.MTF_RSI_PENALTY_STRONG
            mtf_penalty_reason = f"MTF RSI 강과매도(1h:{rsi_1h:.1f} 4h:{rsi_4h:.1f}) → 숏 ×{mtf_penalty}"
        elif rsi_1h <= config.MTF_RSI_OVERSOLD_1H_MILD:
            mtf_penalty = config.MTF_RSI_PENALTY_MILD
            mtf_penalty_reason = f"MTF RSI 약과매도(1h:{rsi_1h:.1f}) → 숏 ×{mtf_penalty}"

    if mtf_penalty < 1.0:
        logger.info(f"[MTF-RSI/{d.upper()}] {mtf_penalty_reason}")

    exhaustion_mult = 1.0; exhaustion_reason = None
    if regime_name == "EXPLOSIVE":
        if d == "long" and rsi_1h >= config.EXPLOSIVE_EXHAUSTION_RSI_LONG:
            exhaustion_mult = config.EXPLOSIVE_EXHAUSTION_PENALTY
            exhaustion_reason = f"EXPLOSIVE 소진(1h RSI:{rsi_1h:.1f}) → 롱 ×{exhaustion_mult}"
        elif d == "short" and rsi_1h <= config.EXPLOSIVE_EXHAUSTION_RSI_SHORT:
            exhaustion_mult = config.EXPLOSIVE_EXHAUSTION_PENALTY
            exhaustion_reason = f"EXPLOSIVE 소진(1h RSI:{rsi_1h:.1f}) → 숏 ×{exhaustion_mult}"
    if exhaustion_mult < 1.0:
        logger.info(f"[EXPLOSIVE소진/{d.upper()}] {exhaustion_reason}")

    # ── BB 연속 이탈 억제 ────────────────────────────────────────
    BB_STREAK    = 3
    lower_streak = bb.get("lower_streak", 0)
    upper_streak = bb.get("upper_streak", 0)
    bb_suppressed = False; bb_reason = None

    if d == "long" and lower_streak >= BB_STREAK and regime_name == "TRENDING":
        if rsi_val_15m <= config.BB_STREAK_SUPPRESS_RSI_EXEMPT:
            logger.info(f"[BB억제면제/{d.upper()}] RSI극단({rsi_val_15m:.0f}) → BB streak 억제 해제")
        else:
            bb_suppressed = True
            bb_reason = f"TRENDING BB 하단 {lower_streak}캔들 연속 이탈 — 롱 억제"
    elif d == "short" and upper_streak >= BB_STREAK and regime_name == "TRENDING":
        if rsi_val_15m >= (100 - config.BB_STREAK_SUPPRESS_RSI_EXEMPT):
            logger.info(f"[BB억제면제/{d.upper()}] RSI극단({rsi_val_15m:.0f}) → BB streak 억제 해제")
        else:
            bb_suppressed = True
            bb_reason = f"TRENDING BB 상단 {upper_streak}캔들 연속 이탈 — 숏 억제"

    if bb_suppressed:
        logger.info(f"[Score/{d.upper()}] ⛔ {bb_reason}")
        return {
            "direction": d, "final_score": 0.0, "raw_score": round(raw_score, 2),
            "weighted_score": 0.0, "ema_multiplier": ema_mult, "adx_multiplier": 1.0,
            "passed_gate": True, "signal": False, "component_scores": scores,
            "bonuses": [], "bonus_total": 0, "gate_info": gate,
            "bb_suppressed": True, "bb_suppress_reason": bb_reason, "regime": regime,
            "breakdown": "⛔ BB 연속 이탈 억제",
            "volume_penalty": 0,
        }

    # ══════════════════════════════════════════════════════════════
    # 보너스 계산
    # ══════════════════════════════════════════════════════════════
    bonuses = []

    # ① 극단 과매도/과매수
    if is_extreme_oversold:
        bonuses.append(("멀티TF극단과매도", config.BONUS_EXTREME_OVERSOLD_MTF))
        logger.info(f"[극단과매도보너스] ★ +{config.BONUS_EXTREME_OVERSOLD_MTF}pt")
    elif is_extreme_overbought:
        bonuses.append(("멀티TF극단과매수", config.BONUS_EXTREME_OVERSOLD_MTF))
        logger.info(f"[극단과매수보너스] ★ +{config.BONUS_EXTREME_OVERSOLD_MTF}pt")

    # ② 볼린저 극단 + RSI 다이버전스
    bb_extreme = bb_state_str in ("lower_breakout", "near_lower", "upper_breakout", "near_upper")
    has_div    = rsi.get("bullish_divergence") if d == "long" else rsi.get("bearish_divergence")
    if bb_extreme and has_div:
        bonuses.append(("볼린저극단+RSI다이버전스", config.BONUS_BB_RSI_ALIGN))

    # ③ 펀딩비 + 롱숏비율 동일 방향
    fr_bias = funding.get("bias", "neutral")
    ls_bias = ls.get("bias", "neutral")
    fr_ok   = (fr_bias == "long_favorable"  if d == "long"  else fr_bias == "short_favorable")
    ls_ok   = (ls_bias in ("long_favorable", "long_extreme")   if d == "long"  else
               ls_bias in ("short_favorable", "short_extreme"))
    if fr_ok and ls_ok:
        bonuses.append(("펀딩비+롱숏비율", config.BONUS_FUNDING_LS_ALIGN))

    # ④ 대규모 청산
    liq_signal = liq.get("signal", "none")
    liq_large  = liq.get("is_large", False)
    liq_api_fired = (
        micro_result is not None and
        any(name == "LiqCascade" and p < 0
            for name, p, _ in micro_result.get("details", []))
    )
    if liq_api_fired:
        logger.info(
            f"[청산프록시/{d.upper()}] BONUS_LIQUIDATION 억제 — "
            f"API 패널티({micro_result.get('total_penalty', 0):+d}pt) 우선 적용"
        )
    elif liq_large and (
        (d == "long"  and liq_signal == "long_liq_detected") or
        (d == "short" and liq_signal == "short_liq_detected")
    ):
        bonuses.append(("대규모청산꼬리", config.BONUS_LIQUIDATION))

    # ⑤ 추세 지속 (EMA+Taker)
    ema_same   = ema_info.get("same_count", 0)
    taker_bias = taker.get("bias", "neutral")
    taker_str  = taker.get("strength", "neutral")
    trend_strong_ok = (
        ema_same == 3 and taker_str in ("strong", "mild") and (
            (d == "long"  and taker_bias == "buy_dominant") or
            (d == "short" and taker_bias == "sell_dominant")
        )
    )
    if trend_strong_ok:
        bonuses.append((f"추세지속:EMA+Taker({'롱' if d=='long' else '숏'})", config.BONUS_TREND_STRONG))

    # ⑥ 눌림목
    pb_ok_strong = (
        (d == "long"  and rsi.get("pullback_long_strong",  False) and ema_same >= 2) or
        (d == "short" and rsi.get("pullback_short_strong", False) and ema_same >= 2)
    )
    pb_ok_weak = (
        (d == "long"  and rsi.get("pullback_long_weak",   False) and not rsi.get("pullback_long_strong")  and ema_same >= 2) or
        (d == "short" and rsi.get("pullback_short_weak",  False) and not rsi.get("pullback_short_strong") and ema_same >= 2)
    )
    pb_ok_micro = (
        (d == "long"  and rsi.get("pullback_long_micro",  False) and not pb_ok_strong and not pb_ok_weak and ema_same >= 1) or
        (d == "short" and rsi.get("pullback_short_micro", False) and not pb_ok_strong and not pb_ok_weak and ema_same >= 1)
    )
    if pb_ok_strong:
        bonuses.append((f"눌림목강({d.upper()})", config.BONUS_PULLBACK_ENTRY))
        logger.info(f"[눌림목강] +{config.BONUS_PULLBACK_ENTRY}pt [{d.upper()}]")
    elif pb_ok_weak:
        bonuses.append((f"눌림목약({d.upper()})", config.BONUS_PULLBACK_ENTRY_WEAK))
        logger.info(f"[눌림목약] +{config.BONUS_PULLBACK_ENTRY_WEAK}pt [{d.upper()}]")
    elif pb_ok_micro:
        bonuses.append((f"눌림목미세({d.upper()})", config.BONUS_PULLBACK_ENTRY_MICRO))
        logger.info(f"[눌림목미세] +{config.BONUS_PULLBACK_ENTRY_MICRO}pt [{d.upper()}]")

    # ⑦ 거래량-가격 다이버전스
    vpd = analysis.get("vol_price_div", {})
    if d == "short" and vpd.get("bearish_vol_div"):
        bonuses.append(("거래량약세다이버전스", config.BONUS_VOL_PRICE_DIV))
    elif d == "long" and vpd.get("bullish_vol_div"):
        bonuses.append(("거래량강세다이버전스", config.BONUS_VOL_PRICE_DIV))

    # ⑧ 돌파/붕괴 실패
    ms = analysis.get("market_structure", {})
    if d == "short":
        if ms.get("failed_breakout"): bonuses.append(("돌파실패",      config.BONUS_FAILED_BREAKOUT))
        if ms.get("lower_high"):      bonuses.append(("LowerHigh구조", config.BONUS_MARKET_STRUCT_TREND))
    elif d == "long":
        if ms.get("failed_breakdown"):bonuses.append(("붕괴실패",      config.BONUS_FAILED_BREAKOUT))
        if ms.get("higher_low"):      bonuses.append(("HigherLow구조", config.BONUS_MARKET_STRUCT_TREND))

    # ⑨ FVG
    fvg      = analysis.get("fvg", {})
    bull_fvg = fvg.get("in_bullish_fvg", False)
    bear_fvg = fvg.get("in_bearish_fvg", False)
    both_fvg = bull_fvg and bear_fvg
    fvg_val  = config.BONUS_FVG_ENTRY_CONFLICTED if both_fvg else config.BONUS_FVG_ENTRY
    if both_fvg:
        bonuses.append((f"FVG{'강세' if d=='long' else '약세'}진입(모호)", fvg_val))
        logger.info(f"[FVG] ⚠️ 양방향 동시 → 보너스 반감 +{fvg_val}pt")
    elif d == "long"  and bull_fvg:
        bonuses.append(("FVG강세진입", fvg_val))
        logger.info(f"[FVG] ★ 롱 FVG +{fvg_val}pt")
    elif d == "short" and bear_fvg:
        bonuses.append(("FVG약세진입", fvg_val))
        logger.info(f"[FVG] ★ 숏 FVG +{fvg_val}pt")

    # ⑩ BOS 확증
    bos_choch = analysis.get("bos_choch", {})
    if d == "long"  and bos_choch.get("bos_bullish"):
        bonuses.append(("BOS상승확증", config.BONUS_BOS_CONFIRM))
        logger.info(f"[BOS] ★ 상승 BOS +{config.BONUS_BOS_CONFIRM}pt")
    elif d == "short" and bos_choch.get("bos_bearish"):
        bonuses.append(("BOS하락확증", config.BONUS_BOS_CONFIRM))
        logger.info(f"[BOS] ★ 하락 BOS +{config.BONUS_BOS_CONFIRM}pt")

    # ⑪ 피보나치
    fibonacci = analysis.get("fibonacci", {})
    if d == "long":
        if fibonacci.get("in_golden_pocket_long"):
            bonuses.append(("피보황금포켓롱", config.BONUS_FIB_GOLDEN_POCKET))
            logger.info(f"[피보] ★ 롱 황금포켓 +{config.BONUS_FIB_GOLDEN_POCKET}pt")
        elif fibonacci.get("near_key_level_long"):
            bonuses.append(("피보주요레벨롱", config.BONUS_FIB_KEY_LEVEL))
    elif d == "short":
        if fibonacci.get("in_golden_pocket_short"):
            bonuses.append(("피보황금포켓숏", config.BONUS_FIB_GOLDEN_POCKET))
            logger.info(f"[피보] ★ 숏 황금포켓 +{config.BONUS_FIB_GOLDEN_POCKET}pt")
        elif fibonacci.get("near_key_level_short"):
            bonuses.append(("피보주요레벨숏", config.BONUS_FIB_KEY_LEVEL))

    # ⑫ 히든 다이버전스
    hidden_bull = rsi.get("hidden_bull_div", False)
    hidden_bear = rsi.get("hidden_bear_div", False)
    if d == "long"  and hidden_bull:
        bonuses.append(("히든강세다이버전스", config.BONUS_HIDDEN_DIVERGENCE))
        logger.info(f"[히든Div] ★ 롱 +{config.BONUS_HIDDEN_DIVERGENCE}pt")
    elif d == "short" and hidden_bear:
        bonuses.append(("히든약세다이버전스", config.BONUS_HIDDEN_DIVERGENCE))
        logger.info(f"[히든Div] ★ 숏 +{config.BONUS_HIDDEN_DIVERGENCE}pt")

    # ⑬ 캔들 패턴
    candle = analysis.get("candle_pattern", {})
    if d == "short":
        if candle.get("bearish_pin"):      bonuses.append(("베어리시핀바",   config.BONUS_CANDLE_PIN_BAR))
        elif candle.get("bearish_engulf"): bonuses.append(("베어리시인걸핑", config.BONUS_CANDLE_ENGULFING))
    elif d == "long":
        if candle.get("bullish_pin"):      bonuses.append(("불리시핀바",     config.BONUS_CANDLE_PIN_BAR))
        elif candle.get("bullish_engulf"): bonuses.append(("불리시인걸핑",   config.BONUS_CANDLE_ENGULFING))

    # ⑭ 거래량 폭발
    vol_ratio = vol.get("ratio", 1.0)
    adx_val   = adx_15m.get("adx", 0.0)
    if vol_ratio >= 2.5 and adx_val >= 22.0 and ema_same < 3:
        bonuses.append(("거래량폭발", config.BONUS_VOLUME_EXPLOSION))

    # ⑮ Post-Squeeze 모멘텀
    prev_regime   = analysis.get("prev_regime", "")
    bb_state      = bb.get("state", "")
    bb_just_broke = (
        (d == "long"  and bb_state in ("upper_breakout","near_upper") and bb.get("upper_streak",0) == 1) or
        (d == "short" and bb_state in ("lower_breakout","near_lower") and bb.get("lower_streak",0) == 1)
    )
    if (prev_regime == "SQUEEZE" or regime_name == "EXPLOSIVE") and bb_just_broke:
        bonuses.append(
            ("Post-Squeeze롱돌파" if d=="long" else "Post-Squeeze숏돌파",
             config.BONUS_POST_SQUEEZE)
        )

    # ── 소진 상태에서 추세확인형 보너스 제거 ────────────────────
    if exhaustion_mult < 1.0:
        tc = {"LowerHigh구조","HigherLow구조","거래량약세다이버전스","거래량강세다이버전스",
              "볼린저극단+RSI다이버전스","BOS상승확증","BOS하락확증"}
        removed = [(n,v) for n,v in bonuses if n in tc]
        bonuses = [(n,v) for n,v in bonuses if n not in tc]
        if removed: logger.info(f"[소진보너스제거] {[n for n,_ in removed]}")

    # ── EMA 3역방향 시 반전 보너스 75% 감산 ─────────────────────
    _REV = {"거래량강세다이버전스","거래량약세다이버전스","볼린저극단+RSI다이버전스"}
    if ema_all_reverse and not bb_reversal_exempt:
        bonuses = [(n, round(v*0.25) if n in _REV else v) for n,v in bonuses]

    # ── Taker 역방향 시 캔들 보너스 감산 ────────────────────────
    _CANDLE = {"불리시핀바","베어리시핀바","불리시인걸핑","베어리시인걸핑"}
    _taker_against = (
        (d == "long"  and taker_bias == "sell_dominant") or
        (d == "short" and taker_bias == "buy_dominant")
    )
    if _taker_against:
        bonuses = [(n, round(v*0.40) if n in _CANDLE else v) for n,v in bonuses]

    # ── 저유동성 구조 패턴 보너스 억제 ──────────────────────────
    _LOW_VOL_STRUCT = {
        "LowerHigh구조", "HigherLow구조",
        "돌파실패",       "붕괴실패",
        "거래량강세다이버전스", "거래량약세다이버전스",
        "볼린저극단+RSI다이버전스",
    }
    vol_ratio = vol.get("ratio", 1.0)
    if vol_ratio < 0.30:
        affected = [(n, v) for n, v in bonuses if n in _LOW_VOL_STRUCT]
        if affected:
            bonuses = [
                (n, round(v * 0.5) if n in _LOW_VOL_STRUCT else v)
                for n, v in bonuses
            ]
            before_sum = sum(v for _, v in affected)
            after_sum  = sum(round(v * 0.5) for _, v in affected)
            logger.info(
                f"[저유동성/{d.upper()}] vol:{vol_ratio:.2f}x < 0.30 "
                f"→ 구조패턴 보너스 {before_sum}pt → {after_sum}pt (50% 감산) "
                f"[{', '.join(n for n, _ in affected)}]"
            )

    # ── 티어드 보너스 캡 ─────────────────────────────────────────
    bonus_raw   = sum(v for _,v in bonuses)
    bonus_cap   = _get_tiered_bonus_cap(base_score)
    bonus_total = min(bonus_cap, bonus_raw)
    if bonus_raw > bonus_cap:
        logger.info(f"[보너스캡] base:{base_score:.0f}pt → 캡:{bonus_cap}pt ({bonus_raw}→{bonus_total}pt)")

    # ── 캔들 모멘텀 역방향 패널티 ───────────────────────────────
    candle_momentum_mult = 1.0
    if d == "short" and candle.get("consecutive_bull"):
        if regime_name == "TRENDING":    candle_momentum_mult = config.CANDLE_MOMENTUM_PENALTY_TRENDING
        elif regime_name == "EXPLOSIVE": candle_momentum_mult = config.CANDLE_MOMENTUM_PENALTY_EXPLOSIVE
        else:                            candle_momentum_mult = config.CANDLE_MOMENTUM_PENALTY_RANGING
        logger.info(f"[캔들모멘텀] 연속양봉 중 숏 ×{candle_momentum_mult:.2f}")
    elif d == "long" and candle.get("consecutive_bear"):
        bb_lower_exempt = (bb_state in ("lower_breakout","near_lower") or bb.get("pct_b",0.5) <= 0.15)
        if not bb_lower_exempt:
            if regime_name == "TRENDING":    candle_momentum_mult = config.CANDLE_MOMENTUM_PENALTY_TRENDING
            elif regime_name == "EXPLOSIVE": candle_momentum_mult = config.CANDLE_MOMENTUM_PENALTY_EXPLOSIVE
            else:                            candle_momentum_mult = config.CANDLE_MOMENTUM_PENALTY_RANGING
            logger.info(f"[캔들모멘텀] 연속음봉 중 롱 ×{candle_momentum_mult:.2f}")

    # ── CHoCH 역방향 패널티 ──────────────────────────────────────
    choch_penalty    = 1.0
    bos_choch_data   = analysis.get("bos_choch", {})
    if d == "long"  and bos_choch_data.get("choch_bearish"):
        choch_penalty = config.CHOCH_AGAINST_PENALTY
        logger.info(f"[CHoCH/{d.upper()}] ⚠️ 하락전환 경고 중 롱 → ×{choch_penalty}")
    elif d == "short" and bos_choch_data.get("choch_bullish"):
        choch_penalty = config.CHOCH_AGAINST_PENALTY
        logger.info(f"[CHoCH/{d.upper()}] ⚠️ 상승전환 경고 중 숏 → ×{choch_penalty}")

    # ── BOS 역방향 패널티 ────────────────────────────────────────
    bos_conflict_penalty = 1.0
    if d == "long" and bos_choch_data.get("bos_bearish"):
        bos_conflict_penalty = config.BOS_CONFLICT_PENALTY
        logger.info(f"[BOS/{d.upper()}] ⚠️ 하락 BOS 확증 → 역추세 롱 ×{bos_conflict_penalty}")
    elif d == "short" and bos_choch_data.get("bos_bullish"):
        bos_conflict_penalty = config.BOS_CONFLICT_PENALTY
        logger.info(f"[BOS/{d.upper()}] ⚠️ 상승 BOS 확증 → 역추세 숏 ×{bos_conflict_penalty}")

    # ── 거래량 페널티 [v3.3 patch] ───────────────────────────────
    # volume 가중치 5~9%로 낮아 0pt여도 raw_score 억제 ~2.5~4.5pt에 불과.
    # 보너스 하나(+4pt~)로 쉽게 상쇄되어 저거래량(주말 등) 신호 과다 발생.
    # → vol score 기준 명시적 덧셈 페널티로 추가 억제.
    vol_score = vol.get("score", 50.0)
    if vol_score < config.VOLUME_PENALTY_LOW_THRESHOLD:
        volume_penalty = config.VOLUME_PENALTY_LOW
        logger.info(
            f"[거래량패널티/{d.upper()}] "
            f"vol:{vol_score:.1f}pt < {config.VOLUME_PENALTY_LOW_THRESHOLD}pt "
            f"→ {config.VOLUME_PENALTY_LOW}pt  (ratio:{vol.get('ratio', 0):.2f}x)"
        )
    elif vol_score < config.VOLUME_PENALTY_MID_THRESHOLD:
        volume_penalty = config.VOLUME_PENALTY_MID
        logger.info(
            f"[거래량패널티/{d.upper()}] "
            f"vol:{vol_score:.1f}pt < {config.VOLUME_PENALTY_MID_THRESHOLD}pt "
            f"→ {config.VOLUME_PENALTY_MID}pt  (ratio:{vol.get('ratio', 0):.2f}x)"
        )
    else:
        volume_penalty = 0

    # ── 최종 점수 ────────────────────────────────────────────────
    soft_penalty  = mtf_penalty * exhaustion_mult * candle_momentum_mult * choch_penalty * bos_conflict_penalty
    micro_penalty = micro_result.get("total_penalty", 0) if micro_result else 0

    final_score = round(
        min(100.0, max(0.0,
            (base_score + bonus_total) * soft_penalty + micro_penalty + volume_penalty
        )), 2
    )

    # [v3.3] SIGNAL_MIN_SCORE 제거: regime_threshold 단독 기준
    regime_threshold = regime.get("threshold", config.REGIME_THRESHOLDS.get("TRENDING", 63))
    signal = (final_score >= regime_threshold)

    # ── 로그 ─────────────────────────────────────────────────────
    micro_note   = f" +micro{micro_penalty:+d}pt" if micro_penalty != 0 else ""
    vol_note     = f" +vol{volume_penalty:+d}pt"  if volume_penalty != 0 else ""
    soft_applied = soft_penalty < 1.0

    if soft_applied:
        logger.info(
            f"[Score/{d.upper()}] [{regime_name}]"
            f" raw:{raw_score:.1f} ×EMA{ema_mult:.2f}"
            + (f" ×게이트{gate_penalty:.2f}" if gate_penalty < 1.0 else "")
            + f" → base:{base_score:.1f}pt"
            f" → (base:{base_score:.1f}+보너스{bonus_total}[cap:{bonus_cap}])"
            + (f" ×MTF{mtf_penalty:.2f}"              if mtf_penalty < 1.0           else "")
            + (f" ×소진{exhaustion_mult:.2f}"          if exhaustion_mult < 1.0       else "")
            + (f" ×캔들{candle_momentum_mult:.2f}"     if candle_momentum_mult < 1.0  else "")
            + (f" ×CHoCH{choch_penalty:.2f}"           if choch_penalty < 1.0         else "")
            + (f" ×BOS충돌{bos_conflict_penalty:.2f}"  if bos_conflict_penalty < 1.0  else "")
            + micro_note + vol_note
            + f" = {final_score:.1f}pt (임계:{regime_threshold}pt)"
            + (" 🚨 신호" if signal else "")
        )
    else:
        logger.info(
            f"[Score/{d.upper()}] [{regime_name}]"
            f" raw:{raw_score:.1f} ×EMA{ema_mult:.2f}"
            + (f" ×게이트{gate_penalty:.2f}" if gate_penalty < 1.0 else "")
            + f" +보너스{bonus_total}[cap:{bonus_cap}]"
            + micro_note + vol_note
            + f" = {final_score:.1f}pt (임계:{regime_threshold}pt)"
            + (" 🚨 신호" if signal else "")
        )

    breakdown = _build_breakdown(
        d, scores, weights, raw_score, ema_mult, gate_penalty,
        mtf_penalty, exhaustion_mult, choch_penalty, bos_conflict_penalty,
        bonuses, bonus_cap, final_score, gate, regime, micro_penalty, volume_penalty
    )
    return {
        "direction": d, "final_score": final_score, "raw_score": round(raw_score, 2),
        "weighted_score": round(base_score, 2), "ema_multiplier": ema_mult, "adx_multiplier": 1.0,
        "passed_gate": True, "signal": signal, "component_scores": scores,
        "bonuses": bonuses, "bonus_total": bonus_total, "bonus_cap": bonus_cap, "gate_info": gate,
        "bb_suppressed": False, "bb_suppress_reason": None, "regime": regime,
        "regime_threshold": regime_threshold, "breakdown": breakdown,
        "mtf_penalty": mtf_penalty, "exhaustion_mult": exhaustion_mult,
        "candle_momentum_mult": candle_momentum_mult, "choch_penalty": choch_penalty,
        "bos_conflict_penalty": bos_conflict_penalty,
        "volume_penalty": volume_penalty,
    }


def _build_breakdown(d, scores, weights, raw, ema_m, pen,
                     mtf_m, exh_m, choch_m, bos_m,
                     bonuses, bonus_cap, final, gate, regime,
                     micro_penalty=0, volume_penalty=0) -> str:
    label = "🟢 롱" if d == "long" else "🔴 숏"
    lines = [f"{label} 진입 점수  [{regime.get('icon','')} {regime.get('regime','')}]"]
    for key, weight in weights.items():
        s = scores.get(key, 0.0); contrib = s * weight
        bar = "█"*int(s/10) + "░"*(10-int(s/10))
        lines.append(f"  {_score_label(key):<14} {bar} {s:>5.1f}pt × {weight:.0%} = {contrib:>4.1f}pt")
    lines.append(f"  {'─'*46}")
    lines.append(f"  가중합                           {raw:>5.1f}pt")
    if ema_m   < 1.0: lines.append(f"  EMA 역방향 배율         × {ema_m:.2f}")
    if pen     < 1.0: lines.append(f"  복합 페널티             × {pen:.2f}")
    if mtf_m   < 1.0: lines.append(f"  MTF RSI 패널티          × {mtf_m:.2f}")
    if exh_m   < 1.0: lines.append(f"  EXPLOSIVE 소진 패널티   × {exh_m:.2f}")
    if choch_m < 1.0: lines.append(f"  CHoCH 역방향 패널티     × {choch_m:.2f}")
    if bos_m   < 1.0: lines.append(f"  BOS 역방향 패널티       × {bos_m:.2f}")
    if bonuses:
        lines.append(f"  보너스 (상한:{bonus_cap}pt):")
        for name, val in bonuses:
            lines.append(f"    + {name}: +{val}pt")
    if micro_penalty != 0:
        lines.append(f"  마이크로구조 패널티       {micro_penalty:+d}pt")
    if volume_penalty != 0:
        lines.append(f"  거래량 페널티             {volume_penalty:+d}pt")
    lines.append(f"  {'─'*46}")
    lines.append(f"  최종 (임계:{regime.get('threshold',63)}pt)  {final:>5.1f}pt")
    return "\n".join(lines)


def _score_label(key: str) -> str:
    return {
        "rsi":              "RSI",
        "bollinger":        "볼린저밴드",
        "funding_rate":     "펀딩비",
        "long_short_ratio": "롱숏비율",
        "taker_volume":     "Taker비율",
        "volume":           "거래량",
    }.get(key, key)


def evaluate_signals(analysis: dict,
                     micro_long: dict = None,
                     micro_short: dict = None) -> dict:
    lr = calculate_entry_score(analysis, "long",  micro_long)
    sr = calculate_entry_score(analysis, "short", micro_short)
    ls = lr["final_score"]; ss = sr["final_score"]
    primary = None; suppressed = None

    if lr["signal"] and sr["signal"]:
        if abs(ls - ss) < 5.0: suppressed = f"양방향 차이 {abs(ls-ss):.1f}pt < 5pt"
        else:                   primary = "long" if ls > ss else "short"
    elif lr["signal"]: primary = "long"
    elif sr["signal"]: primary = "short"

    ps = ls if primary == "long" else (ss if primary == "short" else 0.0)
    if primary: logger.info(f"[Signal] 🚨 {primary.upper()} {ps:.1f}pt")
    else:       logger.info(f"[Signal] 없음 — 롱:{ls:.1f} 숏:{ss:.1f}")
    return {"long": lr, "short": sr, "primary": primary, "primary_score": ps, "suppressed": suppressed}


# ══════════════════════════════════════════════════════════════
# 상태 파일 (쿨다운 / 이전 국면)
# ══════════════════════════════════════════════════════════════

def _load_state() -> dict:
    if os.path.exists(config.SIGNAL_STATE_FILE):
        try:
            with open(config.SIGNAL_STATE_FILE) as f: return json.load(f)
        except: pass
    return {}

def _save_state(state: dict) -> None:
    try:
        d = os.path.dirname(config.SIGNAL_STATE_FILE)
        if d: os.makedirs(d, exist_ok=True)
        with open(config.SIGNAL_STATE_FILE, "w") as f: json.dump(state, f)
    except Exception as e: logger.warning(f"[Cooldown] 저장 실패: {e}")

def _get_effective_cooldown(symbol: str, direction: str, current_price: float) -> int:
    state = _load_state()
    last_price = state.get(f"{symbol}_{direction}_last_price", 0)
    if not last_price: return config.SIGNAL_COOLDOWN_MINUTES
    change_pct       = (current_price - last_price) / last_price
    directional_move = change_pct if direction == "long" else -change_pct
    if directional_move >= config.PRICE_MOVE_SUPPRESS_STRONG: return config.COOLDOWN_SUPPRESSED_STRONG
    if directional_move >= config.PRICE_MOVE_SUPPRESS_MILD:   return config.COOLDOWN_SUPPRESSED_MILD
    if directional_move <= config.PRICE_MOVE_RESET_THRESHOLD: return 0
    return config.SIGNAL_COOLDOWN_MINUTES

def is_in_cooldown(symbol: str, direction: str, current_price: float = 0.0) -> bool:
    state = _load_state()
    last  = state.get(f"{symbol}_{direction}")
    if last is None: return False
    effective_minutes = _get_effective_cooldown(symbol, direction, current_price)
    if effective_minutes == 0: return False
    elapsed  = datetime.now(timezone.utc) - datetime.fromisoformat(last)
    cooldown = timedelta(minutes=effective_minutes)
    if elapsed < cooldown:
        remain = int((cooldown - elapsed).total_seconds() / 60)
        logger.info(f"[Cooldown] {symbol} {direction.upper()} — 잔여:{remain}분")
        return True
    return False

def record_signal_sent(symbol: str, direction: str, current_price: float = 0.0) -> None:
    state = _load_state()
    state[f"{symbol}_{direction}"]             = datetime.now(timezone.utc).isoformat()
    if current_price > 0:
        state[f"{symbol}_{direction}_last_price"] = current_price
    _save_state(state)

def _load_prev_regime(symbol: str) -> str:
    return _load_state().get(f"{symbol}_prev_regime", "")

def _save_prev_regime(symbol: str, regime_name: str) -> None:
    state = _load_state()
    state[f"{symbol}_prev_regime"] = regime_name
    _save_state(state)


# ══════════════════════════════════════════════════════════════
# 파이프라인
# ══════════════════════════════════════════════════════════════

def run_scoring_pipeline(symbol: str, analysis: dict,
                          market_data: dict = None) -> dict:
    import datetime as dt
    logger.info(f"{'─'*50}")
    logger.info(f"🎯 점수 산출: {symbol}")

    regime      = analysis.get("regime", {})
    regime_name = regime.get("regime", "UNKNOWN")
    logger.info(f"  {regime.get('icon','')} 국면: {regime_name} — {regime.get('description','')}")

    prev_regime = _load_prev_regime(symbol)
    if prev_regime:
        analysis["prev_regime"] = prev_regime
        logger.info(f"  이전 국면: {prev_regime}")

    micro_long  = {"total_penalty": 0, "raw_total": 0, "details": [], "suggested_entry": None}
    micro_short = {"total_penalty": 0, "raw_total": 0, "details": [], "suggested_entry": None}

    if market_data:
        try:
            from microstructure_analyzer import compute_microstructure_penalties
            micro_data    = market_data.get("microstructure", {})
            price         = market_data.get("price") or analysis.get("current_price") or 0.0
            taker_buy_pct = market_data.get("taker_volume", {}).get("buy_pct", 50.0)
            pos_long_pct  = market_data.get("ls_ratio", {}).get("long_pct", 0.5)
            percent_b     = analysis.get("bollinger", {}).get("pct_b", 0.5)

            micro_long  = compute_microstructure_penalties(
                micro_data=micro_data, current_price=price, direction="long",
                regime=regime_name, percent_b=percent_b,
                taker_buy_pct=taker_buy_pct, position_long_pct=pos_long_pct,
            )
            micro_short = compute_microstructure_penalties(
                micro_data=micro_data, current_price=price, direction="short",
                regime=regime_name, percent_b=percent_b,
                taker_buy_pct=taker_buy_pct, position_long_pct=pos_long_pct,
            )
        except Exception as e:
            logger.warning(f"[Pipeline] 마이크로구조 계산 실패 (스킵): {e}")

    signals = evaluate_signals(analysis, micro_long=micro_long, micro_short=micro_short)
    primary = signals["primary"]
    ps      = signals["primary_score"]

    current_price = analysis.get("current_price") or 0.0
    cooldown = False; should_notify = False

    if primary:
        if is_in_cooldown(symbol, primary, current_price):
            cooldown = True
            logger.info(f"[Pipeline] {symbol} {primary.upper()} — 쿨다운 스킵")
        else:
            should_notify = True
            logger.info(f"[Pipeline] ✅ {symbol} {primary.upper()} {ps:.1f}pt — 알림 예정")
    else:
        logger.info(f"[Pipeline] {symbol} — 신호 없음")

    _save_prev_regime(symbol, regime_name)

    micro_result = micro_long if primary == "long" else micro_short
    return {
        "symbol":        symbol,
        "should_notify": should_notify,
        "direction":     primary,
        "score":         ps,
        "signal_result": signals,
        "cooldown_skip": cooldown,
        "regime":        regime,
        "scored_at":     dt.datetime.now(timezone.utc).isoformat(),
        "micro_result":  micro_result,
    }