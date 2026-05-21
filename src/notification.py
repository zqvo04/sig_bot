"""
notification.py — 텔레그램 알림 (v3.4)
──────────────────────────────────────────────────────────────────────────────
[v3.4 변경]

★ EXPLOSIVE + BOS 역방향 강화 패널티 표시 추가
  scoring_system v3.4에서 추가된 explosive_bos_penalty(×0.85)를
  "지표별 점수" 섹션에 표시.
  "판단 근거"에도 EXPLOSIVE+BOS 강화 패널티 적용 사실 추가.

★ ADX 연동 역추세 임계값 부스트 표시 추가
  EMA 3역방향 + ADX 강도에 따라 임계값이 상향된 경우
  "지표별 점수" 섹션에 표시.
  base_threshold(regime.threshold) vs 실제 regime_thr 비교로 부스트 감지.

★ 역추세 보너스 캡(14pt) 구분 표시
  BOS역방향+EMA3역방향 동시 → 역추세 캡(14pt) 적용 시
  기존 티어드 캡과 구별되는 표시로 변경.

★ FVG 양방향 + 저거래량 차단 (⑩)
  차단된 신호는 notify_signal 자체가 호출되지 않으므로 알림 없음.
  FVG 양방향 활성은 기존 SMC 섹션의 "방향 모호 구간" 표시로 이미 안내됨.

[v3.3]
  BOS 역방향 패널티 표시, adx_multiplier 제거, OI 섹션 제거
──────────────────────────────────────────────────────────────────────────────
"""
import logging, time
from datetime import datetime, timezone
from typing import Optional
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

def _bar(score, width=10):
    f = max(0, min(width, int(round(score / 100 * width))))
    return f"{'█'*f}{'░'*(width-f)} {score:.0f}pt"


def _fmt_price(price, symbol):
    if price is None:
        return "N/A"
    return (f"${price:,.2f}" if any(s in symbol for s in ["BTC", "ETH"])
            else f"${price:,.4f}")


def _calc_sl_tp(current_price, direction, atr_val, rr=2.0, atr_mult=1.5,
                entry_price=None):
    ep = entry_price or current_price
    if not ep or not atr_val or atr_val <= 0:
        return None, None, None, None
    sl_dist = atr_val * atr_mult
    tp_dist = sl_dist * rr
    if direction in ("long", "LONG"):
        sl = ep - sl_dist; tp = ep + tp_dist
    else:
        sl = ep + sl_dist; tp = ep - tp_dist
    return (
        round(sl, 4), round(tp, 4),
        round(sl_dist / ep * 100, 2),
        round(tp_dist / ep * 100, 2),
    )


def _micro_label(name: str) -> str:
    """마이크로구조 방안 이름 → 한국어 라벨"""
    return {
        "LiqCascade":   "청산캐스케이드",
        "OrderBook":    "오더북벽",
        "OBImbalance":  "호가잔량불균형",
        "CandleMom":    "캔들모멘텀",
        "MarkFunding":  "마크/펀딩",
        "LSDivergence": "LS괴리(고래)",
    }.get(name, name)


