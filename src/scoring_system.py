"""
scoring_system.py — 점수 산출 (A·E·F·H 개선판 + A2·B·C·D-alt)
A2: 가격 변화율 기반 동적 쿨다운 (밀집 신호 억제)
B : BB 억제 로직 국면 분기 (RANGING 반전 보너스)
C : MTF RSI 극단값 패널티
D-alt: EXPLOSIVE 소진 감지 (임계값 유지, 배율 패널티)
"""
import json, logging, os
from datetime import datetime, timezone, timedelta
import config

logger = logging.getLogger(__name__)


def calculate_entry_score(analysis: dict, direction: str) -> dict:
    d    = direction
    gate = analysis.get(f"gate_{d}", {})
    gate_penalty = gate.get("funding_penalty", 1.0)

    rsi     = analysis.get("rsi",         {})
    bb      = analysis.get("bollinger",    {})
    funding = analysis.get("funding_rate", {})
    ls      = analysis.get("ls_ratio",     {})
    oi      = analysis.get("oi_change",    {})
    taker   = analysis.get("taker_volume", {})
    liq     = analysis.get("liquidations", {})
    vol     = analysis.get("volume",       {})
    adx_15m = analysis.get("adx_15m",      {})
    regime  = analysis.get("regime",       {})
    mtf_rsi = analysis.get("rsi",           {})   # C/D-alt: value_1h, value_4h는 rsi 키 아래 저장됨

    ema_info      = analysis.get(f"ema_{d}", {})
    reverse_count = ema_info.get("reverse_count", 0)

    scores = {
        "rsi":              rsi.get(f"{d}_score",    50.0),
        "bollinger":        bb.get(f"{d}_score",     50.0),
        "funding_rate":     funding.get(f"{d}_score", 50.0),
        "long_short_ratio": ls.get(f"{d}_score",     50.0),
        "taker_volume":     taker.get(f"{d}_score",  50.0),
        "oi_change":        oi.get(f"{d}_score",     50.0),
        "volume":           vol.get("score",          0.0),
    }

    regime_name = regime.get("regime", "UNKNOWN")
    weights     = config.REGIME_SCORE_WEIGHTS.get(regime_name, config.SCORE_WEIGHTS)

    # ── 개선1: EMA 3TF 역방향 시 LS 중립화 ──────────────────────────
    # 조건: EMA 3TF 모두 역방향 AND BB lower_breakout 아님
    # BB lower_breakout(pct_b < 0) = 실제 밴드 이탈 반전 패턴 → 면제
    #   이 경우 숏 포지션 쏠림은 "추세 확인"이 아닌 "과도 청산 대기" → contrarian 유효
    # BB lower_breakout 아닌 경우 = 하락 추세 지속 중 → contrarian 해석 역효과
    bb_state_str  = bb.get("state", "")
    bb_pct_b      = bb.get("pct_b", 0.5)
    bb_lower_breakout = (bb_state_str == "lower_breakout")   # pct_b < 0, 실제 이탈
    bb_upper_breakout = (bb_state_str == "upper_breakout")   # pct_b > 1, 실제 이탈
    # 신호 방향 기준 BB 이탈 면제 여부
    bb_reversal_exempt = (
        (d == "long"  and bb_lower_breakout) or
        (d == "short" and bb_upper_breakout)
    )
    ema_all_reverse = (reverse_count == 3)

    if ema_all_reverse and not bb_reversal_exempt:
        ls_weight     = weights.get("long_short_ratio", 0.15)
        ls_raw_before = scores["long_short_ratio"]
        # LS 점수를 중립(50)으로 강제
        scores["long_short_ratio"] = 50.0
        ls_raw_reduction = (ls_raw_before - 50.0) * ls_weight
        logger.info(
            f"[개선1/EMA3역방향] LS 점수 중립화: {ls_raw_before:.0f}→50pt "            f"(raw -{ls_raw_reduction:.1f}pt) [{d.upper()}]"
        )

    raw_score   = sum(scores[k] * weights[k] for k in weights)

    # A: 국면별 EMA 배율
    ema_table = config.REGIME_EMA_MULTIPLIERS.get(regime_name, config.EMA_MULTIPLIER)
    ema_mult  = ema_table.get(reverse_count, 1.0)
    if ema_mult != ema_info.get("multiplier", 1.0):
        logger.info(
            f"[EMA/{d.upper()}] 국면({regime_name}) 전용 배율 ×{ema_mult:.2f} 적용 "
            f"(기본 ×{ema_info.get('multiplier', 1.0):.2f})"
        )
    ema_adjusted = raw_score * ema_mult

    # ADX 배율
    adx_mult     = adx_15m.get("multiplier", 1.0)
    # RANGING 국면 + BB 극단 반전 구간: ADX 횡보 억제 완화 (롱/숏 대칭)
    # 횡보장이라도 BB 극단 이탈은 유효한 반전 신호 → ×0.70 → ×0.85 보정
    bb_state_adx = bb.get("state", "")
    if (regime_name == "RANGING" and adx_mult < 0.85 and (
        (d == "long"  and bb_state_adx in ("lower_breakout", "near_lower")) or
        (d == "short" and bb_state_adx in ("upper_breakout", "near_upper"))
    )):
        adx_mult = 0.85
        logger.info(f"[ADX보정] RANGING+BB반전 → ×0.85 [{d.upper()}]")
    adx_adjusted = ema_adjusted * adx_mult

    # 게이트 페널티
    penalized = adx_adjusted * gate_penalty
    # ▼ 개선안 5: EMA/ADX/gate 적용 후 base 저장 (soft risk penalty 전)
    # MTF RSI / 소진 / 연속캔들 패널티는 보너스에도 동일하게 적용
    base_before_soft = penalized

    # ── C: MTF RSI 극단값 패널티 (롱/숏 완전 대칭) ──
    rsi_1h = mtf_rsi.get("value_1h", 50.0)
    rsi_4h = mtf_rsi.get("value_4h", 50.0)
    mtf_penalty = 1.0
    mtf_penalty_reason = None

    if d == "long":
        if rsi_1h >= config.MTF_RSI_OVERBOUGHT_1H_EXTREME:
            # 극단 과매수(1h ≥ 78): 4h 조건 없이 STRONG 패널티
            mtf_penalty = config.MTF_RSI_PENALTY_STRONG
            mtf_penalty_reason = (
                f"MTF RSI 극단과매수(1h:{rsi_1h:.1f} ≥ {config.MTF_RSI_OVERBOUGHT_1H_EXTREME}) → 롱 ×{mtf_penalty}"
            )
        elif rsi_1h >= config.MTF_RSI_OVERBOUGHT_1H and rsi_4h >= config.MTF_RSI_OVERBOUGHT_4H:
            mtf_penalty = config.MTF_RSI_PENALTY_STRONG
            mtf_penalty_reason = (
                f"MTF RSI 강과매수(1h:{rsi_1h:.1f} 4h:{rsi_4h:.1f}) → 롱 ×{mtf_penalty}"
            )
        elif rsi_1h >= config.MTF_RSI_OVERBOUGHT_1H_MILD:
            mtf_penalty = config.MTF_RSI_PENALTY_MILD
            mtf_penalty_reason = f"MTF RSI 약과매수(1h:{rsi_1h:.1f}) → 롱 ×{mtf_penalty}"
    elif d == "short":
        if rsi_1h <= config.MTF_RSI_OVERSOLD_1H_EXTREME:
            # 극단 과매도(1h ≤ 22): 4h 조건 없이 STRONG 패널티
            # 이 구간에서 숏 = 낙폭 과대 반등 위험 최고조 → 강력 억제
            mtf_penalty = config.MTF_RSI_PENALTY_STRONG
            mtf_penalty_reason = (
                f"MTF RSI 극단과매도(1h:{rsi_1h:.1f} ≤ {config.MTF_RSI_OVERSOLD_1H_EXTREME}) → 숏 ×{mtf_penalty}"
            )
        elif rsi_1h <= config.MTF_RSI_OVERSOLD_1H and rsi_4h <= config.MTF_RSI_OVERSOLD_4H:
            mtf_penalty = config.MTF_RSI_PENALTY_STRONG
            mtf_penalty_reason = (
                f"MTF RSI 강과매도(1h:{rsi_1h:.1f} 4h:{rsi_4h:.1f}) → 숏 ×{mtf_penalty}"
            )
        elif rsi_1h <= config.MTF_RSI_OVERSOLD_1H_MILD:
            mtf_penalty = config.MTF_RSI_PENALTY_MILD
            mtf_penalty_reason = f"MTF RSI 약과매도(1h:{rsi_1h:.1f}) → 숏 ×{mtf_penalty}"

    if mtf_penalty < 1.0:
        penalized *= mtf_penalty
        logger.info(f"[C/MTF-RSI/{d.upper()}] {mtf_penalty_reason}")

    # ── D-alt: EXPLOSIVE 소진 감지 패널티 (롱/숏 완전 대칭) ──
    exhaustion_mult   = 1.0
    exhaustion_reason = None

    if regime_name == "EXPLOSIVE":
        if d == "long" and rsi_1h >= config.EXPLOSIVE_EXHAUSTION_RSI_LONG:
            exhaustion_mult   = config.EXPLOSIVE_EXHAUSTION_PENALTY
            exhaustion_reason = (
                f"EXPLOSIVE 소진(1h RSI:{rsi_1h:.1f} ≥ {config.EXPLOSIVE_EXHAUSTION_RSI_LONG})"
                f" → 롱 ×{exhaustion_mult}"
            )
        elif d == "short" and rsi_1h <= config.EXPLOSIVE_EXHAUSTION_RSI_SHORT:
            exhaustion_mult   = config.EXPLOSIVE_EXHAUSTION_PENALTY
            exhaustion_reason = (
                f"EXPLOSIVE 소진(1h RSI:{rsi_1h:.1f} ≤ {config.EXPLOSIVE_EXHAUSTION_RSI_SHORT})"
                f" → 숏 ×{exhaustion_mult}"
            )

    if exhaustion_mult < 1.0:
        penalized *= exhaustion_mult
        logger.info(f"[D-alt/EXPLOSIVE/{d.upper()}] {exhaustion_reason}")

    # 5-2: OI Spike Filter
    oi_change_pct  = abs(oi.get("change_pct", 0.0))
    oi_direction   = oi.get("direction", "")
    taker_bias_raw = taker.get("bias", "neutral")
    if oi_change_pct >= config.OI_SPIKE_THRESHOLD:
        oi_spike_hit = (
            (d == "long"  and oi_direction == "increasing" and taker_bias_raw == "sell_dominant") or
            (d == "short" and oi_direction == "increasing" and taker_bias_raw == "buy_dominant")
        )
        if oi_spike_hit:
            penalized = max(0.0, penalized - config.OI_SPIKE_SCORE_PENALTY)
            logger.info(
                f"[OISpike/{d.upper()}] OI급증({oi_change_pct*100:.0f}%)+역방향 → "
                f"-{config.OI_SPIKE_SCORE_PENALTY}pt 차감"
            )

    # ── B: BB 연속 이탈 억제 — 국면별 분기 (롱/숏 완전 대칭) ──
    BB_STREAK    = 3
    lower_streak = bb.get("lower_streak", 0)
    upper_streak = bb.get("upper_streak", 0)
    bb_suppressed     = False
    bb_reason         = None
    bb_reversal_bonus = 0

    if d == "long" and lower_streak >= BB_STREAK:
        if regime_name == "TRENDING":
            bb_suppressed = True
            bb_reason = f"TRENDING BB 하단 {lower_streak}캔들 연속 이탈 — 롱 억제"
        elif regime_name == "RANGING":
            bb_reversal_bonus = config.BONUS_BB_RANGING_REVERSAL
            logger.info(
                f"[B/BB분기/{d.upper()}] RANGING BB 하단 반전 보너스 +{bb_reversal_bonus}pt"
            )

    elif d == "short" and upper_streak >= BB_STREAK:
        if regime_name == "TRENDING":
            bb_suppressed = True
            bb_reason = f"TRENDING BB 상단 {upper_streak}캔들 연속 이탈 — 숏 억제"
        elif regime_name == "RANGING":
            bb_reversal_bonus = config.BONUS_BB_RANGING_REVERSAL
            logger.info(
                f"[B/BB분기/{d.upper()}] RANGING BB 상단 반전 보너스 +{bb_reversal_bonus}pt"
            )

    if bb_suppressed:
        logger.info(f"[Score/{d.upper()}] ⛔ {bb_reason}")
        return {
            "direction": d, "final_score": 0.0, "raw_score": round(raw_score, 2),
            "weighted_score": 0.0, "ema_multiplier": ema_mult, "adx_multiplier": adx_mult,
            "passed_gate": True, "signal": False, "component_scores": scores,
            "bonuses": [], "bonus_total": 0, "gate_info": gate,
            "bb_suppressed": True, "bb_suppress_reason": bb_reason,
            "regime": regime, "breakdown": f"⛔ BB 연속 이탈 억제: {bb_reason}",
        }

    # ══ 보너스 계산 ══════════════════════════════════════════
    bonuses = []

    # B: RANGING BB 반전 보너스
    if bb_reversal_bonus > 0:
        label = "RANGING BB 하단 반전(롱)" if d == "long" else "RANGING BB 상단 반전(숏)"
        bonuses.append((label, bb_reversal_bonus))

    # ① 볼린저 극단 + RSI 다이버전스
    bb_extreme = bb.get("state", "") in (
        "lower_breakout", "near_lower", "upper_breakout", "near_upper"
    )
    has_div = rsi.get("bullish_divergence") if d == "long" else rsi.get("bearish_divergence")
    if bb_extreme and has_div:
        bonuses.append(("볼린저극단+RSI다이버전스", config.BONUS_BB_RSI_ALIGN))

    # ② 펀딩비 + 롱숏비율 동일 방향
    fr_bias = funding.get("bias", "neutral")
    ls_bias = ls.get("bias", "neutral")
    fr_ok = (fr_bias == "long_favorable"  if d == "long"  else fr_bias == "short_favorable")
    ls_ok = (ls_bias in ("long_favorable","long_extreme")
             if d == "long" else ls_bias in ("short_favorable","short_extreme"))
    if fr_ok and ls_ok:
        bonuses.append(("펀딩비+롱숏비율 동일방향", config.BONUS_FUNDING_LS_ALIGN))

    # ③ OI + Taker (TREND_STRONG 중복 방지는 아래에서)
    oi_interp  = oi.get("interpretation", "")
    taker_bias = taker.get("bias", "neutral")
    oi_taker_ok = (
        (d == "long"  and oi_interp == "bullish_trend_confirm" and taker_bias == "buy_dominant") or
        (d == "short" and oi_interp == "bearish_trend_confirm" and taker_bias == "sell_dominant")
    )

    # ④ 대규모 청산
    liq_signal = liq.get("signal", "none")
    liq_large  = liq.get("is_large", False)
    if liq_large and (
        (d == "long"  and liq_signal == "long_liq_detected") or
        (d == "short" and liq_signal == "short_liq_detected")
    ):
        bonuses.append(("대규모 청산 꼬리 감지", config.BONUS_LIQUIDATION))

    # ⑤ ADX 강한 추세
    if adx_15m.get("strength") == "strong":
        bonuses.append(("ADX 강한 추세", config.BONUS_ADX_STRONG))

    # ⑥ RSI 다이버전스 단독 — TRENDING 국면 한정
    # RANGING: 다이버전스가 너무 빈번하고 신뢰도 낮음 (노이즈)
    # TRENDING: 추세 진행 중 RSI 다이버전스 = 추세 전환 경고 = 신뢰도 높음
    if has_div and not (bb_extreme and has_div) and regime_name == "TRENDING":
        bonuses.append(("RSI다이버전스(TRENDING)", config.BONUS_RSI_DIVERGENCE))
        logger.info(f"[RSI다이버전스] ★ TRENDING 추세중 다이버전스 감지 +{config.BONUS_RSI_DIVERGENCE}pt [{d.upper()}]")

    # ── 방안2: 추세 지속 보너스 ─────────────────────────────
    ema_same        = ema_info.get("same_count", 0)
    ema_all_aligned = (ema_same == 3)
    taker_str       = taker.get("strength", "neutral")
    vol_strong      = vol.get("strong", False)
    adx_strong      = adx_15m.get("strength") in ("normal", "strong")
    bb_state        = bb.get("state", "")

    oi_confirms_long  = oi_interp in ("bullish_trend_confirm", "short_covering")
    oi_confirms_short = oi_interp in ("bearish_trend_confirm", "long_liquidation")

    trend_strong_ok = (
        ema_all_aligned and taker_str in ("strong", "mild") and (
            (d == "long"  and oi_confirms_long  and taker_bias == "buy_dominant") or
            (d == "short" and oi_confirms_short and taker_bias == "sell_dominant")
        )
    )
    if trend_strong_ok:
        label = "추세지속:EMA+OI+Taker(롱)" if d == "long" else "추세지속:EMA+OI+Taker(숏)"
        bonuses.append((label, config.BONUS_TREND_STRONG))
        logger.info(f"[방안2] ★ 추세지속 강 +{config.BONUS_TREND_STRONG}pt [{d.upper()}]")

    if ema_all_aligned and vol_strong and adx_strong:
        bonuses.append(("추세지속:EMA+거래량+ADX", config.BONUS_TREND_VOLUME))
        logger.info(f"[방안2] ★ 추세가속 +{config.BONUS_TREND_VOLUME}pt [{d.upper()}]")

    band_walking_ok = (
        ema_all_aligned and (
            (d == "long"  and bb_state in ("upper_breakout","near_upper")
             and bb.get("upper_streak",0) >= 2) or
            (d == "short" and bb_state in ("lower_breakout","near_lower")
             and bb.get("lower_streak",0) >= 2)
        )
    )
    if band_walking_ok:
        label = "Band Walking(상승)" if d == "long" else "Band Walking(하락)"
        bonuses.append((label, config.BONUS_BAND_WALKING))
        logger.info(f"[방안2] ★ Band Walking +{config.BONUS_BAND_WALKING}pt [{d.upper()}]")

    # ── 방안4: 눌림목 보너스 ────────────────────────────────
    pb_long_strong  = rsi.get("pullback_long_strong",  False)
    pb_short_strong = rsi.get("pullback_short_strong", False)
    pb_long         = rsi.get("pullback_long",         False)
    pb_short        = rsi.get("pullback_short",        False)

    pb_ok_strong = (
        (d == "long"  and pb_long_strong  and ema_same >= 2) or
        (d == "short" and pb_short_strong and ema_same >= 2)
    )
    pb_ok_weak = (
        (d == "long"  and pb_long  and not pb_long_strong  and ema_same >= 2) or
        (d == "short" and pb_short and not pb_short_strong and ema_same >= 2)
    )
    pb_ok_micro = (
        (d == "long"  and pb_long  and ema_same >= 1
         and not pb_ok_strong and not pb_ok_weak) or
        (d == "short" and pb_short and ema_same >= 1
         and not pb_ok_strong and not pb_ok_weak)
    )

    if pb_ok_strong:
        bonuses.append((f"눌림목강({d.upper()})", config.BONUS_PULLBACK_ENTRY))
        logger.info(f"[방안4] ★ 눌림목강 +{config.BONUS_PULLBACK_ENTRY}pt [{d.upper()}]")
    elif pb_ok_weak:
        bonuses.append((f"눌림목약({d.upper()})", config.BONUS_PULLBACK_ENTRY_WEAK))
        logger.info(f"[방안4] ★ 눌림목약 +{config.BONUS_PULLBACK_ENTRY_WEAK}pt [{d.upper()}]")
    elif pb_ok_micro:
        bonuses.append((f"눌림목미세({d.upper()})", config.BONUS_PULLBACK_ENTRY_MICRO))
        logger.info(f"[방안4] ★ 눌림목미세 +{config.BONUS_PULLBACK_ENTRY_MICRO}pt [{d.upper()}]")

    # OI+Taker — TREND_STRONG 중복 방지
    trend_strong_fired = any("추세지속:EMA+OI+Taker" in b[0] for b in bonuses)
    if oi_taker_ok and not trend_strong_fired:
        bonuses.append(("OI증가+Taker방향일치", config.BONUS_OI_TAKER_CONFIRM))

    # 4-2: Volume Explosion
    vol_avg_ratio = vol.get("ratio", 1.0)
    adx_val_raw   = adx_15m.get("adx", 0.0)
    if vol_avg_ratio >= 2.5 and adx_val_raw >= 22.0 and not ema_all_aligned:
        bonuses.append(("거래량폭발+ADX확인", config.BONUS_VOLUME_EXPLOSION))
        logger.info(f"[4-2] ★ Volume Explosion +{config.BONUS_VOLUME_EXPLOSION}pt [{d.upper()}]")

    # 4-4: Post-Squeeze Momentum
    prev_regime   = analysis.get("prev_regime", "")
    bb_just_broke = (
        (d == "long"  and bb_state in ("upper_breakout","near_upper")
         and bb.get("upper_streak",0) == 1) or
        (d == "short" and bb_state in ("lower_breakout","near_lower")
         and bb.get("lower_streak",0) == 1)
    )
    if ((prev_regime == "SQUEEZE" or regime_name == "EXPLOSIVE") and bb_just_broke):
        label = "Post-Squeeze 롱 돌파" if d == "long" else "Post-Squeeze 숏 돌파"
        bonuses.append((label, config.BONUS_POST_SQUEEZE))
        logger.info(f"[4-4] ★ Post-Squeeze +{config.BONUS_POST_SQUEEZE}pt [{d.upper()}]")

    # ── 신규 1: 추세 전환 경고 보너스 (롱/숏 완전 대칭) ──────────
    # 조건: RSI 극단 + Taker 역방향 전환 + Funding 극단 삼박자
    rsi_1h_val   = mtf_rsi.get("value_1h", 50.0)
    taker_buy_r  = taker.get("buy_ratio",  0.5)
    taker_sell_r = taker.get("sell_ratio", 0.5)
    funding_rate = funding.get("rate", 0.0)

    reversal_warning = False
    if d == "short":
        # 롱→숏 전환 경고: RSI 과매수 + 매도 압력 + 펀딩 양수
        reversal_warning = (
            rsi_1h_val  >= config.REVERSAL_RSI_1H_OB and
            taker_sell_r >= config.REVERSAL_TAKER_SELL and
            funding_rate >= config.REVERSAL_FUNDING_POS
        )
    elif d == "long":
        # 숏→롱 전환 경고: RSI 과매도 + 매수 압력 + 펀딩 음수
        reversal_warning = (
            rsi_1h_val  <= config.REVERSAL_RSI_1H_OS and
            taker_buy_r  >= config.REVERSAL_TAKER_BUY and
            funding_rate <= config.REVERSAL_FUNDING_NEG
        )

    if reversal_warning:
        label = "추세전환경고(롱→숏)" if d == "short" else "추세전환경고(숏→롱)"
        bonuses.append((label, config.BONUS_TREND_REVERSAL_WARNING))
        logger.info(f"[신규1] ★ {label} +{config.BONUS_TREND_REVERSAL_WARNING}pt")

    # ── 신규 2: LS 방향 확인 보너스 (롱/숏 완전 대칭) ──────────
    # LS 포지션 쏠림이 신호 방향과 일치할 때 신뢰도 강화
    ls_bias = ls.get("bias", "neutral")
    ls_confirms_long  = ls_bias in ("long_momentum", "long_lean", "long_favorable", "long_extreme")
    ls_confirms_short = ls_bias in ("short_momentum", "short_lean", "short_favorable", "short_extreme")

    if (d == "long"  and ls_confirms_long) or (d == "short" and ls_confirms_short):
        label = f"LS방향확인({ls_bias})"
        bonuses.append((label, config.BONUS_LS_DIRECTION_CONFIRM))
        logger.info(f"[신규2] ★ {label} +{config.BONUS_LS_DIRECTION_CONFIRM}pt [{d.upper()}]")


    # ══ 트레이더 업그레이드 보너스 ══════════════════════════════

    # ── 캔들 패턴 (분석 데이터에서 읽기) ────────────────────────
    candle = analysis.get("candle_pattern", {})
    if d == "short":
        if candle.get("bearish_pin"):
            bonuses.append(("베어리시핀바", config.BONUS_CANDLE_PIN_BAR))
            logger.info(f"[캔들] ★ 베어리시핀바 +{config.BONUS_CANDLE_PIN_BAR}pt")
        elif candle.get("bearish_engulf"):
            bonuses.append(("베어리시인걸핑", config.BONUS_CANDLE_ENGULFING))
            logger.info(f"[캔들] ★ 베어리시인걸핑 +{config.BONUS_CANDLE_ENGULFING}pt")
        if candle.get("consecutive_bear") and not candle.get("bearish_pin"):
            bonuses.append(("연속음봉3", config.BONUS_CANDLE_CONSECUTIVE))
    elif d == "long":
        if candle.get("bullish_pin"):
            bonuses.append(("불리시핀바", config.BONUS_CANDLE_PIN_BAR))
            logger.info(f"[캔들] ★ 불리시핀바 +{config.BONUS_CANDLE_PIN_BAR}pt")
        elif candle.get("bullish_engulf"):
            bonuses.append(("불리시인걸핑", config.BONUS_CANDLE_ENGULFING))
            logger.info(f"[캔들] ★ 불리시인걸핑 +{config.BONUS_CANDLE_ENGULFING}pt")
        if candle.get("consecutive_bull") and not candle.get("bullish_pin"):
            bonuses.append(("연속양봉3", config.BONUS_CANDLE_CONSECUTIVE))

    # ── 시장 구조 (Lower High / Failed Breakout) ─────────────────
    ms = analysis.get("market_structure", {})
    if d == "short":
        if ms.get("lower_high"):
            bonuses.append(("LowerHigh구조", config.BONUS_MARKET_STRUCT_TREND))
            logger.info(f"[구조] ★ Lower High +{config.BONUS_MARKET_STRUCT_TREND}pt")
        if ms.get("failed_breakout"):
            bonuses.append(("돌파실패", config.BONUS_FAILED_BREAKOUT))
            logger.info(f"[구조] ★ 돌파실패(반전강력) +{config.BONUS_FAILED_BREAKOUT}pt")
    elif d == "long":
        if ms.get("higher_low"):
            bonuses.append(("HigherLow구조", config.BONUS_MARKET_STRUCT_TREND))
            logger.info(f"[구조] ★ Higher Low +{config.BONUS_MARKET_STRUCT_TREND}pt")
        if ms.get("failed_breakdown"):
            bonuses.append(("붕괴실패", config.BONUS_FAILED_BREAKOUT))
            logger.info(f"[구조] ★ 붕괴실패(반전강력) +{config.BONUS_FAILED_BREAKOUT}pt")

    # ── 거래량-가격 다이버전스 ────────────────────────────────────
    vpd = analysis.get("vol_price_div", {})
    if d == "short" and vpd.get("bearish_vol_div"):
        bonuses.append(("거래량약세다이버전스", config.BONUS_VOL_PRICE_DIV))
        logger.info(f"[거래량] ★ 가격신고가+거래량감소 +{config.BONUS_VOL_PRICE_DIV}pt")
    elif d == "long" and vpd.get("bullish_vol_div"):
        bonuses.append(("거래량강세다이버전스", config.BONUS_VOL_PRICE_DIV))
        logger.info(f"[거래량] ★ 가격신저가+거래량증가 +{config.BONUS_VOL_PRICE_DIV}pt")

    # ── 펀딩비 극단 보너스 ────────────────────────────────────────
    fund_rate = funding.get("rate", 0.0)
    if d == "short" and fund_rate >= config.FUNDING_EXTREME_SHORT:
        bonuses.append((f"펀딩극단숏({fund_rate*100:.2f}%)", config.BONUS_FUNDING_EXTREME))
        logger.info(f"[펀딩극단] ★ 롱레버리지과열 {fund_rate*100:.2f}% → 숏 +{config.BONUS_FUNDING_EXTREME}pt")
    elif d == "long" and fund_rate <= config.FUNDING_EXTREME_LONG:
        bonuses.append((f"펀딩극단롱({fund_rate*100:.2f}%)", config.BONUS_FUNDING_EXTREME))
        logger.info(f"[펀딩극단] ★ 숏레버리지과열 {fund_rate*100:.2f}% → 롱 +{config.BONUS_FUNDING_EXTREME}pt")

    # ── ATR 모멘텀 (방향 확장 중) ────────────────────────────────
    atr = analysis.get("atr", {})
    if atr.get("ratio", 1.0) >= config.ATR_MOMENTUM_RATIO:
        bonuses.append((f"ATR모멘텀({atr.get('ratio',1):.1f}x)", config.BONUS_ATR_MOMENTUM))
        logger.info(f"[ATR] ★ 변동성 방향 확장 {atr.get('ratio',1):.1f}x +{config.BONUS_ATR_MOMENTUM}pt [{d.upper()}]")

    # ── 소진 상태에서 추세확인형 보너스 제거 (개선안 5 보완) ──────────
    # 소진 = "추세가 이미 한계에 달함"과 추세확인 보너스는 논리 모순
    # 패널티(×0.85×0.88)가 보너스에 의해 상쇄되는 구조적 문제 차단
    if exhaustion_mult < 1.0:
        trend_confirm_names = {"LowerHigh구조", "HigherLow구조", "연속음봉3", "연속양봉3",
                               "OI증가+Taker방향일치", "볼린저극단+RSI다이버전스"}
        removed = [(n, v) for n, v in bonuses if n in trend_confirm_names]
        bonuses  = [(n, v) for n, v in bonuses if n not in trend_confirm_names]
        if removed:
            logger.info(
                f"[소진보너스제거] EXPLOSIVE 소진 상태 — "
                f"추세확인 보너스 제거: {[n for n,_ in removed]}"
            )

    # ── 개선4: EMA 3TF 역방향 시 반전 보너스 75% 감산 ──────────────
    # BB lower_breakout(실제 이탈) 상태는 면제 — 진짜 반전 패턴이기 때문
    # EMA 역방향 추세에서 반전 보너스의 신뢰도는 크게 낮아짐
    _REVERSAL_BONUS_NAMES = {
        "거래량강세다이버전스", "거래량약세다이버전스",
        "RANGING BB 하단 반전(롱)", "RANGING BB 상단 반전(숏)",
        "볼린저극단+RSI다이버전스",
    }
    if ema_all_reverse and not bb_reversal_exempt:
        new_bonuses_4 = []
        for _n, _v in bonuses:
            if _n in _REVERSAL_BONUS_NAMES:
                _discounted = round(_v * 0.25)   # 75% 감산 → 25%만 유지
                logger.info(
                    f"[개선4/EMA3역방향] {_n} 75% 감산: {_v}→{_discounted}pt [{d.upper()}]"
                )
                new_bonuses_4.append((_n, _discounted))
            else:
                new_bonuses_4.append((_n, _v))
        bonuses = new_bonuses_4

    # ── 개선4: EMA 3TF 역방향 시 LS방향확인 보너스 제거 ──────────────
    if ema_all_reverse and not bb_reversal_exempt:
        _before = len(bonuses)
        bonuses = [(_n, _v) for _n, _v in bonuses if "LS방향확인" not in _n]
        if len(bonuses) < _before:
            logger.info(f"[개선1/EMA3역방향] LS방향확인 보너스 제거 [{d.upper()}]")

    # ── 개선5: Taker 역방향 강세 시 캔들 보너스 60% 감산 ────────────
    # Taker 실시간 체결 방향이 신호와 반대면 캔들 패턴의 신뢰도 하락
    # 단기 기술적 반등 캔들 vs 실제 매수 유입을 구분
    _taker_bias = taker.get("bias", "neutral")
    _taker_against = (
        (d == "long"  and _taker_bias == "sell_dominant") or
        (d == "short" and _taker_bias == "buy_dominant")
    )
    _CANDLE_BONUS_NAMES = {
        "불리시핀바", "베어리시핀바",
        "불리시인걸핑", "베어리시인걸핑",
        "연속양봉3", "연속음봉3",
    }
    if _taker_against:
        new_bonuses_5 = []
        for _n, _v in bonuses:
            if _n in _CANDLE_BONUS_NAMES:
                _discounted = round(_v * 0.40)   # 60% 감산 → 40%만 유지
                logger.info(
                    f"[개선5/Taker역방향] {_n} 60% 감산: {_v}→{_discounted}pt [{d.upper()}]"
                )
                new_bonuses_5.append((_n, _discounted))
            else:
                new_bonuses_5.append((_n, _v))
        bonuses = new_bonuses_5

    # ── 보너스 상한선 35pt (기존 유지)
    BONUS_CAP   = 35
    bonus_total = min(BONUS_CAP, sum(v for _, v in bonuses))
    if sum(v for _, v in bonuses) > BONUS_CAP:
        logger.info(f"[보너스캡] {sum(v for _,v in bonuses)}pt → {BONUS_CAP}pt")

    # ── 개선안 3: 연속캔들 모멘텀 역방향 페널티 ─────────────────────────
    # 신호 방향과 현재 캔들 모멘텀이 반대 = 타이밍 불량
    # 국면별 차등 + 롱+BB하단이탈 면제 (낙폭과대 반전은 연속음봉이 정상)
    candle_momentum_mult = 1.0
    candle_pattern = analysis.get("candle_pattern", {})
    bb_pct_b  = bb.get("pct_b",  0.5)
    bb_state_c = bb.get("state", "")

    if d == "short" and candle_pattern.get("consecutive_bull"):
        # 연속양봉 진행 중 숏 진입 = 상승 모멘텀이 아직 살아있는 타이밍
        if regime_name == "TRENDING":
            candle_momentum_mult = config.CANDLE_MOMENTUM_PENALTY_TRENDING
        elif regime_name == "EXPLOSIVE":
            candle_momentum_mult = config.CANDLE_MOMENTUM_PENALTY_EXPLOSIVE
        else:  # RANGING, SQUEEZE
            candle_momentum_mult = config.CANDLE_MOMENTUM_PENALTY_RANGING
        logger.info(
            f"[캔들모멘텀] ⚠️ 연속양봉중 숏 진입 타이밍 불량"
            f" ×{candle_momentum_mult:.2f} [{regime_name}]"
        )

    elif d == "long" and candle_pattern.get("consecutive_bear"):
        # BB 하단 이탈 구간은 면제: 연속음봉 = 낙폭 과대 과매도 확인 신호
        # (BB 하단 이탈 중 롱은 반전 진입이므로 연속음봉과 모순 아님)
        bb_lower_exempt = (
            bb_state_c in ("lower_breakout", "near_lower") or
            bb_pct_b <= 0.15
        )
        if bb_lower_exempt:
            logger.info("[캔들모멘텀] BB하단이탈 구간 — 연속음봉+롱 페널티 면제")
        else:
            # BB 중간~상단에서 연속음봉 + 롱 = 하락 추세 지속 중 역방향 진입
            if regime_name == "TRENDING":
                candle_momentum_mult = config.CANDLE_MOMENTUM_PENALTY_TRENDING
            elif regime_name == "EXPLOSIVE":
                candle_momentum_mult = config.CANDLE_MOMENTUM_PENALTY_EXPLOSIVE
            else:
                candle_momentum_mult = config.CANDLE_MOMENTUM_PENALTY_RANGING
            logger.info(
                f"[캔들모멘텀] ⚠️ 연속음봉중 롱 진입 타이밍 불량"
                f" ×{candle_momentum_mult:.2f} [{regime_name}]"
            )

    # ── 개선안 5: 보너스도 soft risk 패널티 영향받는 공식 ────────────────
    # 기존: (base × EMA × ADX × gate × MTF × 소진) + bonus
    # 변경: (base × EMA × ADX × gate + bonus) × MTF × 소진 × 캔들모멘텀
    #
    # 근거:
    #   EMA/ADX/gate = 방향성 패널티 → base 신호 강도에만 적용
    #   MTF RSI / 소진 / 캔들모멘텀 = 타이밍/리스크 패널티
    #     → "이 시점에서의 진입 자체"가 위험하므로 보너스 패턴도 신뢰도 하락
    #     → (base + bonus) 전체에 적용
    #
    # 페널티 없는 경우(모두 1.0): 기존 공식과 동일
    soft_penalty = mtf_penalty * exhaustion_mult * candle_momentum_mult
    final_score  = round(min(100.0, max(0.0, (base_before_soft + bonus_total) * soft_penalty)), 2)

    regime_threshold = regime.get("threshold", config.REGIME_THRESHOLDS.get("TRENDING", 60))
    signal           = final_score >= regime_threshold and final_score >= config.SIGNAL_MIN_SCORE

    # soft 패널티 발동 여부 (개선안5 공식 적용 여부 표시용)
    soft_applied = soft_penalty < 1.0

    if soft_applied:
        # soft 패널티 발동 시: 새 공식 명시 (base + bonus) × soft
        base_before_soft_display = round(base_before_soft, 1)
        logger.info(
            f"[Score/{d.upper()}] [{regime_name}가중치] raw:{raw_score:.1f}"
            f" ×EMA{ema_mult:.2f} ×ADX{adx_mult:.2f}"
            + (f" ×페널티{gate_penalty:.2f}" if gate_penalty < 1.0 else "")
            + f" → base:{base_before_soft_display}pt"
            + f" → (base:{base_before_soft_display}+보너스{bonus_total})"
            + (f" ×MTF{mtf_penalty:.2f}" if mtf_penalty < 1.0 else "")
            + (f" ×소진{exhaustion_mult:.2f}" if exhaustion_mult < 1.0 else "")
            + (f" ×캔들{candle_momentum_mult:.2f}" if candle_momentum_mult < 1.0 else "")
            + f" = {final_score:.1f}pt"
            + f" (임계값:{regime_threshold}pt)"
            + (" 🚨 신호" if signal else " — 미달")
        )
    else:
        # soft 패널티 없음: 기존 포맷 (공식 동일)
        logger.info(
            f"[Score/{d.upper()}] [{regime_name}가중치] raw:{raw_score:.1f}"
            f" ×EMA{ema_mult:.2f} ×ADX{adx_mult:.2f}"
            + (f" ×페널티{gate_penalty:.2f}" if gate_penalty < 1.0 else "")
            + f" +보너스{bonus_total}pt = {final_score:.1f}pt"
            + f" (임계값:{regime_threshold}pt)"
            + (" 🚨 신호" if signal else " — 미달")
        )

    breakdown = _build_breakdown(
        d, scores, weights, raw_score, ema_mult, adx_mult,
        gate_penalty, mtf_penalty, exhaustion_mult, bonuses, final_score, gate, regime
    )
    return {
        "direction": d, "final_score": final_score, "raw_score": round(raw_score, 2),
        "weighted_score": round(penalized, 2), "ema_multiplier": ema_mult,
        "adx_multiplier": adx_mult, "passed_gate": True, "signal": signal,
        "component_scores": scores, "bonuses": bonuses, "bonus_total": bonus_total,
        "gate_info": gate, "bb_suppressed": False, "bb_suppress_reason": None,
        "regime": regime, "regime_threshold": regime_threshold, "breakdown": breakdown,
        "mtf_penalty": mtf_penalty, "exhaustion_mult": exhaustion_mult, "candle_momentum_mult": candle_momentum_mult,
    }


