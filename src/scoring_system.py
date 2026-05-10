"""
scoring_system.py — 점수 산출
[이번 변경]
- 티어드 보너스 캡: base 점수 구간별 보너스 상한 차등 (38→20pt / 43→28pt / +→35pt)
- pb_ok_weak/micro 분리 버그 수정: 명시적 플래그 사용
- CHoCH 역방향 패널티: soft_penalty에 통합 (×0.88)
- 신규 보너스: FVG(+10) / Hidden Divergence(+8) / BOS 확증(+8) / 피보황금포켓(+12) / 피보주요레벨(+6)
"""
import json, logging, os
from datetime import datetime, timezone, timedelta
import config

logger = logging.getLogger(__name__)


# ── 티어드 보너스 캡 ────────────────────────────────────────
def _get_tiered_bonus_cap(base_score: float) -> int:
    """
    베이스 점수가 낮을수록 보너스 상한도 낮춤.
    지표 품질 없이 보너스만으로 임계값 통과하는 구조 차단.

    base < 38pt:  상한 20pt — 중립 시장, 보너스로 구제 불가
    38~43pt:      상한 28pt — 약한 방향성
    43pt 이상:    상한 35pt — 명확한 방향성, 정상
    """
    for threshold, cap in config.BONUS_CAP_TIERS:
        if base_score < threshold:
            return cap
    return 35


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
    adx_15m = analysis.get("adx_15m",     {})
    regime  = analysis.get("regime",       {})

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

    # ── EMA 3TF 역방향: LS 중립화 ─────────────────────────────
    bb_state_str      = bb.get("state", "")
    bb_pct_b          = bb.get("pct_b", 0.5)
    bb_lower_breakout = (bb_state_str == "lower_breakout")
    bb_upper_breakout = (bb_state_str == "upper_breakout")
    bb_reversal_exempt = (
        (d == "long"  and bb_lower_breakout) or
        (d == "short" and bb_upper_breakout)
    )
    ema_all_reverse = (reverse_count == 3)

    if ema_all_reverse and not bb_reversal_exempt:
        ls_raw_before = scores["long_short_ratio"]
        scores["long_short_ratio"] = 50.0
        logger.info(f"[EMA3역방향] LS 중립화: {ls_raw_before:.0f}→50pt [{d.upper()}]")

    raw_score = sum(scores[k] * weights[k] for k in weights)

    # ── 국면별 EMA 배율 ──────────────────────────────────────
    ema_table = config.REGIME_EMA_MULTIPLIERS.get(regime_name, config.EMA_MULTIPLIER)
    ema_mult  = ema_table.get(reverse_count, 1.0)
    ema_adjusted = raw_score * ema_mult

    # ── ADX 배율 (RANGING+BB극단 보정) ───────────────────────
    adx_mult     = adx_15m.get("multiplier", 1.0)
    bb_state_adx = bb.get("state", "")
    if (regime_name == "RANGING" and adx_mult < 0.85 and (
        (d == "long"  and bb_state_adx in ("lower_breakout","near_lower")) or
        (d == "short" and bb_state_adx in ("upper_breakout","near_upper"))
    )):
        adx_mult = 0.85
        logger.info(f"[ADX보정] RANGING+BB반전 → ×0.85 [{d.upper()}]")
    adx_adjusted = ema_adjusted * adx_mult

    penalized = adx_adjusted * gate_penalty
    base_before_soft = penalized  # soft 패널티 전 베이스

    # ── C: MTF RSI 극단값 패널티 ─────────────────────────────
    rsi_1h = rsi.get("value_1h", 50.0)
    rsi_4h = rsi.get("value_4h", 50.0)
    mtf_penalty = 1.0; mtf_penalty_reason = None

    if d == "long":
        if rsi_1h >= config.MTF_RSI_OVERBOUGHT_1H_EXTREME:
            mtf_penalty=config.MTF_RSI_PENALTY_STRONG
            mtf_penalty_reason=f"MTF RSI 극단과매수(1h:{rsi_1h:.1f}) → 롱 ×{mtf_penalty}"
        elif rsi_1h>=config.MTF_RSI_OVERBOUGHT_1H and rsi_4h>=config.MTF_RSI_OVERBOUGHT_4H:
            mtf_penalty=config.MTF_RSI_PENALTY_STRONG
            mtf_penalty_reason=f"MTF RSI 강과매수(1h:{rsi_1h:.1f} 4h:{rsi_4h:.1f}) → 롱 ×{mtf_penalty}"
        elif rsi_1h>=config.MTF_RSI_OVERBOUGHT_1H_MILD:
            mtf_penalty=config.MTF_RSI_PENALTY_MILD
            mtf_penalty_reason=f"MTF RSI 약과매수(1h:{rsi_1h:.1f}) → 롱 ×{mtf_penalty}"
    elif d == "short":
        if rsi_1h<=config.MTF_RSI_OVERSOLD_1H_EXTREME:
            mtf_penalty=config.MTF_RSI_PENALTY_STRONG
            mtf_penalty_reason=f"MTF RSI 극단과매도(1h:{rsi_1h:.1f}) → 숏 ×{mtf_penalty}"
        elif rsi_1h<=config.MTF_RSI_OVERSOLD_1H and rsi_4h<=config.MTF_RSI_OVERSOLD_4H:
            mtf_penalty=config.MTF_RSI_PENALTY_STRONG
            mtf_penalty_reason=f"MTF RSI 강과매도(1h:{rsi_1h:.1f} 4h:{rsi_4h:.1f}) → 숏 ×{mtf_penalty}"
        elif rsi_1h<=config.MTF_RSI_OVERSOLD_1H_MILD:
            mtf_penalty=config.MTF_RSI_PENALTY_MILD
            mtf_penalty_reason=f"MTF RSI 약과매도(1h:{rsi_1h:.1f}) → 숏 ×{mtf_penalty}"

    if mtf_penalty < 1.0:
        penalized *= mtf_penalty
        logger.info(f"[C/MTF-RSI/{d.upper()}] {mtf_penalty_reason}")

    # ── D-alt: EXPLOSIVE 소진 패널티 ────────────────────────
    exhaustion_mult=1.0; exhaustion_reason=None
    if regime_name=="EXPLOSIVE":
        if d=="long" and rsi_1h>=config.EXPLOSIVE_EXHAUSTION_RSI_LONG:
            exhaustion_mult=config.EXPLOSIVE_EXHAUSTION_PENALTY
            exhaustion_reason=f"EXPLOSIVE 소진(1h RSI:{rsi_1h:.1f}) → 롱 ×{exhaustion_mult}"
        elif d=="short" and rsi_1h<=config.EXPLOSIVE_EXHAUSTION_RSI_SHORT:
            exhaustion_mult=config.EXPLOSIVE_EXHAUSTION_PENALTY
            exhaustion_reason=f"EXPLOSIVE 소진(1h RSI:{rsi_1h:.1f}) → 숏 ×{exhaustion_mult}"
    if exhaustion_mult < 1.0:
        penalized *= exhaustion_mult
        logger.info(f"[D-alt/{d.upper()}] {exhaustion_reason}")

    # ── OI Spike Filter ──────────────────────────────────────
    oi_change_pct = abs(oi.get("change_pct",0.0))
    oi_direction  = oi.get("direction","")
    taker_bias_raw= taker.get("bias","neutral")
    if oi_change_pct >= config.OI_SPIKE_THRESHOLD:
        oi_spike_hit=(
            (d=="long"  and oi_direction=="increasing" and taker_bias_raw=="sell_dominant") or
            (d=="short" and oi_direction=="increasing" and taker_bias_raw=="buy_dominant")
        )
        if oi_spike_hit:
            penalized=max(0.0,penalized-config.OI_SPIKE_SCORE_PENALTY)
            logger.info(f"[OISpike/{d.upper()}] OI급증+역방향 → -{config.OI_SPIKE_SCORE_PENALTY}pt")

    # ── B: BB 연속 이탈 억제 / RANGING 반전 보너스 ───────────
    BB_STREAK=3; lower_streak=bb.get("lower_streak",0); upper_streak=bb.get("upper_streak",0)
    bb_suppressed=False; bb_reason=None; bb_reversal_bonus=0

    if d=="long" and lower_streak>=BB_STREAK:
        if regime_name=="TRENDING":
            bb_suppressed=True; bb_reason=f"TRENDING BB 하단 {lower_streak}캔들 연속 이탈 — 롱 억제"
        elif regime_name=="RANGING":
            bb_reversal_bonus=config.BONUS_BB_RANGING_REVERSAL
    elif d=="short" and upper_streak>=BB_STREAK:
        if regime_name=="TRENDING":
            bb_suppressed=True; bb_reason=f"TRENDING BB 상단 {upper_streak}캔들 연속 이탈 — 숏 억제"
        elif regime_name=="RANGING":
            bb_reversal_bonus=config.BONUS_BB_RANGING_REVERSAL

    if bb_suppressed:
        logger.info(f"[Score/{d.upper()}] ⛔ {bb_reason}")
        return {
            "direction":d,"final_score":0.0,"raw_score":round(raw_score,2),"weighted_score":0.0,
            "ema_multiplier":ema_mult,"adx_multiplier":adx_mult,"passed_gate":True,"signal":False,
            "component_scores":scores,"bonuses":[],"bonus_total":0,"gate_info":gate,
            "bb_suppressed":True,"bb_suppress_reason":bb_reason,"regime":regime,"breakdown":"⛔ BB 연속 이탈 억제",
        }

    # ══ 보너스 계산 ══════════════════════════════════════════════
    bonuses = []

    # ── B: RANGING BB 반전 보너스 ────────────────────────────
    if bb_reversal_bonus > 0:
        label = "RANGING BB 하단 반전(롱)" if d=="long" else "RANGING BB 상단 반전(숏)"
        bonuses.append((label, bb_reversal_bonus))

    # ① 볼린저 극단 + RSI 다이버전스
    bb_extreme = bb.get("state","") in ("lower_breakout","near_lower","upper_breakout","near_upper")
    has_div = rsi.get("bullish_divergence") if d=="long" else rsi.get("bearish_divergence")
    if bb_extreme and has_div:
        bonuses.append(("볼린저극단+RSI다이버전스", config.BONUS_BB_RSI_ALIGN))

    # ② 펀딩비 + 롱숏비율 동일 방향
    fr_bias=funding.get("bias","neutral"); ls_bias_v=ls.get("bias","neutral")
    fr_ok=(fr_bias=="long_favorable" if d=="long" else fr_bias=="short_favorable")
    ls_ok=(ls_bias_v in ("long_favorable","long_extreme") if d=="long" else ls_bias_v in ("short_favorable","short_extreme"))
    if fr_ok and ls_ok: bonuses.append(("펀딩비+롱숏비율 동일방향", config.BONUS_FUNDING_LS_ALIGN))

    # ③ 대규모 청산
    liq_signal=liq.get("signal","none"); liq_large=liq.get("is_large",False)
    if liq_large and (
        (d=="long"  and liq_signal=="long_liq_detected") or
        (d=="short" and liq_signal=="short_liq_detected")
    ): bonuses.append(("대규모 청산 꼬리 감지", config.BONUS_LIQUIDATION))

    # ④ ADX 강한 추세
    if adx_15m.get("strength")=="strong": bonuses.append(("ADX 강한 추세", config.BONUS_ADX_STRONG))

    # ⑤ RSI 다이버전스 단독 — TRENDING 한정
    if has_div and not (bb_extreme and has_div) and regime_name=="TRENDING":
        bonuses.append(("RSI다이버전스(TRENDING)", config.BONUS_RSI_DIVERGENCE))

    # ── [신규] Hidden Divergence ─────────────────────────────
    # 추세 지속 확증 — 눌림목/반등 품질 향상
    hidden_bull = rsi.get("hidden_bull_div", False)
    hidden_bear = rsi.get("hidden_bear_div", False)
    if d=="long"  and hidden_bull:
        bonuses.append(("히든다이버전스(추세지속롱)", config.BONUS_HIDDEN_DIVERGENCE))
        logger.info(f"[Hidden Div] ★ 롱 히든 다이버전스 +{config.BONUS_HIDDEN_DIVERGENCE}pt")
    elif d=="short" and hidden_bear:
        bonuses.append(("히든다이버전스(추세지속숏)", config.BONUS_HIDDEN_DIVERGENCE))
        logger.info(f"[Hidden Div] ★ 숏 히든 다이버전스 +{config.BONUS_HIDDEN_DIVERGENCE}pt")

    # ── [신규] FVG 진입 보너스 ──────────────────────────────
    # 기관 미체결 주문 구간 → 반등/하락 기대
    fvg = analysis.get("fvg", {})
    if d=="long"  and fvg.get("in_bullish_fvg"):
        bonuses.append(("FVG 강세구간 진입", config.BONUS_FVG_ENTRY))
        logger.info(f"[FVG] ★ 롱 FVG 진입 +{config.BONUS_FVG_ENTRY}pt")
    elif d=="short" and fvg.get("in_bearish_fvg"):
        bonuses.append(("FVG 약세구간 진입", config.BONUS_FVG_ENTRY))
        logger.info(f"[FVG] ★ 숏 FVG 진입 +{config.BONUS_FVG_ENTRY}pt")

    # ── [신규] BOS 확증 보너스 ──────────────────────────────
    # 추세 방향으로 BOS 확인 = 구조적 신뢰도 상승
    bos_choch = analysis.get("bos_choch", {})
    if d=="long"  and bos_choch.get("bos_bullish"):
        bonuses.append(("BOS 상승구조 확증", config.BONUS_BOS_CONFIRM))
        logger.info(f"[BOS] ★ 상승 BOS 확증 +{config.BONUS_BOS_CONFIRM}pt")
    elif d=="short" and bos_choch.get("bos_bearish"):
        bonuses.append(("BOS 하락구조 확증", config.BONUS_BOS_CONFIRM))
        logger.info(f"[BOS] ★ 하락 BOS 확증 +{config.BONUS_BOS_CONFIRM}pt")

    # ── [신규] 피보나치 보너스 ──────────────────────────────
    fibonacci = analysis.get("fibonacci", {})
    if d=="long":
        if fibonacci.get("in_golden_pocket_long"):
            bonuses.append((f"피보황금포켓롱({fibonacci.get('long_retracement','?')}%)", config.BONUS_FIB_GOLDEN_POCKET))
            logger.info(f"[피보] ★ 롱 황금포켓 +{config.BONUS_FIB_GOLDEN_POCKET}pt")
        elif fibonacci.get("near_key_level_long"):
            bonuses.append((f"피보주요레벨롱({fibonacci.get('long_retracement','?')}%)", config.BONUS_FIB_KEY_LEVEL))
            logger.info(f"[피보] 롱 주요레벨 +{config.BONUS_FIB_KEY_LEVEL}pt")
    elif d=="short":
        if fibonacci.get("in_golden_pocket_short"):
            bonuses.append((f"피보황금포켓숏({fibonacci.get('short_retracement','?')}%)", config.BONUS_FIB_GOLDEN_POCKET))
            logger.info(f"[피보] ★ 숏 황금포켓 +{config.BONUS_FIB_GOLDEN_POCKET}pt")
        elif fibonacci.get("near_key_level_short"):
            bonuses.append((f"피보주요레벨숏({fibonacci.get('short_retracement','?')}%)", config.BONUS_FIB_KEY_LEVEL))
            logger.info(f"[피보] 숏 주요레벨 +{config.BONUS_FIB_KEY_LEVEL}pt")

    # ── 추세 지속 보너스 ──────────────────────────────────────
    ema_same=ema_info.get("same_count",0); ema_all_aligned=(ema_same==3)
    taker_str=taker.get("strength","neutral"); vol_strong=vol.get("strong",False)
    adx_strong=adx_15m.get("strength") in ("normal","strong"); bb_state=bb.get("state","")
    oi_interp=oi.get("interpretation",""); taker_bias=taker.get("bias","neutral")
    oi_confirms_long  = oi_interp in ("bullish_trend_confirm","short_covering")
    oi_confirms_short = oi_interp in ("bearish_trend_confirm","long_liquidation")

    trend_strong_ok=(
        ema_all_aligned and taker_str in ("strong","mild") and (
            (d=="long"  and oi_confirms_long  and taker_bias=="buy_dominant") or
            (d=="short" and oi_confirms_short and taker_bias=="sell_dominant")
        )
    )
    if trend_strong_ok:
        label="추세지속:EMA+OI+Taker(롱)" if d=="long" else "추세지속:EMA+OI+Taker(숏)"
        bonuses.append((label, config.BONUS_TREND_STRONG))

    if ema_all_aligned and vol_strong and adx_strong:
        bonuses.append(("추세지속:EMA+거래량+ADX", config.BONUS_TREND_VOLUME))

    band_walking_ok=(
        ema_all_aligned and (
            (d=="long"  and bb_state in ("upper_breakout","near_upper") and bb.get("upper_streak",0)>=2) or
            (d=="short" and bb_state in ("lower_breakout","near_lower") and bb.get("lower_streak",0)>=2)
        )
    )
    if band_walking_ok:
        bonuses.append(("Band Walking" + ("(상승)" if d=="long" else "(하락)"), config.BONUS_BAND_WALKING))

    # ── [수정] 눌림목 보너스 — weak/micro 명시적 플래그 사용 ──
    # 기존: pb_long(strong+weak+micro 전부)에서 weak 판단 → micro가 weak 보너스 받는 버그
    # 수정: rsi dict의 명시적 플래그 사용
    pb_long_strong  = rsi.get("pullback_long_strong",  False)
    pb_long_weak    = rsi.get("pullback_long_weak",    False)
    pb_long_micro   = rsi.get("pullback_long_micro",   False)
    pb_short_strong = rsi.get("pullback_short_strong", False)
    pb_short_weak   = rsi.get("pullback_short_weak",   False)
    pb_short_micro  = rsi.get("pullback_short_micro",  False)

    pb_ok_strong = (
        (d=="long"  and pb_long_strong  and ema_same>=2) or
        (d=="short" and pb_short_strong and ema_same>=2)
    )
    pb_ok_weak = (
        (d=="long"  and pb_long_weak   and not pb_long_strong  and ema_same>=2) or
        (d=="short" and pb_short_weak  and not pb_short_strong and ema_same>=2)
    )
    pb_ok_micro = (
        (d=="long"  and pb_long_micro  and not pb_ok_strong and not pb_ok_weak and ema_same>=1) or
        (d=="short" and pb_short_micro and not pb_ok_strong and not pb_ok_weak and ema_same>=1)
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

    # OI+Taker — 추세지속 중복 방지
    trend_strong_fired=any("추세지속:EMA+OI+Taker" in b[0] for b in bonuses)
    oi_taker_ok=(
        (d=="long"  and oi_interp=="bullish_trend_confirm" and taker_bias=="buy_dominant") or
        (d=="short" and oi_interp=="bearish_trend_confirm" and taker_bias=="sell_dominant")
    )
    if oi_taker_ok and not trend_strong_fired:
        bonuses.append(("OI증가+Taker방향일치", config.BONUS_OI_TAKER_CONFIRM))

    # Volume Explosion
    vol_avg_ratio=vol.get("ratio",1.0); adx_val_raw=adx_15m.get("adx",0.0)
    if vol_avg_ratio>=2.5 and adx_val_raw>=22.0 and not ema_all_aligned:
        bonuses.append(("거래량폭발+ADX확인", config.BONUS_VOLUME_EXPLOSION))

    # Post-Squeeze Momentum
    prev_regime=analysis.get("prev_regime","")
    bb_just_broke=(
        (d=="long"  and bb_state in ("upper_breakout","near_upper") and bb.get("upper_streak",0)==1) or
        (d=="short" and bb_state in ("lower_breakout","near_lower") and bb.get("lower_streak",0)==1)
    )
    if (prev_regime=="SQUEEZE" or regime_name=="EXPLOSIVE") and bb_just_broke:
        bonuses.append(("Post-Squeeze 롱 돌파" if d=="long" else "Post-Squeeze 숏 돌파", config.BONUS_POST_SQUEEZE))

    # 추세 전환 경고 보너스
    taker_buy_r=taker.get("buy_ratio",0.5); taker_sell_r=taker.get("sell_ratio",0.5)
    funding_rate=funding.get("rate",0.0)
    reversal_warning=False
    if d=="short":
        reversal_warning=(rsi_1h>=config.REVERSAL_RSI_1H_OB and taker_sell_r>=config.REVERSAL_TAKER_SELL and funding_rate>=config.REVERSAL_FUNDING_POS)
    elif d=="long":
        reversal_warning=(rsi_1h<=config.REVERSAL_RSI_1H_OS and taker_buy_r>=config.REVERSAL_TAKER_BUY and funding_rate<=config.REVERSAL_FUNDING_NEG)
    if reversal_warning:
        bonuses.append(("추세전환경고(롱→숏)" if d=="short" else "추세전환경고(숏→롱)", config.BONUS_TREND_REVERSAL_WARNING))

    # LS 방향 확인
    ls_bias_v2=ls.get("bias","neutral")
    ls_confirms_long  = ls_bias_v2 in ("long_momentum","long_lean","long_favorable","long_extreme")
    ls_confirms_short = ls_bias_v2 in ("short_momentum","short_lean","short_favorable","short_extreme")
    if (d=="long" and ls_confirms_long) or (d=="short" and ls_confirms_short):
        bonuses.append((f"LS방향확인({ls_bias_v2})", config.BONUS_LS_DIRECTION_CONFIRM))

    # ── 캔들 패턴 ────────────────────────────────────────────
    candle=analysis.get("candle_pattern",{})
    if d=="short":
        if candle.get("bearish_pin"):  bonuses.append(("베어리시핀바", config.BONUS_CANDLE_PIN_BAR))
        elif candle.get("bearish_engulf"): bonuses.append(("베어리시인걸핑", config.BONUS_CANDLE_ENGULFING))
        if candle.get("consecutive_bear") and not candle.get("bearish_pin"):
            bonuses.append(("연속음봉3", config.BONUS_CANDLE_CONSECUTIVE))
    elif d=="long":
        if candle.get("bullish_pin"):  bonuses.append(("불리시핀바", config.BONUS_CANDLE_PIN_BAR))
        elif candle.get("bullish_engulf"): bonuses.append(("불리시인걸핑", config.BONUS_CANDLE_ENGULFING))
        if candle.get("consecutive_bull") and not candle.get("bullish_pin"):
            bonuses.append(("연속양봉3", config.BONUS_CANDLE_CONSECUTIVE))

    # ── 시장 구조 ────────────────────────────────────────────
    ms=analysis.get("market_structure",{})
    if d=="short":
        if ms.get("lower_high"):      bonuses.append(("LowerHigh구조", config.BONUS_MARKET_STRUCT_TREND))
        if ms.get("failed_breakout"): bonuses.append(("돌파실패", config.BONUS_FAILED_BREAKOUT))
    elif d=="long":
        if ms.get("higher_low"):      bonuses.append(("HigherLow구조", config.BONUS_MARKET_STRUCT_TREND))
        if ms.get("failed_breakdown"):bonuses.append(("붕괴실패", config.BONUS_FAILED_BREAKOUT))

    # ── 거래량-가격 다이버전스 ──────────────────────────────
    vpd=analysis.get("vol_price_div",{})
    if d=="short" and vpd.get("bearish_vol_div"):
        bonuses.append(("거래량약세다이버전스", config.BONUS_VOL_PRICE_DIV))
    elif d=="long" and vpd.get("bullish_vol_div"):
        bonuses.append(("거래량강세다이버전스", config.BONUS_VOL_PRICE_DIV))

    # ── 펀딩비 극단 ─────────────────────────────────────────
    fund_rate=funding.get("rate",0.0)
    if d=="short" and fund_rate>=config.FUNDING_EXTREME_SHORT:
        bonuses.append((f"펀딩극단숏({fund_rate*100:.2f}%)", config.BONUS_FUNDING_EXTREME))
    elif d=="long" and fund_rate<=config.FUNDING_EXTREME_LONG:
        bonuses.append((f"펀딩극단롱({fund_rate*100:.2f}%)", config.BONUS_FUNDING_EXTREME))

    # ── ATR 모멘텀 ──────────────────────────────────────────
    atr=analysis.get("atr",{})
    if atr.get("ratio",1.0)>=config.ATR_MOMENTUM_RATIO:
        bonuses.append((f"ATR모멘텀({atr.get('ratio',1):.1f}x)", config.BONUS_ATR_MOMENTUM))

    # ── 소진 상태에서 추세확인형 보너스 제거 ─────────────────
    if exhaustion_mult < 1.0:
        tc_names={"LowerHigh구조","HigherLow구조","연속음봉3","연속양봉3","OI증가+Taker방향일치","볼린저극단+RSI다이버전스"}
        removed=[(n,v) for n,v in bonuses if n in tc_names]
        bonuses=[(n,v) for n,v in bonuses if n not in tc_names]
        if removed: logger.info(f"[소진보너스제거] {[n for n,_ in removed]}")

    # ── EMA 3TF 역방향 시 반전 보너스 75% 감산 ──────────────
    _REV_BONUS={"거래량강세다이버전스","거래량약세다이버전스","RANGING BB 하단 반전(롱)","RANGING BB 상단 반전(숏)","볼린저극단+RSI다이버전스"}
    if ema_all_reverse and not bb_reversal_exempt:
        bonuses=[(n, round(v*0.25) if n in _REV_BONUS else v) for n,v in bonuses]
        bonuses=[(n,v) for n,v in bonuses if "LS방향확인" not in n]

    # ── Taker 역방향 시 캔들 보너스 60% 감산 ─────────────────
    _CANDLE_B={"불리시핀바","베어리시핀바","불리시인걸핑","베어리시인걸핑","연속양봉3","연속음봉3"}
    _taker_against=((d=="long" and taker_bias=="sell_dominant") or (d=="short" and taker_bias=="buy_dominant"))
    if _taker_against:
        bonuses=[(n, round(v*0.40) if n in _CANDLE_B else v) for n,v in bonuses]

    # ── [신규] 티어드 보너스 캡 적용 ────────────────────────
    bonus_raw   = sum(v for _,v in bonuses)
    bonus_cap   = _get_tiered_bonus_cap(base_before_soft)
    bonus_total = min(bonus_cap, bonus_raw)
    if bonus_raw > bonus_cap:
        logger.info(f"[보너스캡] base:{base_before_soft:.0f}pt → 캡:{bonus_cap}pt ({bonus_raw}→{bonus_total}pt)")

    # ── 연속캔들 모멘텀 역방향 패널티 ───────────────────────
    candle_momentum_mult=1.0
    candle_pattern=analysis.get("candle_pattern",{})
    bb_state_c=bb.get("state","")
    if d=="short" and candle_pattern.get("consecutive_bull"):
        if regime_name=="TRENDING":    candle_momentum_mult=config.CANDLE_MOMENTUM_PENALTY_TRENDING
        elif regime_name=="EXPLOSIVE": candle_momentum_mult=config.CANDLE_MOMENTUM_PENALTY_EXPLOSIVE
        else:                          candle_momentum_mult=config.CANDLE_MOMENTUM_PENALTY_RANGING
        logger.info(f"[캔들모멘텀] 연속양봉중 숏 타이밍 불량 ×{candle_momentum_mult:.2f}")
    elif d=="long" and candle_pattern.get("consecutive_bear"):
        bb_lower_exempt=(bb_state_c in ("lower_breakout","near_lower") or bb.get("pct_b",0.5)<=0.15)
        if not bb_lower_exempt:
            if regime_name=="TRENDING":    candle_momentum_mult=config.CANDLE_MOMENTUM_PENALTY_TRENDING
            elif regime_name=="EXPLOSIVE": candle_momentum_mult=config.CANDLE_MOMENTUM_PENALTY_EXPLOSIVE
            else:                          candle_momentum_mult=config.CANDLE_MOMENTUM_PENALTY_RANGING
            logger.info(f"[캔들모멘텀] 연속음봉중 롱 타이밍 불량 ×{candle_momentum_mult:.2f}")

    # ── [신규] CHoCH 역방향 패널티 ──────────────────────────
    # 추세 전환 경고 중 역방향 진입 = 구조적 위험
    choch_penalty=1.0; choch_reason=None
    bos_choch_data=analysis.get("bos_choch",{})
    if d=="long"  and bos_choch_data.get("choch_bearish"):
        choch_penalty=config.CHOCH_AGAINST_PENALTY
        choch_reason="CHoCH 하락전환 경고 중 롱 진입"
    elif d=="short" and bos_choch_data.get("choch_bullish"):
        choch_penalty=config.CHOCH_AGAINST_PENALTY
        choch_reason="CHoCH 상승전환 경고 중 숏 진입"
    if choch_penalty < 1.0:
        logger.info(f"[CHoCH/{d.upper()}] ⚠️ {choch_reason} → ×{choch_penalty}")

    # ── 최종 점수 (개선안5 공식: soft 패널티는 base+bonus 전체에) ──
    soft_penalty = mtf_penalty * exhaustion_mult * candle_momentum_mult * choch_penalty
    final_score  = round(min(100.0, max(0.0, (base_before_soft + bonus_total) * soft_penalty)), 2)

    regime_threshold=regime.get("threshold", config.REGIME_THRESHOLDS.get("TRENDING",60))
    signal=(final_score>=regime_threshold and final_score>=config.SIGNAL_MIN_SCORE)

    soft_applied = soft_penalty < 1.0
    if soft_applied:
        logger.info(
            f"[Score/{d.upper()}] [{regime_name}] raw:{raw_score:.1f}"
            f" ×EMA{ema_mult:.2f} ×ADX{adx_mult:.2f}"
            + (f" ×페널티{gate_penalty:.2f}" if gate_penalty<1.0 else "")
            + f" → base:{base_before_soft:.1f}pt"
            + f" → (base:{base_before_soft:.1f}+보너스{bonus_total}[cap:{bonus_cap}])"
            + (f" ×MTF{mtf_penalty:.2f}" if mtf_penalty<1.0 else "")
            + (f" ×소진{exhaustion_mult:.2f}" if exhaustion_mult<1.0 else "")
            + (f" ×캔들{candle_momentum_mult:.2f}" if candle_momentum_mult<1.0 else "")
            + (f" ×CHoCH{choch_penalty:.2f}" if choch_penalty<1.0 else "")
            + f" = {final_score:.1f}pt (임계:{regime_threshold}pt)"
            + (" 🚨 신호" if signal else "")
        )
    else:
        logger.info(
            f"[Score/{d.upper()}] [{regime_name}] raw:{raw_score:.1f}"
            f" ×EMA{ema_mult:.2f} ×ADX{adx_mult:.2f}"
            + (f" ×페널티{gate_penalty:.2f}" if gate_penalty<1.0 else "")
            + f" +보너스{bonus_total}[cap:{bonus_cap}] = {final_score:.1f}pt (임계:{regime_threshold}pt)"
            + (" 🚨 신호" if signal else "")
        )

    breakdown=_build_breakdown(d,scores,weights,raw_score,ema_mult,adx_mult,gate_penalty,
                                mtf_penalty,exhaustion_mult,choch_penalty,bonuses,bonus_cap,final_score,gate,regime)
    return {
        "direction":d,"final_score":final_score,"raw_score":round(raw_score,2),
        "weighted_score":round(penalized,2),"ema_multiplier":ema_mult,"adx_multiplier":adx_mult,
        "passed_gate":True,"signal":signal,"component_scores":scores,
        "bonuses":bonuses,"bonus_total":bonus_total,"bonus_cap":bonus_cap,"gate_info":gate,
        "bb_suppressed":False,"bb_suppress_reason":None,"regime":regime,
        "regime_threshold":regime_threshold,"breakdown":breakdown,
        "mtf_penalty":mtf_penalty,"exhaustion_mult":exhaustion_mult,
        "candle_momentum_mult":candle_momentum_mult,"choch_penalty":choch_penalty,
    }


def _build_breakdown(d, scores, weights, raw, ema_m, adx_m, pen,
                     mtf_m, exh_m, choch_m, bonuses, bonus_cap, final, gate, regime) -> str:
    label="🟢 롱" if d=="long" else "🔴 숏"
    lines=[f"{label} 진입 점수 분석  [{regime.get('icon','')} {regime.get('regime','')}]"]
    for key, weight in weights.items():
        s=scores[key]; contrib=s*weight
        bar="█"*int(s/10)+"░"*(10-int(s/10))
        lines.append(f"  {_score_label(key):<14} {bar} {s:>5.1f}pt × {weight:.0%} = {contrib:>4.1f}pt")
    lines.append(f"  {'─'*46}")
    lines.append(f"  가중합                           {raw:>5.1f}pt")
    if ema_m<1.0: lines.append(f"  EMA 역방향 배율      × {ema_m:.2f}")
    if adx_m<1.0: lines.append(f"  ADX 횡보 배율        × {adx_m:.2f}")
    if pen<1.0:   lines.append(f"  복합 페널티          × {pen:.2f}")
    if mtf_m<1.0: lines.append(f"  MTF RSI 패널티       × {mtf_m:.2f}")
    if exh_m<1.0: lines.append(f"  EXPLOSIVE 소진 패널티× {exh_m:.2f}")
    if choch_m<1.0: lines.append(f"  CHoCH 역방향 패널티 × {choch_m:.2f}")
    if bonuses:
        lines.append(f"  보너스 (상한:{bonus_cap}pt):")
        for name,val in bonuses:
            lines.append(f"    + {name}: +{val}pt")
    lines.append(f"  {'─'*46}")
    lines.append(f"  최종 점수 (임계값:{regime.get('threshold',60)}pt)  {final:>5.1f}pt")
    return "\n".join(lines)


def _score_label(key: str) -> str:
    return {"rsi":"RSI","bollinger":"볼린저밴드","funding_rate":"펀딩비",
            "long_short_ratio":"롱숏비율","taker_volume":"Taker비율",
            "oi_change":"OI변화율","volume":"거래량"}.get(key,key)


def evaluate_signals(analysis: dict) -> dict:
    lr=calculate_entry_score(analysis,"long")
    sr=calculate_entry_score(analysis,"short")
    ls=lr["final_score"]; ss=sr["final_score"]
    primary=None; suppressed=None
    if lr["signal"] and sr["signal"]:
        if abs(ls-ss)<5.0: suppressed=f"양방향 차이 {abs(ls-ss):.1f}pt<5pt"
        else: primary="long" if ls>ss else "short"
    elif lr["signal"]: primary="long"
    elif sr["signal"]: primary="short"
    ps=ls if primary=="long" else (ss if primary=="short" else 0.0)
    if primary: logger.info(f"[Signal] 🚨 {primary.upper()} {ps:.1f}pt")
    else:        logger.info(f"[Signal] 없음 — 롱:{ls:.1f} 숏:{ss:.1f}")
    return {"long":lr,"short":sr,"primary":primary,"primary_score":ps,"suppressed":suppressed}


# ── 상태 파일 ─────────────────────────────────────────────────

def _load_state() -> dict:
    if os.path.exists(config.SIGNAL_STATE_FILE):
        try:
            with open(config.SIGNAL_STATE_FILE) as f: return json.load(f)
        except: pass
    return {}

def _save_state(state: dict) -> None:
    try:
        d=os.path.dirname(config.SIGNAL_STATE_FILE)
        if d: os.makedirs(d,exist_ok=True)
        with open(config.SIGNAL_STATE_FILE,"w") as f: json.dump(state,f)
    except Exception as e: logger.warning(f"[Cooldown] 저장 실패: {e}")

def _get_effective_cooldown(symbol: str, direction: str, current_price: float) -> int:
    state=_load_state(); price_key=f"{symbol}_{direction}_last_price"
    last_price=state.get(price_key)
    if not last_price or last_price==0: return config.SIGNAL_COOLDOWN_MINUTES
    change_pct=(current_price-last_price)/last_price
    directional_move=change_pct if direction=="long" else -change_pct
    if directional_move>=config.PRICE_MOVE_SUPPRESS_STRONG:   return config.COOLDOWN_SUPPRESSED_STRONG
    elif directional_move>=config.PRICE_MOVE_SUPPRESS_MILD:   return config.COOLDOWN_SUPPRESSED_MILD
    elif directional_move<=config.PRICE_MOVE_RESET_THRESHOLD: return 0
    return config.SIGNAL_COOLDOWN_MINUTES

def is_in_cooldown(symbol: str, direction: str, current_price: float=0.0) -> bool:
    state=_load_state(); last=state.get(f"{symbol}_{direction}")
    if last is None: return False
    effective_minutes=_get_effective_cooldown(symbol,direction,current_price)
    if effective_minutes==0: return False
    elapsed=datetime.now(timezone.utc)-datetime.fromisoformat(last)
    cooldown=timedelta(minutes=effective_minutes)
    if elapsed<cooldown:
        remain=int((cooldown-elapsed).total_seconds()/60)
        logger.info(f"[Cooldown] {symbol} {direction.upper()} — 실효:{effective_minutes}분 잔여:{remain}분")
        return True
    return False

def record_signal_sent(symbol: str, direction: str, current_price: float=0.0) -> None:
    state=_load_state()
    state[f"{symbol}_{direction}"]=datetime.now(timezone.utc).isoformat()
    if current_price>0: state[f"{symbol}_{direction}_last_price"]=current_price
    _save_state(state)

def _load_prev_regime(symbol: str) -> str:
    return _load_state().get(f"{symbol}_prev_regime","")

def _save_prev_regime(symbol: str, regime_name: str) -> None:
    state=_load_state(); state[f"{symbol}_prev_regime"]=regime_name; _save_state(state)


# ── 파이프라인 ────────────────────────────────────────────────

def run_scoring_pipeline(symbol: str, analysis: dict) -> dict:
    import datetime as dt
    logger.info(f"{'─'*50}")
    logger.info(f"🎯 점수 산출: {symbol}")
    regime=analysis.get("regime",{}); regime_name=regime.get("regime","UNKNOWN")
    logger.info(f"  {regime.get('icon','')} 국면: {regime_name} — {regime.get('description','')}")
    prev_regime=_load_prev_regime(symbol)
    if prev_regime:
        analysis["prev_regime"]=prev_regime
        logger.info(f"  이전 국면: {prev_regime}")
    signals=evaluate_signals(analysis)
    primary=signals["primary"]; ps=signals["primary_score"]
    current_price=analysis.get("current_price") or 0.0
    cooldown=False; should_notify=False
    if primary:
        if is_in_cooldown(symbol,primary,current_price):
            cooldown=True
            logger.info(f"[Pipeline] {symbol} {primary.upper()} — 쿨다운 스킵")
        elif ps<config.SIGNAL_MIN_SCORE:
            logger.info(f"[Pipeline] {symbol} {primary.upper()} {ps:.1f}pt — 최소점수({config.SIGNAL_MIN_SCORE}pt) 미달")
        else:
            should_notify=True
            logger.info(f"[Pipeline] ✅ {symbol} {primary.upper()} {ps:.1f}pt — 알림 예정")
    else:
        logger.info(f"[Pipeline] {symbol} — 신호 없음")
    _save_prev_regime(symbol,regime_name)
    return {
        "symbol":symbol,"should_notify":should_notify,"direction":primary,
        "score":ps,"signal_result":signals,"cooldown_skip":cooldown,
        "regime":regime,"scored_at":dt.datetime.now(timezone.utc).isoformat(),
    }