def _micro_severity(penalty: int) -> str:
    if penalty <= -12:  return "🔴"
    if penalty < 0:     return "🟡"
    if penalty > 0:     return "🟢"
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
    micro_entry    = micro.get("suggested_entry")
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
    atr       = analysis.get("atr",           {})
    price     = analysis.get("current_price")

    cs         = side_result.get("component_scores", {})
    bonuses    = side_result.get("bonuses",           [])
    gate       = side_result.get("gate_info",         {})
    regime_thr = side_result.get("regime_threshold",  63)

    mtf_penalty          = side_result.get("mtf_penalty",            1.0)
    exhaustion_mult      = side_result.get("exhaustion_mult",         1.0)
    candle_momentum_m    = side_result.get("candle_momentum_mult",    1.0)
    choch_penalty        = side_result.get("choch_penalty",           1.0)
    bos_conflict_penalty = side_result.get("bos_conflict_penalty",    1.0)
    explosive_bos_penalty = side_result.get("explosive_bos_penalty",  1.0)  # [v3.4]
    bonus_cap            = side_result.get("bonus_cap",               36)
    bonus_total          = side_result.get("bonus_total",             0)
    volume_penalty       = side_result.get("volume_penalty",          0)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = []

    same_count    = ema.get("same_count",    0)
    reverse_count = ema.get("reverse_count", 0)

    # ── 등급 ──────────────────────────────────────────────────
    if score >= 85:
        grade_icon, grade_label, grade_desc = "🔥🔥", "STRONG", "매우 강한 신호 — 즉시 대응 권장"
    elif score >= 72:
        grade_icon, grade_label, grade_desc = "🔥",   "GOOD",   "좋은 신호 — 표준 진입"
    else:
        grade_icon, grade_label, grade_desc = "📊",   "WATCH",  "기준 통과 — 확인 후 진입"

    if micro_critical and grade_label != "WATCH":
        grade_icon  = "⚠️"
        grade_label = grade_label + "⚠"
        grade_desc  = grade_desc + " | 마이크로구조 경고 확인"

    # ── EMA 정합 ──────────────────────────────────────────────
    if   same_count == 3: trend_align, trend_detail = "✅ 순방향 3/3", "3개 TF EMA 모두 신호 방향 일치"
    elif same_count == 2: trend_align, trend_detail = "✅ 순방향 2/3", "2개 TF EMA 신호 방향 일치"
    elif same_count == 1: trend_align, trend_detail = "⚠️ 부분 역방향 2/3", "상위 TF와 방향 불일치 — 주의"
    else:                 trend_align, trend_detail = "⚠️ 역방향 3/3", "모든 TF EMA가 반대 방향 — 역추세 진입"

    # ── 눌림목 ────────────────────────────────────────────────
    pb_strong = rsi.get("pullback_long_strong"  if direction=="long" else "pullback_short_strong", False)
    pb_weak   = rsi.get("pullback_long_weak"    if direction=="long" else "pullback_short_weak",   False)
    pb_micro  = rsi.get("pullback_long_micro"   if direction=="long" else "pullback_short_micro",  False)
    pullback_str = ""
    if direction == "long":
        if pb_strong:  pullback_str = "  ★ 눌림목 롱(강) — 1h RSI 강세(>58) + 15m 과매도(<40)"
        elif pb_weak:  pullback_str = "  ★ 눌림목 롱(약) — 1h RSI 중립상(>52) + 15m 눌림(<44)"
        elif pb_micro: pullback_str = "  ★ 눌림목 롱(미) — 1h RSI 최소조건 + 15m 소폭 눌림(<45)"
    else:
        if pb_strong:  pullback_str = "  ★ 눌림목 숏(강) — 1h RSI 약세(<42) + 15m 과매수(>60)"
        elif pb_weak:  pullback_str = "  ★ 눌림목 숏(약) — 1h RSI 중립하(<48) + 15m 과열(>56)"
        elif pb_micro: pullback_str = "  ★ 눌림목 숏(미) — 1h RSI 최소조건 + 15m 소폭 과열(>55)"

    # ── SMC 태그 ──────────────────────────────────────────────
    smc_tags = []
    if fvg.get("in_bullish_fvg" if direction=="long" else "in_bearish_fvg"):
        smc_tags.append("FVG")
    if bos_choch.get("bos_bullish" if direction=="long" else "bos_bearish"):
        smc_tags.append("BOS↑" if direction=="long" else "BOS↓")
    if fibonacci.get("in_golden_pocket_long" if direction=="long" else "in_golden_pocket_short"):
        retr = fibonacci.get("long_retracement" if direction=="long" else "short_retracement")
        smc_tags.append(f"황금포켓({retr}%)" if retr else "황금포켓")

    # ── 헤더 ──────────────────────────────────────────────────
    dir_icon = "🟢" if direction == "long" else "🔴"
    dir_text = "롱(LONG)" if direction == "long" else "숏(SHORT)"
    lines.append(f"{dir_icon} <b>{dir_text} 진입 신호</b>  {grade_icon} <b>{grade_label}</b>")
    lines.append(f"<code>{'─'*32}</code>")
    lines.append(f"🪙 <b>{symbol}</b>   💰 <b>{_fmt_price(price, symbol)}</b>")

    micro_note = f"  <i>(micro:{micro_total:+d}pt)</i>" if micro_total != 0 else ""
    lines.append(f"🎯 신뢰도: <b>{score:.1f}pt</b>  {_bar(score)}  (임계:{regime_thr}pt){micro_note}")
    lines.append(f"📌 {grade_desc}")
    lines.append(f"📐 추세 정합: {trend_align}  <i>{trend_detail}</i>")
    if pullback_str:
        lines.append(f"<b>{pullback_str}</b>")
    if smc_tags:
        lines.append(f"🏛 SMC 확인: <b>{' | '.join(smc_tags)}</b>")
    lines.append(f"🕐 {now}")
    lines.append("")
    

    # ── 마이크로구조 필터 ─────────────────────────────────────
    if micro_details:
        cap_note = (f" → cap {micro_total:+d}pt" if micro_raw != micro_total else "")
        lines.append(f"🔬 <b>마이크로구조 필터</b>  합계:<b>{micro_raw:+d}pt</b>{cap_note}")
        for name, p, reason in micro_details:
            icon    = _micro_severity(p)
            label   = _micro_label(name)
            r_short = reason.replace("⚠️","").replace("✅","").strip()[:60]
            lines.append(f"  {icon} {label}: <b>{p:+d}pt</b>  <i>{r_short}</i>")

        obi = next((d for d in micro_details if d[0] == "OBImbalance"), None)
        if obi and obi[1] != 0:
            obi_icon = "🟢" if obi[1] > 0 else "🔴"
            lines.append(
                f"  └ 호가잔량: {obi_icon} "
                f"{obi[2].replace('✅','').replace('⚠️','').strip()[:45]}"
            )
        lines.append("")

    # ── 시장 국면 ─────────────────────────────────────────────
    r_icon = regime_info.get("icon", "")
    r_name = regime_info.get("regime", "?")
    r_desc = regime_info.get("description", "")
    lines.append(f"📊 <b>시장 국면: {r_icon} {r_name}</b>")
    lines.append(f"   <i>{r_desc}</i>")
    lines.append("")

    # ── 기술 지표 ─────────────────────────────────────────────
    lines.append("📈 <b>기술 지표</b>")

    rsi_val = rsi.get("value", 50.0)
    rsi_1h  = rsi.get("value_1h")
    rsi_4h  = rsi.get("value_4h")
    rsi_tag = ("⚡ 과매도" if rsi.get("state") == "oversold" else
               "⚡ 과매수" if rsi.get("state") == "overbought" else "— 중립")

    div_s = ""
    if direction == "long":
        if rsi.get("hidden_bull_div"):      div_s = "  📊히든강세(추세지속)"
        elif rsi.get("bullish_divergence"): div_s = "  ✅강세다이버전스(반전)"
    else:
        if rsi.get("hidden_bear_div"):      div_s = "  📊히든약세(추세지속)"
        elif rsi.get("bearish_divergence"): div_s = "  ✅약세다이버전스(반전)"

    rsi_tf = [f"15m:<code>{rsi_val:.0f}</code>"]
    if rsi_1h is not None: rsi_tf.append(f"1h:<code>{rsi_1h:.0f}</code>")
    if rsi_4h is not None: rsi_tf.append(f"4h:<code>{rsi_4h:.0f}</code>")
    lines.append(f"  RSI({config.RSI_PERIOD}) : {' / '.join(rsi_tf)}  {rsi_tag}{div_s}")

    bb_map = {
        "lower_breakout": "🔵 하단이탈", "near_lower": "↘하단영역",
        "lower_zone":     "↘하단영역",   "middle":     "— 중앙",
        "upper_zone":     "↗상단영역",   "near_upper": "🔴 상단근접",
        "upper_breakout": "🔴 상단이탈",
    }
    bb_tag = bb_map.get(bb.get("state", ""), "—")
    sq_s   = "  🔊 스퀴즈" if bb.get("squeeze") else ""
    sk_s   = ""
    if bb.get("lower_streak", 0) >= 2:
        sk_s = f"  ⚠️하단이탈{bb['lower_streak']}캔들"
    if bb.get("upper_streak", 0) >= 2:
        sk_s = f"  ⚠️상단이탈{bb['upper_streak']}캔들"
    lines.append(
        f"  볼린저밴드: {bb_tag}  (%B:{bb.get('pct_b',0.5):.2f}){sq_s}{sk_s}"
    )

    ema_tf   = ema.get("tf_signals", {})
    ema_mult = ema.get("multiplier", 1.0)
    ema_str  = " | ".join(
        f"{tf}:{'↑' if s=='bullish' else ('↓' if s=='bearish' else '—')}"
        for tf, s in ema_tf.items()
    )
    ema_rev  = ema.get("reverse_count", 0)
    ema_warn = "" if ema_mult == 1.0 else f"  ⚠️역방향{ema_rev}TF(×{ema_mult:.2f})"
    lines.append(f"  EMA교차  : [{ema_str}]{ema_warn}")

    adx_map = {
        "strong": "🔥강한추세", "normal": "📈추세중",
        "weak":   "〰약한추세", "none":   "💤횡보",
    }
    lines.append(
        f"  ADX({config.ADX_PERIOD})  : "
        f"<code>{adx.get('adx',0):.1f}</code>  "
        f"{adx_map.get(adx.get('strength','none'),'—')}"
    )
    lines.append("")

    # ── SMC / 구조 분석 ───────────────────────────────────────
    has_smc = (
        fvg.get("in_bullish_fvg") or fvg.get("in_bearish_fvg") or
        bos_choch.get("bos_bullish") or bos_choch.get("bos_bearish") or
        bos_choch.get("choch_bullish") or bos_choch.get("choch_bearish") or
        fibonacci.get("in_golden_pocket_long") or fibonacci.get("in_golden_pocket_short") or
        fibonacci.get("near_key_level_long")   or fibonacci.get("near_key_level_short")
    )
    if has_smc:
        lines.append("🏛 <b>SMC / 구조 분석</b>")

        # FVG
        bull_fvg = fvg.get("in_bullish_fvg", False)
        bear_fvg = fvg.get("in_bearish_fvg", False)
        if bull_fvg and bear_fvg:
            lines.append("  FVG      : ⚠️ 강세+약세 FVG 동시 — 방향 모호 구간 (각 보너스 ÷2 적용)")
        elif bull_fvg:
            cnt = fvg.get("bullish_fvg_count", 0)
            lines.append(f"  FVG      : ✅ 강세 FVG 내부  (미충전 {cnt}개) — 기관 매수 주문 구간")
        elif bear_fvg:
            cnt = fvg.get("bearish_fvg_count", 0)
            lines.append(f"  FVG      : ✅ 약세 FVG 내부  (미충전 {cnt}개) — 기관 매도 주문 구간")
        else:
            lines.append("  FVG      : — FVG 외부")

        # BOS / CHoCH
        if bos_choch.get("bos_bullish"):
            if direction == "short":
                lines.append("  BOS/CHoCH: ✅ 상승 BOS 확증 — 스윙고점 돌파, 상승 추세 지속")
                lines.append(
                    f"    └ ⛔ 숏 진입과 역방향 — BOS 충돌 패널티 ×{bos_conflict_penalty:.2f} 적용됨"
                )
            else:
                lines.append("  BOS/CHoCH: ✅ 상승 BOS 확증 — 스윙고점 돌파, 상승 추세 지속")

        elif bos_choch.get("bos_bearish"):
            if direction == "long":
                lines.append("  BOS/CHoCH: ✅ 하락 BOS 확증 — 스윙저점 이탈, 하락 추세 지속")
                lines.append(
                    f"    └ ⛔ 롱 진입과 역방향 — BOS 충돌 패널티 ×{bos_conflict_penalty:.2f} 적용됨"
                )
            else:
                lines.append("  BOS/CHoCH: ✅ 하락 BOS 확증 — 스윙저점 이탈, 하락 추세 지속")

        elif bos_choch.get("choch_bullish"):
            lines.append("  BOS/CHoCH: ⚠️ 상승전환 CHoCH — 하락→상승 구조 전환 경고")
            if direction == "short":
                lines.append(
                    f"    └ ⛔ 숏 진입과 역방향 — CHoCH 패널티 ×{choch_penalty:.2f} 적용됨"
                )

        elif bos_choch.get("choch_bearish"):
            lines.append("  BOS/CHoCH: ⚠️ 하락전환 CHoCH — 상승→하락 구조 전환 경고")
            if direction == "long":
                lines.append(
                    f"    └ ⛔ 롱 진입과 역방향 — CHoCH 패널티 ×{choch_penalty:.2f} 적용됨"
                )

        else:
            last_sh = bos_choch.get("last_swing_high")
            last_sl = bos_choch.get("last_swing_low")
            if last_sh or last_sl:
                parts = []
                if last_sh: parts.append(f"고점:{_fmt_price(last_sh, symbol)}")
                if last_sl: parts.append(f"저점:{_fmt_price(last_sl, symbol)}")
                lines.append(f"  BOS/CHoCH: — 구조 유지  ({', '.join(parts)})")
            else:
                lines.append("  BOS/CHoCH: — 구조 분석 불충분")

        # 피보나치
        if direction == "long":
            if fibonacci.get("in_golden_pocket_long"):
                retr = fibonacci.get("long_retracement", "?")
                lines.append(f"  피보나치  : 🥇 황금포켓 {retr}% (61.8~65%) — 최고 강도 반전 구간")
            elif fibonacci.get("near_key_level_long"):
                retr = fibonacci.get("long_retracement", "?")
                lines.append(f"  피보나치  : ✅ 주요레벨 근접 {retr}% (38.2/50/78.6%)")
            else:
                retr = fibonacci.get("long_retracement")
                lines.append(
                    f"  피보나치  : — 주요레벨 외부"
                    + (f"  (현재 {retr}% 되돌림)" if retr else "")
                )
        else:
            if fibonacci.get("in_golden_pocket_short"):
                retr = fibonacci.get("short_retracement", "?")
                lines.append(f"  피보나치  : 🥇 황금포켓 {retr}% (61.8~65%) — 최고 강도 재진입 구간")
            elif fibonacci.get("near_key_level_short"):
                retr = fibonacci.get("short_retracement", "?")
                lines.append(f"  피보나치  : ✅ 주요레벨 근접 {retr}%")
            else:
                retr = fibonacci.get("short_retracement")
                lines.append(
                    f"  피보나치  : — 주요레벨 외부"
                    + (f"  (현재 {retr}% 반등)" if retr else "")
                )

        lines.append("")

    # ── 시장 심리 ─────────────────────────────────────────────
    lines.append("💡 <b>시장 심리</b>")

    fr_pct  = funding.get("rate_pct", 0.0) or 0.0
    fr_bias = funding.get("bias", "neutral")
    fr_icon = (
        "🟢" if ((direction=="long"  and fr_bias=="long_favorable") or
                 (direction=="short" and fr_bias=="short_favorable"))
        else ("🔴" if fr_bias != "neutral" else "⚪")
    )
    lines.append(
        f"  펀딩비   : {fr_icon} {fr_pct:+.4f}%  [{fr_bias}]"
        if funding.get("available") else "  펀딩비   : ⚪ N/A"
    )

    mf_detail = next((d for d in micro_details if d[0] == "MarkFunding"), None)
    if mf_detail and mf_detail[1] != 0:
        mf_icon  = "🔴" if mf_detail[1] < 0 else "🟢"
        mf_short = mf_detail[2].replace("[MF]","").strip()[:45]
        lines.append(f"  마크/펀딩 : {mf_icon} {mf_short}  <i>({mf_detail[1]:+d}pt)</i>")

    if ls.get("available"):
        ls_bias_v = ls.get("bias", "neutral")
        ls_icon   = (
            "🟢" if ((direction=="long"  and ls_bias_v in ("long_favorable","long_extreme")) or
                     (direction=="short" and ls_bias_v in ("short_favorable","short_extreme")))
            else ("🔴" if ls_bias_v != "neutral" else "⚪")
        )
        lines.append(
            f"  롱숏비율 : {ls_icon} "
            f"롱{ls.get('long_pct',0.5)*100:.1f}% / "
            f"숏{ls.get('short_pct',0.5)*100:.1f}%  [{ls_bias_v}]"
        )
        ls_div = next((d for d in micro_details if d[0] == "LSDivergence"), None)
        if ls_div and ls_div[1] != 0:
            ls_icon2 = "🔴" if ls_div[1] < 0 else "🟢"
            ls_short = ls_div[2].replace("⚠️","").replace("✅","").strip()[:50]
            lines.append(
                f"  └ 고래포지션: {ls_icon2} {ls_short}  <i>({ls_div[1]:+d}pt)</i>"
            )
    else:
        lines.append("  롱숏비율 : ⚪ N/A")

    if taker.get("available"):
        tk_bias = taker.get("bias", "neutral")
        tk_icon = (
            "🟢" if ((direction=="long"  and tk_bias=="buy_dominant") or
                     (direction=="short" and tk_bias=="sell_dominant"))
            else ("🔴" if tk_bias != "neutral" else "⚪")
        )
        lines.append(
            f"  Taker    : {tk_icon} "
            f"매수{taker.get('buy_ratio',0.5)*100:.1f}% / "
            f"매도{taker.get('sell_ratio',0.5)*100:.1f}%  [{tk_bias}]"
        )
    else:
        lines.append("  Taker    : ⚪ N/A")

    # 청산 감지
    if liq.get("available") and liq.get("signal", "none") != "none":
        liq_icon     = "💥" if liq.get("is_large") else "⚡"
        display_hint = liq.get("display_hint", "")
        fav_dir      = liq.get("favorable_direction")
        lw = liq.get("long_liq_proxy",  0)
        sw = liq.get("short_liq_proxy", 0)
        liq_cascade = next((d for d in micro_details if d[0] == "LiqCascade"), None)
        if liq_cascade and liq_cascade[1] < 0:
            lines.append(
                f"  청산감지  : ⚠️ {display_hint}  "
                f"<i>(API패널티 {liq_cascade[1]:+d}pt와 상충 — API 우선)</i>"
            )
        elif fav_dir == direction:
            lines.append(
                f"  청산감지  : {liq_icon} {display_hint}  "
                f"(롱:{lw:.2f} / 숏:{sw:.2f})"
            )
        else:
            lines.append(
                f"  청산감지  : ⚠️ {display_hint}  "
                f"← 진입 방향 역방향 주의  (롱:{lw:.2f} / 숏:{sw:.2f})"
            )
    lines.append("")

    # ── 지표별 점수 ───────────────────────────────────────────
    lines.append("📉 <b>지표별 점수</b>")
    regime_name    = regime_info.get("regime", "UNKNOWN")
    actual_weights = config.REGIME_SCORE_WEIGHTS.get(regime_name, config.SCORE_WEIGHTS)

    label_map = {
        "rsi":              "RSI         ",
        "bollinger":        "볼린저밴드  ",
        "funding_rate":     "펀딩비      ",
        "long_short_ratio": "롱숏비율    ",
        "taker_volume":     "Taker비율   ",
        "volume":           "거래량      ",
    }
    for key, weight in actual_weights.items():
        s       = cs.get(key, 0.0)
        contrib = s * weight
        lines.append(f"  {label_map.get(key, key)}: {_bar(s, 8)}  <i>({contrib:.1f}pt)</i>")

    # 패널티 표시
    ema_m_d  = side_result.get("ema_multiplier", 1.0)
    gate_p   = gate.get("funding_penalty",       1.0)
    rsi_1h_v = rsi.get("value_1h") or 0
    rsi_4h_v = rsi.get("value_4h") or 0

    if ema_m_d              < 1.0:
        lines.append(f"  EMA역방향 배율           : ×{ema_m_d:.2f}")
    if gate_p               < 1.0:
        lines.append(f"  복합 페널티              : ×{gate_p:.2f}")
    if mtf_penalty          < 1.0:
        lines.append(
            f"  ⚠️ MTF RSI 과열 패널티  : ×{mtf_penalty:.2f}  (1h:{rsi_1h_v:.0f} 4h:{rsi_4h_v:.0f})"
        )
    if exhaustion_mult      < 1.0:
        lines.append(
            f"  ⚠️ EXPLOSIVE 소진 패널티: ×{exhaustion_mult:.2f}  (1h RSI:{rsi_1h_v:.0f})"
        )
    if candle_momentum_m    < 1.0:
        lines.append(f"  ⚠️ 캔들 모멘텀 역방향   : ×{candle_momentum_m:.2f}")
    if choch_penalty        < 1.0:
        lines.append(
            f"  ⚠️ CHoCH 역방향 패널티  : ×{choch_penalty:.2f}  (추세 전환 경고 중)"
        )
    if bos_conflict_penalty < 1.0:
        lines.append(
            f"  ⚠️ BOS 역방향 패널티    : ×{bos_conflict_penalty:.2f}  "
            f"({'하락' if direction=='long' else '상승'} BOS 확증 중 역추세 진입)"
        )

    # [v3.4] EXPLOSIVE + BOS 강화 패널티 표시
    if explosive_bos_penalty < 1.0:
        combined = round(bos_conflict_penalty * explosive_bos_penalty, 3)
        lines.append(
            f"  ⚠️ EXPLOSIVE+BOS 강화패널티: ×{explosive_bos_penalty:.2f}  "
            f"(합산 ×{combined:.3f})"
        )

    # [v3.4] ADX 역추세 임계값 부스트 표시
    # base_threshold와 실제 regime_thr 비교로 부스트 감지
    base_threshold = regime_info.get("threshold", 63)
    if regime_thr > base_threshold and reverse_count == 3:
        ct_boost    = regime_thr - base_threshold
        adx_val_cur = adx.get("adx", 0.0)
        lines.append(
            f"  ⚠️ ADX 역추세 임계값 상향: +{ct_boost}pt  "
            f"(ADX:{adx_val_cur:.0f} → 임계 {regime_thr}pt)"
        )

    if volume_penalty != 0:
        lines.append(f"  거래량 페널티            : {volume_penalty:+d}pt")
    if micro_total != 0:
        cap_sfx = f" (raw:{micro_raw:+d}pt → cap)" if micro_raw != micro_total else ""
        lines.append(f"  🔬 마이크로구조 합계     : {micro_total:+d}pt{cap_sfx}")
    lines.append("")

    # ── 판단 근거 ─────────────────────────────────────────────
    lines.append("🤖 <b>판단 근거</b>")
    reasons = []

    if micro_critical:
        for _, p, r in micro_critical[:2]:
            r_clean = (r.replace("⚠️","").replace("[Liq]","").replace("[OI]","")
                        .replace("[OB]","").replace("[MF]","").replace("[LS]","")
                        .replace("[OBI]","").replace("[CM]","").strip())
            reasons.append(f"⚠️ {r_clean[:55]}")

    pb_any = rsi.get("pullback_long" if direction=="long" else "pullback_short", False)
    if pb_any:
        grade    = "강" if pb_strong else ("약" if pb_weak else "미세")
        rsi_1h_s = f"{rsi_1h:.1f}" if rsi_1h else "-"
        reasons.append(
            f"★ 눌림목({grade}) {'롱' if direction=='long' else '숏'} "
            f"— 1h RSI({rsi_1h_s})+15m({rsi_val:.0f})"
        )

    if direction=="long"  and rsi.get("hidden_bull_div"):
        reasons.append("★ 히든 강세 다이버전스 — 가격 Higher Low + RSI Lower Low → 추세 지속")
    elif direction=="short" and rsi.get("hidden_bear_div"):
        reasons.append("★ 히든 약세 다이버전스 — 가격 Lower High + RSI Higher High → 추세 지속")

    if fvg.get("in_bullish_fvg") and fvg.get("in_bearish_fvg"):
        reasons.append("⚠️ 강세+약세 FVG 동시 활성 — 방향 모호성 높음 (보너스 반감)")
    elif direction=="long"  and fvg.get("in_bullish_fvg"):
        reasons.append("FVG 강세 구간 — 기관 미체결 매수 주문 대기 레벨")
    elif direction=="short" and fvg.get("in_bearish_fvg"):
        reasons.append("FVG 약세 구간 — 기관 미체결 매도 주문 대기 레벨")

    if direction=="long"  and fibonacci.get("in_golden_pocket_long"):
        reasons.append(f"피보 황금포켓 {fibonacci.get('long_retracement','?')}% — 가장 강력한 반전 구간")
    elif direction=="short" and fibonacci.get("in_golden_pocket_short"):
        reasons.append(f"피보 황금포켓 {fibonacci.get('short_retracement','?')}% — 가장 강력한 재진입 구간")

    # BOS 방향 일치
    if direction=="long"  and bos_choch.get("bos_bullish"):
        reasons.append("BOS 상승 확증 — 스윙고점 돌파로 상승 구조 지속")
    elif direction=="short" and bos_choch.get("bos_bearish"):
        reasons.append("BOS 하락 확증 — 스윙저점 이탈로 하락 구조 지속")
    # BOS 방향 충돌 (역추세)
    elif direction=="long"  and bos_choch.get("bos_bearish"):
        reasons.append(f"⚠️ 하락 BOS 중 역추세 롱 — BOS 패널티 ×{bos_conflict_penalty:.2f}")
    elif direction=="short" and bos_choch.get("bos_bullish"):
        reasons.append(f"⚠️ 상승 BOS 중 역추세 숏 — BOS 패널티 ×{bos_conflict_penalty:.2f}")

    # [v3.4] EXPLOSIVE + BOS 역방향 강화 패널티 이유 표시
    if explosive_bos_penalty < 1.0:
        combined = round(bos_conflict_penalty * explosive_bos_penalty, 3)
        reasons.append(
            f"⚠️ EXPLOSIVE 국면 역추세 — 강화 패널티 적용 (합산 ×{combined:.3f})"
        )

    taker_bias = taker.get("bias", "neutral")
    vol_strong = analysis.get("volume", {}).get("strong", False)
    bb_state_n = bb.get("state", "")
    st         = rsi.get("state", "neutral")

    if same_count == 3:
        if direction=="long"  and taker_bias=="buy_dominant":
            reasons.append("★ 추세 지속 — EMA 3TF+Taker 매수 일치")
        elif direction=="short" and taker_bias=="sell_dominant":
            reasons.append("★ 추세 지속 — EMA 3TF+Taker 매도 일치")
        if vol_strong:
            reasons.append("추세 가속 — 거래량 급증 동반")

    if direction=="long"  and st=="oversold":    reasons.append("RSI 과매도 — 반등 구간")
    elif direction=="short" and st=="overbought": reasons.append("RSI 과매수 — 하락 구간")
    if direction=="long"  and bb_state_n in ("lower_breakout","near_lower"):
        reasons.append("볼린저 하단 — 반등 타이밍")
    if direction=="short" and bb_state_n in ("upper_breakout","near_upper"):
        reasons.append("볼린저 상단 — 하락 타이밍")
    if bb.get("squeeze"):
        reasons.append("볼린저 스퀴즈 — 큰 움직임 임박")
    if same_count >= 2 and not pb_any:
        reasons.append(f"EMA {same_count}/3TF {'상승' if direction=='long' else '하락'} 일치")
    if (taker.get("available") and taker.get("strength") == "strong"
            and not any("Taker" in r for r in reasons)):
        t_icon = "강한 매수체결" if taker_bias=="buy_dominant" else "강한 매도체결"
        reasons.append(
            f"Taker {t_icon} "
            f"({taker.get('buy_ratio' if direction=='long' else 'sell_ratio',0)*100:.0f}%)"
        )

    if micro_bonus:
        for name, p, r in micro_bonus[:1]:
            r_clean = (r.replace("✅","").replace("[OI]","").replace("[Liq]","")
                        .replace("[OBI]","").replace("[CM]","").strip()[:50])
            reasons.append(f"✅ {r_clean}")

    for i, r in enumerate(reasons[:7], 1):
        lines.append(f"  {i}. {r}")
    lines.append("")

    # ── 보너스 ────────────────────────────────────────────────
    if bonuses:
        applied_bonus = sum(v for _, v in bonuses)

        # [v3.4] 역추세 캡(14pt)과 일반 티어드 캡 구분 표시
        is_ct_cap = (bonus_cap == config.COUNTER_TREND_BONUS_CAP
                     and bonus_total < applied_bonus)
        if is_ct_cap:
            cap_note = f"  <i>(역추세 캡:{bonus_cap}pt 적용)</i>"
        elif bonus_total < applied_bonus:
            cap_note = f"  <i>(상한:{bonus_cap}pt 적용)</i>"
        else:
            cap_note = ""

        lines.append(f"🎁 보너스 +{bonus_total}pt{cap_note}")
        main_b = [(n, v) for n, v in bonuses if v >= 4]
        minor  = sum(v for _, v in bonuses if v < 4)
        parts  = [f"{n}(+{v}pt)" for n, v in main_b]
        if minor > 0:
            parts.append(f"기타(+{minor}pt)")
        lines.append(f"  {' · '.join(parts)}")
        lines.append("")

    if gate.get("penalty_reason"):
        lines.append(f"⚠️ <i>{gate['penalty_reason']}</i>")
        lines.append("")

    lines.append(f"<code>{'─'*32}</code>")
    lines.append(f"⚙️ 임계값 {regime_thr}pt | 쿨다운 {config.SIGNAL_COOLDOWN_MINUTES}분 | OKX")
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
    logger.info(
        f"[Notify] {symbol} {direction.upper()} "
        f"{pipeline_result['score']:.1f}pt — 발송"
    )
    msg    = build_signal_message(pipeline_result, analysis)
    result = send_message(msg)
    if result:
        record_signal_sent(symbol, direction, current_price)
        logger.info("[Notify] ✅ 발송 완료")
        return True
    logger.error("[Notify] ❌ 발송 실패")
    return False