def _build_breakdown(d, scores, weights, raw, ema_m, adx_m, pen,
                     mtf_m, exh_m, bonuses, final, gate, regime) -> str:
    label = "🟢 롱" if d == "long" else "🔴 숏"
    lines = [f"{label} 진입 점수 분석  [{regime.get('icon','')} {regime.get('regime','')}]"]
    for key, weight in weights.items():
        s = scores[key]; contrib = s * weight
        bar = "█" * int(s / 10) + "░" * (10 - int(s / 10))
        lines.append(f"  {_score_label(key):<14} {bar} {s:>5.1f}pt × {weight:.0%} = {contrib:>4.1f}pt")
    lines.append(f"  {'─'*46}")
    lines.append(f"  가중합                           {raw:>5.1f}pt")
    if ema_m < 1.0:
        lines.append(f"  EMA 역방향 배율      × {ema_m:.2f}  {raw*ema_m:>5.1f}pt")
    if adx_m < 1.0:
        lines.append(f"  ADX 횡보 배율        × {adx_m:.2f}  {raw*ema_m*adx_m:>5.1f}pt")
    if pen < 1.0:
        lines.append(f"  복합 페널티          × {pen:.2f}")
        if gate.get("penalty_reason"):
            lines.append(f"    └ {gate['penalty_reason']}")
    if mtf_m < 1.0:
        lines.append(f"  MTF RSI 패널티       × {mtf_m:.2f}")
    if exh_m < 1.0:
        lines.append(f"  EXPLOSIVE 소진 패널티× {exh_m:.2f}")
    if bonuses:
        lines.append("  보너스:")
        for name, val in bonuses:
            lines.append(f"    + {name}: +{val}pt")
    lines.append(f"  {'─'*46}")
    lines.append(f"  최종 점수 (임계값:{regime.get('threshold',60)}pt)  {final:>5.1f}pt")
    return "\n".join(lines)


