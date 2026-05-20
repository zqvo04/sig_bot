"""
notification.py — 텔레그램 알림 (v3.5)
──────────────────────────────────────────────────────────────────────────────
[v3.5 변경]

★ 진입 가이드(SL/TP/ATR) 섹션 완전 제거
  이유: 가독성 저하, ATR 기준 SL/TP는 참고 목적에 그침

★ 전체 메시지 구조 재편 — 가독성 대폭 향상
  - 헤더에 시간 통합 (별도 줄 제거)
  - 기술 지표 컴팩트화 (RSI/BB/EMA/ADX 각 1줄)
  - 시장 심리 1줄 요약 형태로 압축
  - SMC 섹션 간결화
  - 판단 근거 핵심만 표시

★ A/B/C 개선 패널티 표시 추가
  - 거래량다이버전스 RANGING 감액 표시
  - 저ADX+BOS역방향 추가 패널티 표시
──────────────────────────────────────────────────────────────────────────────
"""
import logging, time
from datetime import datetime, timezone
import requests
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import config

logger = logging.getLogger(__name__)
_TG_BASE = "https://api.telegram.org/bot{token}/{method}"


# ══════════════════════════════════════════════════════════════════════════════
# 텔레그램 공통
# ══════════════════════════════════════════════════════════════════════════════

def _send_telegram(method, payload, retries=3):
    if not config.TELEGRAM_BOT_TOKEN:
        return None
    url = _TG_BASE.format(token=config.TELEGRAM_BOT_TOKEN, method=method)
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(url, json=payload, timeout=10)
            if r.status_code == 200:
                d = r.json()
                if d.get("ok"):
                    return d
                logger.error(f"[TG] API 오류: {d.get('description')}")
                return None
            elif r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", 5)))
            else:
                time.sleep(3 * attempt)
        except Exception as e:
            logger.error(f"[TG] 오류: {e}")
            time.sleep(3 * attempt)
    return None


def send_message(text, parse_mode="HTML"):
    if not config.TELEGRAM_CHAT_ID:
        return None
    return _send_telegram("sendMessage", {
        "chat_id":                  config.TELEGRAM_CHAT_ID,
        "text":                     text,
        "parse_mode":               parse_mode,
        "disable_web_page_preview": True,
    })


# ══════════════════════════════════════════════════════════════════════════════
# 유틸리티
# ══════════════════════════════════════════════════════════════════════════════

def _bar(score, width=8):
    f = max(0, min(width, int(round(score / 100 * width))))
    return f"{'█'*f}{'░'*(width-f)}"


def _fmt_price(price, symbol):
    if price is None:
        return "N/A"
    return (f"${price:,.2f}" if any(s in symbol for s in ["BTC", "ETH"])
            else f"${price:,.4f}")


def _micro_label(name: str) -> str:
    return {
        "LiqCascade":   "청산캐스케이드",
        "OrderBook":    "오더북벽",
        "OBImbalance":  "호가잔량불균형",
        "CandleMom":    "캔들모멘텀",
        "MarkFunding":  "마크/펀딩",
        "LSDivergence": "LS괴리(고래)",
    }.get(name, name)


def _micro_severity(penalty: int) -> str:
    if penalty <= -12: return "🔴"
    if penalty < 0:    return "🟡"
    if penalty > 0:    return "🟢"
    return "⚪"


# ══════════════════════════════════════════════════════════════════════════════
# 신호 메시지 빌더
# ══════════════════════════════════════════════════════════════════════════════

