"""
notification.py — 텔레그램 알림
수정: Bug3 — notify_signal에서 current_price 추출 후 record_signal_sent에 전달 (A2용)
추가: MTF RSI 패널티 / EXPLOSIVE 소진 패널티 표시
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


def _send_telegram(method, payload, retries=3):
    if not config.TELEGRAM_BOT_TOKEN: return None
    url = _TG_BASE.format(token=config.TELEGRAM_BOT_TOKEN, method=method)
    for attempt in range(1, retries+1):
        try:
            r = requests.post(url, json=payload, timeout=10)
            if r.status_code==200:
                d=r.json()
                if d.get("ok"): return d
                logger.error(f"[TG] API 오류: {d.get('description')}"); return None
            elif r.status_code==429:
                time.sleep(int(r.headers.get("Retry-After",5)))
            else:
                time.sleep(3*attempt)
        except Exception as e:
            logger.error(f"[TG] 오류: {e}"); time.sleep(3*attempt)
    return None


def send_message(text, parse_mode="HTML"):
    if not config.TELEGRAM_CHAT_ID: return None
    return _send_telegram("sendMessage",{
        "chat_id":config.TELEGRAM_CHAT_ID,"text":text,
        "parse_mode":parse_mode,"disable_web_page_preview":True,
    })


def _bar(score, width=10):
    f=max(0,min(width,int(round(score/100*width))))
    return f"{'█'*f}{'░'*(width-f)} {score:.0f}pt"

def _fmt_price(price, symbol):
    if price is None: return "N/A"
    return f"${price:,.2f}" if any(s in symbol for s in ["BTC","ETH"]) else f"${price:,.4f}"


def build_signal_message(pipeline_result: dict, analysis: dict) -> str:
    direction   = pipeline_result["direction"]
    score       = pipeline_result["score"]
    symbol      = pipeline_result["symbol"]
    signals     = pipeline_result["signal_result"]
    side_result = signals[direction]
    regime_info = pipeline_result.get("regime", {})

    rsi     = analysis.get("rsi",          {})
    bb      = analysis.get("bollinger",     {})
    ema     = analysis.get(f"ema_{direction}", {})
    adx     = analysis.get("adx_15m",      {})
    funding = analysis.get("funding_rate",  {})
    ls      = analysis.get("ls_ratio",      {})
    oi      = analysis.get("oi_change",     {})
    taker   = analysis.get("taker_volume",  {})
    liq     = analysis.get("liquidations",  {})
    price   = analysis.get("current_price")
    cs      = side_result.get("component_scores", {})
    bonuses = side_result.get("bonuses", [])
    gate    = side_result.get("gate_info", {})
    regime_thr = side_result.get("regime_threshold", 60)

    # C/D-alt 패널티 값 (scoring_system 반환값에서 읽기)
    mtf_penalty    = side_result.get("mtf_penalty",    1.0)
    exhaustion_mult= side_result.get("exhaustion_mult", 1.0)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = []

    # ── 신호 등급 ──
    ema_info      = analysis.get(f"ema_{direction}", {})
    same_count    = ema_info.get("same_count",    0)
    reverse_count = ema_info.get("reverse_count", 0)

    if score >= 85:
        grade_icon  = "🔥🔥"; grade_label = "STRONG"; grade_desc = "매우 강한 신호 — 즉시 대응 권장"
    elif score >= 72:
        grade_icon  = "🔥";   grade_label = "GOOD";   grade_desc = "좋은 신호 — 표준 진입"
    else:
        grade_icon  = "📊";   grade_label = "WATCH";  grade_desc = "기준 통과 — 확인 후 진입"

    if same_count == 3:
        trend_align  = "✅ 순방향 3/3"; trend_detail = "3개 TF EMA 모두 신호 방향 일치"
    elif same_count == 2:
        trend_align  = "✅ 순방향 2/3"; trend_detail = "2개 TF EMA 신호 방향 일치"
    elif same_count == 1:
        trend_align  = "⚠️ 부분 역방향 2/3"; trend_detail = "상위 TF와 방향 불일치 — 주의"
    else:
        trend_align  = "⚠️ 역방향 3/3"; trend_detail = "모든 TF EMA가 반대 방향 — 역추세 진입"

    pullback_long  = rsi.get("pullback_long",  False)
    pullback_short = rsi.get("pullback_short", False)
    pullback_str   = ""
    if direction == "long"  and pullback_long:
        pullback_str = "  ★ 눌림목 롱 (1h RSI 강세 + 15m 과매도)"
    elif direction == "short" and pullback_short:
        pullback_str = "  ★ 눌림목 숏 (1h RSI 약세 + 15m 과매수)"

    # ── 헤더 ──
    dir_icon = "🟢" if direction=="long" else "🔴"
    dir_text = "롱(LONG)" if direction=="long" else "숏(SHORT)"
    lines.append(f"{dir_icon} <b>{dir_text} 진입 신호</b>  {grade_icon} <b>{grade_label}</b>")
    lines.append(f"<code>{'─'*32}</code>")
    lines.append(f"🪙 <b>{symbol}</b>   💰 <b>{_fmt_price(price, symbol)}</b>")
    lines.append(f"🎯 신뢰도: <b>{score:.1f}pt</b>  {_bar(score)}  (임계:{regime_thr}pt)")
    lines.append(f"📌 {grade_desc}")
    lines.append(f"📐 추세 정합: {trend_align}  <i>{trend_detail}</i>")
    if pullback_str:
        lines.append(f"<b>{pullback_str}</b>")
    lines.append(f"🕐 {now}")
    lines.append("")

    # H: 시장 국면
    r_icon = regime_info.get("icon","")
    r_name = regime_info.get("regime","?")
    r_desc = regime_info.get("description","")
    lines.append(f"📊 <b>시장 국면: {r_icon} {r_name}</b>")
    lines.append(f"   <i>{r_desc}</i>")
    lines.append("")

    # ── 기술 지표 ──
    lines.append("📈 <b>기술 지표</b>")

    rsi_val = rsi.get("value", 50.0)
    rsi_1h  = rsi.get("value_1h")
    rsi_4h  = rsi.get("value_4h")
    rsi_tag = ("⚡ 과매도" if rsi.get("state")=="oversold"
               else "⚡ 과매수" if rsi.get("state")=="overbought"
               else "— 중립")
    div_s = ""
    if rsi.get("bullish_divergence"): div_s = "  ✅강세다이버전스"
    if rsi.get("bearish_divergence"): div_s = "  ✅약세다이버전스"

    rsi_tf_parts = [f"15m:<code>{rsi_val:.0f}</code>"]
    if rsi_1h is not None: rsi_tf_parts.append(f"1h:<code>{rsi_1h:.0f}</code>")
    if rsi_4h is not None: rsi_tf_parts.append(f"4h:<code>{rsi_4h:.0f}</code>")
    lines.append(f"  RSI({config.RSI_PERIOD}) : {' / '.join(rsi_tf_parts)}  {rsi_tag}{div_s}")

    bb_map={"lower_breakout":"🔵 하단이탈","near_lower":"🔵 하단근접","lower_zone":"↘하단영역",
            "middle":"— 중앙","upper_zone":"↗상단영역","near_upper":"🔴 상단근접","upper_breakout":"🔴 상단이탈"}
    bb_tag=bb_map.get(bb.get("state",""),"—")
    sq_s="  🔊 스퀴즈" if bb.get("squeeze") else ""
    sk_s=""
    if bb.get("lower_streak",0)>=2: sk_s=f"  ⚠️하단이탈{bb['lower_streak']}캔들"
    if bb.get("upper_streak",0)>=2: sk_s=f"  ⚠️상단이탈{bb['upper_streak']}캔들"
    lines.append(f"  볼린저밴드: {bb_tag}  (%B:{bb.get('pct_b',0.5):.2f}){sq_s}{sk_s}")

    ema_mult=ema.get("multiplier",1.0); ema_tf=ema.get("tf_signals",{})
    ema_str=" | ".join(f"{tf}:{'↑' if s=='bullish' else ('↓' if s=='bearish' else '—')}" for tf,s in ema_tf.items())
    ema_rev=ema.get("reverse_count",0)
    ema_warn="" if ema_mult==1.0 else f"  ⚠️역방향{ema_rev}TF(×{ema_mult:.2f})"
    lines.append(f"  EMA교차  : [{ema_str}]{ema_warn}")

    adx_map={"strong":"🔥강한추세","normal":"📈추세중","weak":"〰약한추세","none":"💤횡보"}
    adx_m=adx.get("multiplier",1.0)
    adx_s=f"  ×{adx_m:.2f}배율적용" if adx_m<1.0 else ""
    lines.append(f"  ADX({config.ADX_PERIOD})  : <code>{adx.get('adx',0):.1f}</code>  {adx_map.get(adx.get('strength','none'),'—')}{adx_s}")
    lines.append("")

    # ── 시장 심리 ──
    lines.append("💡 <b>시장 심리</b>")

    fr_pct=funding.get("rate_pct",0.0) or 0.0; fr_bias=funding.get("bias","neutral")
    fr_icon="🟢" if ((direction=="long" and fr_bias=="long_favorable") or
                     (direction=="short" and fr_bias=="short_favorable")) else (
             "🔴" if fr_bias!="neutral" else "⚪")
    lines.append(f"  펀딩비   : {fr_icon} {fr_pct:+.4f}%  [{fr_bias}]" if funding.get("available") else "  펀딩비   : ⚪ N/A")

    if ls.get("available"):
        ls_icon="🟢" if ((direction=="long" and ls.get("bias") in ("long_favorable","long_extreme")) or
                         (direction=="short" and ls.get("bias") in ("short_favorable","short_extreme"))) else (
                 "🔴" if ls.get("bias")!="neutral" else "⚪")
        lines.append(f"  롱숏비율 : {ls_icon} 롱{ls.get('long_pct',0.5)*100:.1f}% / 숏{ls.get('short_pct',0.5)*100:.1f}%  [{ls.get('bias')}]")
    else:
        lines.append("  롱숏비율 : ⚪ N/A")

    if taker.get("available"):
        tk_icon="🟢" if ((direction=="long" and taker.get("bias")=="buy_dominant") or
                         (direction=="short" and taker.get("bias")=="sell_dominant")) else (
                 "🔴" if taker.get("bias")!="neutral" else "⚪")
        lines.append(f"  Taker    : {tk_icon} 매수{taker.get('buy_ratio',0.5)*100:.1f}% / 매도{taker.get('sell_ratio',0.5)*100:.1f}%  [{taker.get('bias')}]")
    else:
        lines.append("  Taker    : ⚪ N/A")

    if oi.get("available"):
        oi_map={"bullish_trend_confirm":"📈롱추세강화","bearish_trend_confirm":"📉숏추세강화",
                "short_covering":"↗숏커버링","long_liquidation":"↘롱청산","neutral":"— 중립"}
        lines.append(f"  OI변화   : {oi.get('change_pct',0):+.2f}%  {oi_map.get(oi.get('interpretation','neutral'),'—')}")

    if liq.get("available") and liq.get("signal","none") != "none":
        liq_icon = "💥" if liq.get("is_large") else "⚡"
        sig_map  = {"long_liq_detected":"롱청산 감지 → 반등 가능","short_liq_detected":"숏청산 감지 → 하락 가능"}
        liq_label= sig_map.get(liq.get("signal","none"), liq.get("signal","none"))
        lw = liq.get("long_liq_proxy",  0)
        sw = liq.get("short_liq_proxy", 0)
        lines.append(f"  청산감지  : {liq_icon} {liq_label}  (롱:{lw:.2f} / 숏:{sw:.2f})")
    lines.append("")

    # ── 지표별 점수 ──
    lines.append("📉 <b>지표별 점수</b>")
    label_map={"rsi":"RSI         ","bollinger":"볼린저밴드  ","funding_rate":"펀딩비      ",
               "long_short_ratio":"롱숏비율    ","taker_volume":"Taker비율   ",
               "oi_change":"OI변화율    ","volume":"거래량      "}
    for key, weight in config.SCORE_WEIGHTS.items():
        s=cs.get(key,0.0); contrib=s*weight
        lines.append(f"  {label_map.get(key,key)}: {_bar(s,8)}  <i>({contrib:.1f}pt)</i>")

    # 배율 표시 (EMA / ADX / 페널티 / C: MTF RSI / D-alt: 소진)
    ema_m  = side_result.get("ema_multiplier",  1.0)
    adx_m2 = side_result.get("adx_multiplier",  1.0)
    gate_p = gate.get("funding_penalty",         1.0)
    if ema_m   < 1.0: lines.append(f"  EMA역방향 배율  : ×{ema_m:.2f}")
    if adx_m2  < 1.0: lines.append(f"  ADX횡보 배율   : ×{adx_m2:.2f}")
    if gate_p  < 1.0: lines.append(f"  복합 페널티    : ×{gate_p:.2f}")
    if mtf_penalty     < 1.0:
        rsi_1h_v = rsi.get("value_1h") or 0
        rsi_4h_v = rsi.get("value_4h") or 0
        lines.append(f"  ⚠️ MTF RSI 과열 패널티 : ×{mtf_penalty:.2f}  (1h:{rsi_1h_v:.0f} 4h:{rsi_4h_v:.0f})")
    if exhaustion_mult < 1.0:
        rsi_1h_v = rsi.get("value_1h") or 0
        lines.append(f"  ⚠️ EXPLOSIVE 소진 패널티: ×{exhaustion_mult:.2f}  (1h RSI:{rsi_1h_v:.0f})")
    lines.append("")

    # ── 판단 근거 ──
    lines.append("🤖 <b>판단 근거</b>")
    reasons=[]
    st=rsi.get("state","neutral")
    if direction=="long"  and rsi.get("pullback_long"):
        reasons.append(f"★ 눌림목 롱 진입 — 1h RSI 강세({rsi.get('value_1h') or '-'})+15m 과매도({rsi.get('value'):.0f})")
    elif direction=="short" and rsi.get("pullback_short"):
        reasons.append(f"★ 눌림목 숏 진입 — 1h RSI 약세({rsi.get('value_1h') or '-'})+15m 과매수({rsi.get('value'):.0f})")
    ema_same   = ema.get("same_count", 0)
    taker_bias = taker.get("bias","neutral")
    oi_interp  = oi.get("interpretation","")
    vol_strong = analysis.get("volume",{}).get("strong", False)
    bb_state_n = bb.get("state","")
    if ema_same == 3:
        if (direction=="long"  and taker_bias=="buy_dominant" and "bullish" in oi_interp):
            reasons.append("★ 추세 지속 — EMA 3TF+OI+Taker 매수 일치 (피라미딩 적기)")
        elif (direction=="short" and taker_bias=="sell_dominant" and "bearish" in oi_interp):
            reasons.append("★ 추세 지속 — EMA 3TF+OI+Taker 매도 일치 (피라미딩 적기)")
        if vol_strong: reasons.append("추세 가속 — 거래량 급증 동반")
        if direction=="long"  and bb_state_n in ("upper_breakout","near_upper"):
            reasons.append("Band Walking — BB 상단 타고 상승 중")
        elif direction=="short" and bb_state_n in ("lower_breakout","near_lower"):
            reasons.append("Band Walking — BB 하단 타고 하락 중")
    if direction=="long"  and st=="oversold":    reasons.append("RSI 과매도 — 반등 구간")
    elif direction=="short" and st=="overbought": reasons.append("RSI 과매수 — 하락 구간")
    if direction=="long"  and bb_state_n in ("lower_breakout","near_lower"): reasons.append("볼린저 하단 — 반등 타이밍")
    if direction=="short" and bb_state_n in ("upper_breakout","near_upper"): reasons.append("볼린저 상단 — 하락 타이밍")
    if bb.get("squeeze"): reasons.append("볼린저 스퀴즈 — 큰 움직임 임박")
    if ema_same >= 2 and not rsi.get("pullback_long") and not rsi.get("pullback_short"):
        reasons.append(f"EMA {ema_same}/3TF {'상승' if direction=='long' else '하락'} 일치")
    if taker.get("available") and taker.get("strength")=="strong" and not any("Taker" in r for r in reasons):
        t_icon="강한 매수체결" if taker_bias=="buy_dominant" else "강한 매도체결"
        reasons.append(f"Taker {t_icon} ({taker.get('buy_ratio' if direction=='long' else 'sell_ratio',0)*100:.0f}%)")
    if liq.get("is_large"): reasons.append("대규모 청산 꼬리 감지 — 모멘텀 소진 반전 가능")
    for i,r in enumerate(reasons[:5],1): lines.append(f"  {i}. {r}")
    lines.append("")

    # ── 보너스 + 푸터 ──
    if bonuses:
        bt=side_result.get("bonus_total",0)
        lines.append(f"🎁 보너스 +{bt}pt: {' · '.join(f'{n}(+{v}pt)' for n,v in bonuses)}")
        lines.append("")
    if gate.get("penalty_reason"): lines.append(f"⚠️ <i>{gate['penalty_reason']}</i>"); lines.append("")

    lines.append(f"<code>{'─'*32}</code>")
    lines.append(f"⚙️ 임계값 {regime_thr}pt | 쿨다운 {config.SIGNAL_COOLDOWN_MINUTES}분 | OKX")
    lines.append("<i>⚠️ 참고용 신호입니다. 투자 결정은 본인 책임입니다.</i>")

    msg="\n".join(lines)
    return msg[:3980]+"\n\n<i>...(생략)</i>" if len(msg)>4000 else msg


def send_error_alert(error_msg: str, context: str="") -> None:
    now=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    send_message(f"🚨 <b>시스템 에러</b>\n<code>{'─'*32}</code>\n🕐 {now}\n📍 {context or '—'}\n\n<pre>{error_msg[:800]}</pre>")


def send_heartbeat(symbols: list, scan_count: int, signal_count: int) -> None:
    now=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    send_message(f"💚 <b>봇 정상 동작 중</b>\n<code>{'─'*32}</code>\n🕐 {now}\n"
                 f"🪙 {', '.join(symbols)}\n🔄 실행:{scan_count}회 | 🚨 신호:{signal_count}건")


def notify_signal(pipeline_result: dict, analysis: dict) -> bool:
    """
    Bug3 수정: current_price를 추출해 record_signal_sent에 전달 (A2 동적 쿨다운용)
    """
    from scoring_system import record_signal_sent
    if not pipeline_result.get("should_notify"): return False
    symbol        = pipeline_result["symbol"]
    direction     = pipeline_result["direction"]
    current_price = analysis.get("current_price") or 0.0   # A2: 동적 쿨다운용 가격
    logger.info(f"[Notify] {symbol} {direction.upper()} {pipeline_result['score']:.1f}pt — 발송")
    msg=build_signal_message(pipeline_result, analysis)
    result=send_message(msg)
    if result:
        record_signal_sent(symbol, direction, current_price)   # A2: 마지막 신호 가격 저장
        logger.info(f"[Notify] ✅ 발송 완료")
        return True
    logger.error(f"[Notify] ❌ 발송 실패")
    return False