def _score_label(key: str) -> str:
    return {
        "rsi": "RSI", "bollinger": "볼린저밴드", "funding_rate": "펀딩비",
        "long_short_ratio": "롱숏비율", "taker_volume": "Taker비율",
        "oi_change": "OI변화율", "volume": "거래량",
    }.get(key, key)


def evaluate_signals(analysis: dict) -> dict:
    lr = calculate_entry_score(analysis, "long")
    sr = calculate_entry_score(analysis, "short")
    ls = lr["final_score"]
    ss = sr["final_score"]

    primary = None; suppressed = None
    if lr["signal"] and sr["signal"]:
        if abs(ls - ss) < 5.0:
            suppressed = f"양방향 차이 {abs(ls-ss):.1f}pt<5pt"
        else:
            primary = "long" if ls > ss else "short"
    elif lr["signal"]:
        primary = "long"
    elif sr["signal"]:
        primary = "short"

    ps = ls if primary == "long" else (ss if primary == "short" else 0.0)
    if primary:
        logger.info(f"[Signal] 🚨 {primary.upper()} {ps:.1f}pt")
    else:
        logger.info(f"[Signal] 없음 — 롱:{ls:.1f} 숏:{ss:.1f}")

    return {
        "long": lr, "short": sr,
        "primary": primary, "primary_score": ps, "suppressed": suppressed,
    }