def build_signal_message(pipeline_result: dict, analysis: dict) -> str:
    direction   = pipeline_result["direction"]
    score       = pipeline_result["score"]
    symbol      = pipeline_result["symbol"]
    signals     = pipeline_result["signal_result"]
    side_result = signals[direction]
    regime_info = pipeline_result.get("regime", {})

    micro: dict    = pipeline_result.get("micro_result") or {}
    micro_total    = micro.get("total_penalty", 0)
    micro_raw      = micro.get("raw_total", micro_total)
    micro_details  = micro.get("details", [])
    micro_critical = [d for d in micro_details if d[1] <= -10]
    micro_bonus    = [d for d in micro_details if d[1] > 0]

    rsi       = analysis.get("rsi",          {})
    bb        = analysis.get("bollinger",     {})
    ema       = analysis.get(f"ema_{direction}", {})
    adx       = analysis.get("adx_15m",      {})
    funding   = analysis.get("funding_rate",  {})
    ls        = analysis.get("ls_ratio",      {})
    taker     = analysis.get("taker_volume",  {})
    liq       = analysis.get("liquidations",  {})
    fvg       = analysis.get("fvg",           {})
    bos_choch = analysis.get("bos_choch",     {})
    fibonacci = analysis.get("fibonacci",     {})
    price     = analysis.get("current_price")

    cs         = side_result.get("component_scores", {})
    bonuses    = side_result.get("bonuses",           [])
    gate       = side_result.get("gate_info",         {})
    regime_thr = side_result.get("regime_threshold",  63)
    bonus_total= side_result.get("bonus_total",       0)
    bonus_cap  = side_result.get("bonus_cap",         36)

    mtf_penalty            = side_result.get("mtf_penalty",              1.0)
    exhaustion_mult        = side_result.get("exhaustion_mult",           1.0)
    candle_momentum_m      = side_result.get("candle_momentum_mult",      1.0)
    choch_penalty          = side_result.get("choch_penalty",             1.0)
    bos_conflict_penalty   = side_result.get("bos_conflict_penalty",      1.0)
    explosive_bos_penalty  = side_result.get("explosive_bos_penalty",     1.0)
    ranging_bos_weak_p     = side_result.get("ranging_bos_weak_penalty",  1.0)
    volume_penalty         = side_result.get("volume_penalty",            0)

    now     = datetime.now(timezone.utc).strftime("%m/%d %H:%M UTC")
    lines   = []
    d       = direction
    same_c  = ema.get("same_count",    0)
    rev_c   = ema.get("reverse_count", 0)

    # ── 등급 ──────────────────────────────────────────────────────
    if score >= 85:
        grade_icon, grade_label = "🔥🔥", "STRONG"
    elif score >= 72:
        grade_icon, grade_label = "🔥",   "GOOD"
    else:
        grade_icon, grade_label = "📊",   "WATCH"

    if micro_critical and grade_label != "WATCH":
        grade_icon  = "⚠️"
        grade_label = grade_label + "⚠"

    # ── 핵심 태그 줄 ──────────────────────────────────────────────
    dir_icon = "🟢" if d == "long" else "🔴"
    dir_text = "롱(LONG)" if d == "long" else "숏(SHORT)"

    # EMA 정합
    ema_tag = (
        "EMA ✅3/3" if same_c == 3 else
        "EMA ✅2/3" if same_c == 2 else
        "EMA ⚠️1/3" if same_c == 1 else
        "EMA ⚠️역방향"
    )

    # 눌림목
    pb_strong = rsi.get("pullback_long_strong"  if d=="long" else "pullback_short_strong", False)
    pb_weak   = rsi.get("pullback_long_weak"    if d=="long" else "pullback_short_weak",   False)
    pb_micro  = rsi.get("pullback_long_micro"   if d=="long" else "pullback_short_micro",  False)
    pb_tag = ""
    if pb_strong: pb_tag = "  ★ 눌림목(강)"
    elif pb_weak: pb_tag = "  ★ 눌림목(약)"
    elif pb_micro:pb_tag = "  ★ 눌림목(미)"

    # SMC 요약 태그
    smc_parts = []
    if fvg.get("in_bullish_fvg" if d=="long" else "in_bearish_fvg"):
        smc_parts.append("FVG")
    if fvg.get("in_bullish_fvg") and fvg.get("in_bearish_fvg"):
        smc_parts = ["FVG(모호)"]
    if bos_choch.get("bos_bullish" if d=="long" else "bos_bearish"):
        smc_parts.append("BOS↑" if d=="long" else "BOS↓")
    if fibonacci.get("in_golden_pocket_long" if d=="long" else "in_golden_pocket_short"):
        retr = fibonacci.get("long_retracement" if d=="long" else "short_retracement")
        smc_parts.append(f"황금포켓({retr}%)" if retr else "황금포켓")

    # 국면
    r_icon = regime_info.get("icon","")
    r_name = regime_info.get("regime","?")
    adx_v  = adx.get("adx", 0.0)

    # ── 헤더 블록 ─────────────────────────────────────────────────
    lines.append(f"{dir_icon} <b>{dir_text} 진입 신호</b>  {grade_icon} <b>{grade_label}</b>")
    lines.append(f"<code>{'─'*32}</code>")
    lines.append(f"🪙 <b>{symbol}</b>  💰 <b>{_fmt_price(price, symbol)}</b>  |  🕐 {now}")

    micro_note = f"  <i>(micro:{micro_total:+d}pt)</i>" if micro_total != 0 else ""
    score_bar  = "█" * int(score/10) + "░" * (10 - int(score/10))
    lines.append(f"🎯 <b>{score:.1f}pt</b>  {score_bar}  임계:{regime_thr}pt{micro_note}")
    lines.append(f"📐 {ema_tag}  |  {r_icon} {r_name}  ADX:{adx_v:.0f}")
    if pb_tag:
        lines.append(f"<b>{pb_tag}</b>")
    if smc_parts:
        lines.append(f"🏛 SMC: <b>{' · '.join(smc_parts)}</b>")
    lines.append("")

    # ── 마이크로구조 (있을 때만) ──────────────────────────────────
    if micro_details:
        cap_note = f" → cap {micro_total:+d}pt" if micro_raw != micro_total else ""
        lines.append(f"🔬 <b>마이크로구조</b>  합계:<b>{micro_raw:+d}pt</b>{cap_note}")
        for name, p, reason in micro_details:
            icon    = _micro_severity(p)
            label   = _micro_label(name)
            r_short = reason.replace("⚠️","").replace("✅","").strip()[:50]
            lines.append(f"  {icon} {label}: <b>{p:+d}pt</b>  <i>{r_short}</i>")
        lines.append("")

    # ── 기술 지표 (컴팩트) ────────────────────────────────────────
    lines.append("📈 <b>지표</b>")

    # RSI
    rsi_val = rsi.get("value", 50.0)
    rsi_1h  = rsi.get("value_1h")
    rsi_4h  = rsi.get("value_4h")
    rsi_state = rsi.get("state","neutral")
    rsi_tag = "⚡과매도" if rsi_state=="oversold" else ("⚡과매수" if rsi_state=="overbought" else "중립")
    rsi_tf  = f"15m:{rsi_val:.0f}"
    if rsi_1h: rsi_tf += f" / 1h:{rsi_1h:.0f}"
    if rsi_4h: rsi_tf += f" / 4h:{rsi_4h:.0f}"
    div_tag = ""
    if d=="long":
        if rsi.get("hidden_bull_div"):      div_tag = " 📊히든강세"
        elif rsi.get("bullish_divergence"): div_tag = " ✅강세다이버"
    else:
        if rsi.get("hidden_bear_div"):      div_tag = " 📊히든약세"
        elif rsi.get("bearish_divergence"): div_tag = " ✅약세다이버"
    lines.append(f"  RSI  {rsi_tf}  {rsi_tag}{div_tag}")

    # BB
    bb_map = {
        "lower_breakout":"🔵하단이탈","near_lower":"↘하단영역","lower_zone":"↘하단영역",
        "middle":"—중앙","upper_zone":"↗상단영역","near_upper":"🔴상단근접","upper_breakout":"🔴상단이탈",
    }
    bb_tag2  = bb_map.get(bb.get("state",""),"—")
    sq_s     = " 🔊스퀴즈" if bb.get("squeeze") else ""
    ls_str   = bb.get("lower_streak",0); us_str = bb.get("upper_streak",0)
    sk_s     = f" ⚠️하단{ls_str}캔들" if ls_str>=2 else (f" ⚠️상단{us_str}캔들" if us_str>=2 else "")
    lines.append(f"  BB   {bb_tag2}  %B:{bb.get('pct_b',0.5):.2f}{sq_s}{sk_s}")

    # EMA
    ema_tf   = ema.get("tf_signals",{})
    ema_str  = " | ".join(
        f"{tf}:{'↑' if s=='bullish' else('↓' if s=='bearish' else '—')}"
        for tf, s in ema_tf.items()
    )
    ema_mult = ema.get("multiplier",1.0)
    ema_warn = "" if ema_mult==1.0 else f"  ⚠️×{ema_mult:.2f}"
    lines.append(f"  EMA  [{ema_str}]{ema_warn}")

    # ADX
    adx_str = {"strong":"🔥강추세","normal":"📈추세중","weak":"〰약추세","none":"💤횡보"}
    lines.append(f"  ADX  {adx_v:.1f}  {adx_str.get(adx.get('strength','none'),'—')}")
    lines.append("")

    # ── SMC / 구조 (간결) ─────────────────────────────────────────
    has_smc = (fvg.get("in_bullish_fvg") or fvg.get("in_bearish_fvg") or
               bos_choch.get("bos_bullish") or bos_choch.get("bos_bearish") or
               bos_choch.get("choch_bullish") or bos_choch.get("choch_bearish") or
               fibonacci.get("in_golden_pocket_long") or fibonacci.get("in_golden_pocket_short") or
               fibonacci.get("near_key_level_long") or fibonacci.get("near_key_level_short"))

    if has_smc:
        lines.append("🏛 <b>SMC</b>")
        bull_fvg = fvg.get("in_bullish_fvg",False); bear_fvg = fvg.get("in_bearish_fvg",False)
        if bull_fvg and bear_fvg:
            lines.append(f"  FVG: ⚠️ 양방향 모호  (강세{fvg.get('bullish_fvg_count',0)}·약세{fvg.get('bearish_fvg_count',0)}개)")
        elif bull_fvg:
            lines.append(f"  FVG: ✅ 강세 {fvg.get('bullish_fvg_count',0)}개 — 매수 주문 구간")
        elif bear_fvg:
            lines.append(f"  FVG: ✅ 약세 {fvg.get('bearish_fvg_count',0)}개 — 매도 주문 구간")

        # BOS/CHoCH
        if bos_choch.get("bos_bullish"):
            if d == "short":
                lines.append(f"  BOS: 상승확증  ⛔ 숏역방향 ×{bos_conflict_penalty:.2f}")
                if ranging_bos_weak_p < 1.0:
                    lines.append(f"    └ 저ADX+BOS 추가패널티 ×{ranging_bos_weak_p:.2f}")
            else:
                lines.append("  BOS: ✅ 상승확증")
        elif bos_choch.get("bos_bearish"):
            if d == "long":
                lines.append(f"  BOS: 하락확증  ⛔ 롱역방향 ×{bos_conflict_penalty:.2f}")
                if ranging_bos_weak_p < 1.0:
                    lines.append(f"    └ 저ADX+BOS 추가패널티 ×{ranging_bos_weak_p:.2f}")
            else:
                lines.append("  BOS: ✅ 하락확증")
        elif bos_choch.get("choch_bullish"):
            lines.append(f"  CHoCH: ⚠️ 상승전환경고" + (f"  숏역방향 ×{choch_penalty:.2f}" if d=="short" else ""))
        elif bos_choch.get("choch_bearish"):
            lines.append(f"  CHoCH: ⚠️ 하락전환경고" + (f"  롱역방향 ×{choch_penalty:.2f}" if d=="long" else ""))
        else:
            sh = bos_choch.get("last_swing_high"); sl2 = bos_choch.get("last_swing_low")
            if sh or sl2:
                parts = []
                if sh:  parts.append(f"H:{_fmt_price(sh,symbol)}")
                if sl2: parts.append(f"L:{_fmt_price(sl2,symbol)}")
                lines.append(f"  BOS: — 구조유지  ({' '.join(parts)})")

        # 피보나치
        if d == "long":
            if fibonacci.get("in_golden_pocket_long"):
                lines.append(f"  피보: 🥇 황금포켓 {fibonacci.get('long_retracement','?')}%")
            elif fibonacci.get("near_key_level_long"):
                lines.append(f"  피보: ✅ 주요레벨 {fibonacci.get('long_retracement','?')}%")
        else:
            if fibonacci.get("in_golden_pocket_short"):
                lines.append(f"  피보: 🥇 황금포켓 {fibonacci.get('short_retracement','?')}%")
            elif fibonacci.get("near_key_level_short"):
                lines.append(f"  피보: ✅ 주요레벨 {fibonacci.get('short_retracement','?')}%")
        lines.append("")

    # ── 시장 심리 (압축) ──────────────────────────────────────────
    lines.append("💡 <b>시장심리</b>")

    fr_pct  = funding.get("rate_pct",0.0) or 0.0
    fr_bias = funding.get("bias","neutral")
    fr_icon = ("🟢" if ((d=="long" and fr_bias=="long_favorable") or (d=="short" and fr_bias=="short_favorable"))
               else ("🔴" if fr_bias!="neutral" else "⚪"))

    ls_bias_v  = ls.get("bias","neutral")
    ls_icon    = ("🟢" if ((d=="long" and ls_bias_v in ("long_favorable","long_extreme")) or
                           (d=="short" and ls_bias_v in ("short_favorable","short_extreme")))
                  else ("🔴" if ls_bias_v!="neutral" else "⚪"))
    ls_display = (f"롱{ls.get('long_pct',0.5)*100:.0f}%/숏{ls.get('short_pct',0.5)*100:.0f}%"
                  if ls.get("available") else "N/A")

    tk_bias = taker.get("bias","neutral")
    tk_icon = ("🟢" if ((d=="long" and tk_bias=="buy_dominant") or (d=="short" and tk_bias=="sell_dominant"))
               else ("🔴" if tk_bias!="neutral" else "⚪"))
    tk_display = (f"매수{taker.get('buy_ratio',0.5)*100:.0f}%/매도{taker.get('sell_ratio',0.5)*100:.0f}%"
                  if taker.get("available") else "N/A")

    lines.append(
        f"  펀딩 {fr_icon}{fr_pct:+.4f}%  |  "
        f"롱숏 {ls_icon}{ls_display}  [{ls_bias_v}]"
    )
    lines.append(f"  Taker {tk_icon}{tk_display}  [{tk_bias}]")

    # MarkFunding 마이크로
    mf_d = next((x for x in micro_details if x[0]=="MarkFunding"), None)
    if mf_d and mf_d[1] != 0:
        mf_icon = "🔴" if mf_d[1]<0 else "🟢"
        lines.append(f"  마크/펀딩 {mf_icon} {mf_d[2].replace('[MF]','').strip()[:40]}  <i>({mf_d[1]:+d}pt)</i>")

    # LS 고래 마이크로
    ls_d = next((x for x in micro_details if x[0]=="LSDivergence"), None)
    if ls_d and ls_d[1] != 0:
        ls_icon2 = "🔴" if ls_d[1]<0 else "🟢"
        lines.append(f"  고래 {ls_icon2} {ls_d[2].replace('⚠️','').replace('✅','').strip()[:40]}  <i>({ls_d[1]:+d}pt)</i>")

    # 청산
    if liq.get("available") and liq.get("signal","none") != "none":
        liq_icon = "💥" if liq.get("is_large") else "⚡"
        hint = liq.get("display_hint","")
        fav  = liq.get("favorable_direction")
        liq_dir = "✅" if fav==d else "⚠️역방향"
        lines.append(f"  청산 {liq_icon}{liq_dir} {hint}")
    lines.append("")

    # ── 지표별 점수 ───────────────────────────────────────────────
    lines.append("📉 <b>지표별 점수</b>  <i>[" + regime_info.get("regime","?") + "]</i>")
    regime_name    = regime_info.get("regime","UNKNOWN")
    actual_weights = config.REGIME_SCORE_WEIGHTS.get(regime_name, config.SCORE_WEIGHTS)

    label_map = {
        "rsi":              "RSI ", "bollinger":        "BB  ",
        "funding_rate":     "FR  ", "long_short_ratio": "LS  ",
        "taker_volume":     "TK  ", "volume":           "Vol ",
    }
    # 2열 레이아웃
    items = list(actual_weights.items())
    for i in range(0, len(items), 2):
        parts = []
        for key, weight in items[i:i+2]:
            s = cs.get(key, 0.0); contrib = s * weight
            bar = _bar(s)
            parts.append(f"{label_map.get(key,key)}{bar} {s:>3.0f}({contrib:>4.1f})")
        lines.append("  " + "  |  ".join(parts))

    # 페널티 표시
    rsi_1h_v = rsi.get("value_1h") or 0
    rsi_4h_v = rsi.get("value_4h") or 0
    ema_m_d  = side_result.get("ema_multiplier",1.0)
    gate_p   = gate.get("funding_penalty",1.0)
    base_thr = regime_info.get("threshold",63)

    penalties = []
    if ema_m_d           < 1.0: penalties.append(f"EMA역방향 ×{ema_m_d:.2f}")
    if gate_p            < 1.0: penalties.append(f"Gate ×{gate_p:.2f}")
    if mtf_penalty       < 1.0: penalties.append(f"MTF-RSI ×{mtf_penalty:.2f}({rsi_1h_v:.0f}/{rsi_4h_v:.0f})")
    if exhaustion_mult   < 1.0: penalties.append(f"소진 ×{exhaustion_mult:.2f}")
    if candle_momentum_m < 1.0: penalties.append(f"캔들모멘텀 ×{candle_momentum_m:.2f}")
    if choch_penalty     < 1.0: penalties.append(f"CHoCH ×{choch_penalty:.2f}")
    if bos_conflict_penalty < 1.0:
        penalties.append(f"BOS역방향 ×{bos_conflict_penalty:.2f}")
    if ranging_bos_weak_p < 1.0:
        penalties.append(f"저ADX+BOS ×{ranging_bos_weak_p:.2f}")
    if explosive_bos_penalty < 1.0:
        comb = round(bos_conflict_penalty*explosive_bos_penalty,3)
        penalties.append(f"EXP+BOS ×{explosive_bos_penalty:.2f}(합산×{comb})")
    if volume_penalty != 0:
        penalties.append(f"거래량 {volume_penalty:+d}pt")
    if micro_total != 0:
        cap_sfx = f"(raw:{micro_raw:+d}→cap)" if micro_raw!=micro_total else ""
        penalties.append(f"마이크로 {micro_total:+d}pt{cap_sfx}")
    if regime_thr > base_thr and rev_c == 3:
        ct_boost = regime_thr - base_thr
        penalties.append(f"ADX역추세 임계+{ct_boost}pt→{regime_thr}pt")

    if penalties:
        lines.append("  ⚠️ " + "  /  ".join(penalties))
    lines.append("")

    # ── 판단 근거 ─────────────────────────────────────────────────
    lines.append("🤖 <b>판단 근거</b>")
    reasons = []

    if micro_critical:
        for _, p, r in micro_critical[:2]:
            rc = r.replace("⚠️","").replace("[Liq]","").replace("[OB]","").replace("[MF]","").replace("[LS]","").replace("[OBI]","").replace("[CM]","").strip()
            reasons.append(f"⚠️ {rc[:50]}")

    if pb_strong: reasons.append(f"★ 눌림목(강) — 1h:{rsi_1h:.0f if rsi_1h else '-'}+15m:{rsi_val:.0f}")
    elif pb_weak: reasons.append(f"★ 눌림목(약) — 1h:{rsi_1h:.0f if rsi_1h else '-'}+15m:{rsi_val:.0f}")
    elif pb_micro:reasons.append(f"★ 눌림목(미) — 1h:{rsi_1h:.0f if rsi_1h else '-'}+15m:{rsi_val:.0f}")

    if d=="long"  and rsi.get("hidden_bull_div"):  reasons.append("★ 히든강세다이버전스 — 추세지속")
    elif d=="short" and rsi.get("hidden_bear_div"):reasons.append("★ 히든약세다이버전스 — 추세지속")
    if d=="long"  and rsi.get("bullish_divergence"):reasons.append("★ 강세다이버전스 — 반전신호")
    elif d=="short" and rsi.get("bearish_divergence"):reasons.append("★ 약세다이버전스 — 반전신호")

    if fvg.get("in_bullish_fvg") and fvg.get("in_bearish_fvg"):
        reasons.append("⚠️ FVG 양방향 모호")
    elif d=="long"  and fvg.get("in_bullish_fvg"):  reasons.append("FVG강세 — 기관매수 주문구간")
    elif d=="short" and fvg.get("in_bearish_fvg"):  reasons.append("FVG약세 — 기관매도 주문구간")

    if d=="long"  and fibonacci.get("in_golden_pocket_long"):
        reasons.append(f"피보황금포켓 {fibonacci.get('long_retracement','?')}%")
    elif d=="short" and fibonacci.get("in_golden_pocket_short"):
        reasons.append(f"피보황금포켓 {fibonacci.get('short_retracement','?')}%")

    if d=="long"  and bos_choch.get("bos_bullish"):  reasons.append("BOS상승확증 — 상승구조지속")
    elif d=="short" and bos_choch.get("bos_bearish"):reasons.append("BOS하락확증 — 하락구조지속")
    elif d=="long"  and bos_choch.get("bos_bearish"):reasons.append(f"⚠️ 하락BOS 역추세롱 ×{bos_conflict_penalty:.2f}")
    elif d=="short" and bos_choch.get("bos_bullish"):reasons.append(f"⚠️ 상승BOS 역추세숏 ×{bos_conflict_penalty:.2f}")

    if ranging_bos_weak_p < 1.0:
        reasons.append(f"⚠️ 저ADX+BOS 추가억제 (ADX:{adx_v:.0f}<{config.ADX_WEAK_TREND})")

    if explosive_bos_penalty < 1.0:
        reasons.append(f"⚠️ EXPLOSIVE국면 역추세 (합산×{round(bos_conflict_penalty*explosive_bos_penalty,3)})")

    taker_bias = taker.get("bias","neutral")
    same_count = ema.get("same_count",0)
    if same_count == 3:
        if d=="long"  and taker_bias=="buy_dominant":  reasons.append("★ EMA3TF+Taker매수 추세지속")
        elif d=="short" and taker_bias=="sell_dominant":reasons.append("★ EMA3TF+Taker매도 추세지속")
    if d=="long"  and bb.get("state") in ("lower_breakout","near_lower"): reasons.append("볼린저 하단 — 반등구간")
    elif d=="short" and bb.get("state") in ("upper_breakout","near_upper"):reasons.append("볼린저 상단 — 하락구간")
    if bb.get("squeeze"): reasons.append("볼린저스퀴즈 — 큰움직임임박")
    if same_count >= 2 and not (pb_strong or pb_weak or pb_micro):
        reasons.append(f"EMA {same_count}/3TF {'상승' if d=='long' else '하락'}일치")

    if micro_bonus:
        for name, p, r in micro_bonus[:1]:
            rc = r.replace("✅","").replace("[OBI]","").replace("[CM]","").strip()[:45]
            reasons.append(f"✅ {rc}")

    for i, r in enumerate(reasons[:6], 1):
        lines.append(f"  {i}. {r}")
    lines.append("")

    # ── 보너스 ────────────────────────────────────────────────────
    if bonuses:
        applied = sum(v for _,v in bonuses)
        is_ct_cap = (bonus_cap == config.COUNTER_TREND_BONUS_CAP and bonus_total < applied)
        cap_note  = (f"  <i>(역추세캡:{bonus_cap}pt)</i>" if is_ct_cap else
                     f"  <i>(상한:{bonus_cap}pt)</i>"       if bonus_total < applied else "")
        lines.append(f"🎁 보너스 <b>+{bonus_total}pt</b>{cap_note}")
        main_b = [(n,v) for n,v in bonuses if v >= 4]
        minor  = sum(v for _,v in bonuses if v < 4)
        parts  = [f"{n}(+{v})" for n,v in main_b]
        if minor > 0: parts.append(f"기타(+{minor})")
        lines.append(f"  {' · '.join(parts)}")
        lines.append("")

    if gate.get("penalty_reason"):
        lines.append(f"⚠️ <i>{gate['penalty_reason']}</i>")
        lines.append("")

    lines.append(f"<code>{'─'*32}</code>")
    lines.append(f"⚙️ 임계:{regime_thr}pt | 쿨다운:{config.SIGNAL_COOLDOWN_MINUTES}분 | OKX")
    lines.append("<i>⚠️ 참고용 신호입니다. 투자 결정은 본인 책임입니다.</i>")

    msg = "\n".join(lines)
    return msg[:3980] + "\n\n<i>...(생략)</i>" if len(msg) > 4000 else msg