# ── 상태 파일 ────────────────────────────────────────────────

def _load_state() -> dict:
    if os.path.exists(config.SIGNAL_STATE_FILE):
        try:
            with open(config.SIGNAL_STATE_FILE) as f:
                return json.load(f)
        except:
            pass
    return {}


def _save_state(state: dict) -> None:
    try:
        d = os.path.dirname(config.SIGNAL_STATE_FILE)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(config.SIGNAL_STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        logger.warning(f"[Cooldown] 저장 실패: {e}")


# ── [A2] 가격 변화율 기반 동적 쿨다운 ───────────────────────

def _get_effective_cooldown(symbol: str, direction: str, current_price: float) -> int:
    """
    마지막 신호 이후 방향성 가격 이동에 따라 실효 쿨다운 반환.
    롱: 상승 폭 클수록 쿨다운 연장 / 숏: 하락 폭 클수록 쿨다운 연장 (완전 대칭)
    역방향 2% 이상 되돌림 → 쿨다운 리셋
    """
    state      = _load_state()
    price_key  = f"{symbol}_{direction}_last_price"
    last_price = state.get(price_key)

    if not last_price or last_price == 0:
        return config.SIGNAL_COOLDOWN_MINUTES

    change_pct       = (current_price - last_price) / last_price
    directional_move = change_pct if direction == "long" else -change_pct

    if directional_move >= config.PRICE_MOVE_SUPPRESS_STRONG:
        logger.info(
            f"[A2/{direction.upper()}] {symbol} 강억제 {config.COOLDOWN_SUPPRESSED_STRONG}분 "
            f"— 방향 이동 {directional_move*100:.1f}%"
        )
        return config.COOLDOWN_SUPPRESSED_STRONG

    elif directional_move >= config.PRICE_MOVE_SUPPRESS_MILD:
        logger.info(
            f"[A2/{direction.upper()}] {symbol} 약억제 {config.COOLDOWN_SUPPRESSED_MILD}분 "
            f"— 방향 이동 {directional_move*100:.1f}%"
        )
        return config.COOLDOWN_SUPPRESSED_MILD

    elif directional_move <= config.PRICE_MOVE_RESET_THRESHOLD:
        logger.info(
            f"[A2/{direction.upper()}] {symbol} 역방향 되돌림 "
            f"{directional_move*100:.1f}% → 쿨다운 리셋"
        )
        return 0

    return config.SIGNAL_COOLDOWN_MINUTES


def is_in_cooldown(symbol: str, direction: str, current_price: float = 0.0) -> bool:
    state = _load_state()
    last  = state.get(f"{symbol}_{direction}")
    if last is None:
        return False

    effective_minutes = _get_effective_cooldown(symbol, direction, current_price)
    if effective_minutes == 0:
        return False

    elapsed  = datetime.now(timezone.utc) - datetime.fromisoformat(last)
    cooldown = timedelta(minutes=effective_minutes)
    if elapsed < cooldown:
        remain = int((cooldown - elapsed).total_seconds() / 60)
        logger.info(
            f"[Cooldown] {symbol} {direction.upper()} — "
            f"실효쿨다운:{effective_minutes}분 잔여:{remain}분"
        )
        return True
    return False


def record_signal_sent(symbol: str, direction: str, current_price: float = 0.0) -> None:
    """신호 발송 기록 + 마지막 신호 가격 저장 (A2용)"""
    state = _load_state()
    state[f"{symbol}_{direction}"] = datetime.now(timezone.utc).isoformat()
    if current_price > 0:
        state[f"{symbol}_{direction}_last_price"] = current_price
    _save_state(state)


def _load_prev_regime(symbol: str) -> str:
    return _load_state().get(f"{symbol}_prev_regime", "")


def _save_prev_regime(symbol: str, regime_name: str) -> None:
    state = _load_state()
    state[f"{symbol}_prev_regime"] = regime_name
    _save_state(state)


# ── 파이프라인 ───────────────────────────────────────────────

def run_scoring_pipeline(symbol: str, analysis: dict) -> dict:
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

    signals = evaluate_signals(analysis)
    primary = signals["primary"]
    ps      = signals["primary_score"]

    # A2: 현재 가격 추출
    current_price = analysis.get("current_price") or 0.0   # analysis_engine은 current_price 키로 저장

    cooldown = False; should_notify = False
    if primary:
        if is_in_cooldown(symbol, primary, current_price):
            cooldown = True
            logger.info(f"[Pipeline] {symbol} {primary.upper()} — 쿨다운 스킵")
        elif ps < config.SIGNAL_MIN_SCORE:
            # B안: WATCH 완전 제거 — 절대 하한선 미달 시 발화 차단
            logger.info(
                f"[Pipeline] {symbol} {primary.upper()} {ps:.1f}pt — "
                f"최소점수({config.SIGNAL_MIN_SCORE}pt) 미달 스킵"
            )
        else:
            should_notify = True
            logger.info(f"[Pipeline] ✅ {symbol} {primary.upper()} {ps:.1f}pt — 알림 예정")
    else:
        logger.info(f"[Pipeline] {symbol} — 신호 없음")

    _save_prev_regime(symbol, regime_name)

    return {
        "symbol": symbol, "should_notify": should_notify, "direction": primary,
        "score": ps, "signal_result": signals, "cooldown_skip": cooldown,
        "regime": regime, "scored_at": dt.datetime.now(timezone.utc).isoformat(),
    }