# ══════════════════════════════════════════════════════════════════════════════
# 시스템 메시지
# ══════════════════════════════════════════════════════════════════════════════

def send_error_alert(error_msg: str, context: str = "") -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    send_message(
        f"🚨 <b>시스템 에러</b>\n<code>{'─'*32}</code>\n🕐 {now}\n"
        f"📍 {context or '—'}\n\n<pre>{error_msg[:800]}</pre>"
    )


def send_heartbeat(symbols: list, scan_count: int, signal_count: int) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    send_message(
        f"💚 <b>봇 정상 동작 중</b>\n<code>{'─'*32}</code>\n🕐 {now}\n"
        f"🪙 {', '.join(symbols)}\n🔄 실행:{scan_count}회 | 🚨 신호:{signal_count}건"
    )


def notify_signal(pipeline_result: dict, analysis: dict) -> bool:
    from scoring_system import record_signal_sent
    if not pipeline_result.get("should_notify"):
        return False
    symbol        = pipeline_result["symbol"]
    direction     = pipeline_result["direction"]
    current_price = analysis.get("current_price") or 0.0
    logger.info(f"[Notify] {symbol} {direction.upper()} {pipeline_result['score']:.1f}pt — 발송")
    msg    = build_signal_message(pipeline_result, analysis)
    result = send_message(msg)
    if result:
        record_signal_sent(symbol, direction, current_price)
        logger.info("[Notify] ✅ 발송 완료")
        return True
    logger.error("[Notify] ❌ 발송 실패")
    return False
