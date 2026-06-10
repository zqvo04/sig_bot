"""
ortho_engine.py — ORTHO-3 직교 3축 합의 엔진 (자립형) [TARGET: 15분봉]
════════════════════════════════════════════════════════════════════
레거시 만능 스코어러를 대체하는 유일한 의사결정 엔진.
점수를 더하지 않는다. 세 직교축이 모두 동의(AND)하고 어떤 거부권(VETO)도
없을 때만 신호. 모든 임계는 그 코인의 최근 자기 분포 백분위 — 매크로 무관.

  [축 L] 위치  : (close−SMA)/ATR 의 W_L 분포 백분위
  [축 F] 흐름  : CVD 프록시(캔들 모멘텀) 슬라이딩 백분위
  [축 S] 구조  : 다TF(15m/1h/4h) EMA 정렬 + 신선 돌파
  [VETO]       : 군중 과밀(LS) / Taker 역방향 / 호가 스프레드

폴라리티:
  REV  회귀형 : L=극단 ∧ F=반전 ∧ ¬S_broken → 평균 회귀
  CONT 연속형 : L=눌림 ∧ F=동조 ∧ S=정렬   → 추세 지속

반환: 가상 신호 dict 리스트 (ortho_notion/ortho_notify가 소비). 실주문 없음.
"""
import logging
from typing import List, Dict, Optional

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import ortho_config as oc
# ortho_data(ccxt 의존)는 실제 fetch가 필요한 evaluate()에서 지연 import —
# 순수 축 로직(axis_*/veto/barriers)은 ccxt 없이도 import·단위테스트 가능.

logger = logging.getLogger("ortho.engine")


# ════════════════════════════════════════════════════════════════════
# 0. 순수 지표 헬퍼 (무상태, numpy/pandas 불필요)
# ════════════════════════════════════════════════════════════════════
def _closes(candles): return [float(c[4]) for c in candles]

def sma(values, period):
    return sum(values[-period:]) / period if len(values) >= period else None

def atr(candles, period):
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = float(candles[i][2]), float(candles[i][3]), float(candles[i-1][4])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period if len(trs) >= period else None

def ema(values, period):
    if len(values) < period:
        return None
    k = 2.0 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e

def percentile_rank(x, dist):
    if not dist:
        return None
    below = sum(1 for d in dist if d < x)
    equal = sum(1 for d in dist if d == x)
    return (below + 0.5 * equal) / len(dist) * 100.0

def candle_momentum(candles_5m, window):
    """CVD 프록시: 캔들방향×body비율×거래량 가중. 0=완전매도, 1=완전매수."""
    recent = candles_5m[-window:]
    if len(recent) < max(2, window // 2):
        return None
    w_bull = w_bear = 0.0
    for i, c in enumerate(recent):
        o, h, l, cl, vol = float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])
        rng = h - l
        if rng <= 0 or vol <= 0:
            continue
        weight = (i + 1) * vol
        w_bull += max(0.0, (cl - o) / rng) * weight
        w_bear += max(0.0, (o - cl) / rng) * weight
    total = w_bull + w_bear
    return w_bull / total if total > 0 else None


# ════════════════════════════════════════════════════════════════════
# 1. 세 축
# ════════════════════════════════════════════════════════════════════
def axis_location(candles_15m) -> Optional[dict]:
    closes = _closes(candles_15m)
    a = atr(candles_15m, oc.N_ATR)
    if a is None or a <= 0 or len(closes) < oc.N_MEAN + oc.W_L:
        return None
    devs = []
    for t in range(len(closes) - oc.W_L, len(closes)):
        m = sma(closes[:t + 1], oc.N_MEAN)
        if m is not None:
            devs.append((closes[t] - m) / a)
    if len(devs) < oc.W_L // 2:
        return None
    L_pct = percentile_rank(devs[-1], devs)
    state = "EXT_LOW" if L_pct <= oc.P_EXT else ("EXT_HIGH" if L_pct >= 100 - oc.P_EXT else "NEUTRAL")
    return {"L_pct": round(L_pct, 1), "state": state, "mean": sma(closes, oc.N_MEAN), "atr": a}


def axis_flow(candles_5m) -> Optional[dict]:
    if len(candles_5m) < oc.W_F + 6:
        return None
    series = []
    span = min(24, len(candles_5m) - oc.W_F)
    for end in range(len(candles_5m) - span, len(candles_5m) + 1):
        m = candle_momentum(candles_5m[:end], oc.W_F)
        if m is not None:
            series.append(m)
    if len(series) < 6:
        return None
    F_pct = percentile_rank(series[-1], series)
    state = "FLOW_UP" if F_pct >= 100 - oc.P_FLOW else ("FLOW_DOWN" if F_pct <= oc.P_FLOW else "NEUTRAL")
    return {"F_pct": round(F_pct, 1), "state": state, "raw": round(series[-1], 3)}


def _ema_up(candles):
    closes = _closes(candles)
    ef, es = ema(closes, oc.EMA_FAST), ema(closes, oc.EMA_SLOW)
    return None if ef is None or es is None else ef > es


def axis_structure(candles_15m, candles_1h, candles_4h) -> dict:
    flags = []
    for c in (candles_15m, candles_1h, candles_4h):
        if c and len(c) >= oc.EMA_SLOW:
            u = _ema_up(c)
            if u is not None:
                flags.append(u)
    n = len(flags) if flags else 1
    up = sum(1 for f in flags if f)
    closes = _closes(candles_15m)
    window = closes[-oc.W_L:] if len(closes) >= oc.W_L else closes
    cur = closes[-1]
    new_low, new_high = cur <= min(window), cur >= max(window)
    return {
        "ema_up_count": up, "ema_tf_n": n,
        "aligned_up":   up >= max(2, n - 1),
        "aligned_down": (up <= min(1, n - 2)) if n >= 3 else (up == 0),
        "broken_long":  new_low and up == 0,
        "broken_short": new_high and up == n,
    }


# ════════════════════════════════════════════════════════════════════
# 2. 맥락 거부권 (차단 전용)
# ════════════════════════════════════════════════════════════════════
def context_veto(direction, context, spread_bps) -> Optional[str]:
    d = direction.lower()
    ls = (context or {}).get("ls_ratio") or {}
    if ls.get("available"):
        crowd = ls.get("long_pct", 0.5) if d == "long" else ls.get("short_pct", 0.5)
        if 0.0 < crowd < 1.0 and crowd >= oc.LS_CROWD_VETO:
            return f"crowd({crowd:.2f})"
    tk = (context or {}).get("taker") or {}
    if tk.get("available"):
        against = tk.get("sell_ratio", 0.5) if d == "long" else tk.get("buy_ratio", 0.5)
        if against >= oc.TAKER_VETO:
            return f"taker({against:.2f})"
    if spread_bps is not None and spread_bps > oc.SPREAD_MAX_BPS:
        return f"spread({spread_bps:.1f}bps)"
    return None


# ════════════════════════════════════════════════════════════════════
# 3. 구조 기반 배리어 (TP/SL — 구조가 결정, 최적화 금지)
# ════════════════════════════════════════════════════════════════════
def _round_price(p, ref):
    digits = 2 if ref >= 100 else (4 if ref >= 1 else 6)
    return round(p, digits)


def build_barriers(polarity, direction, entry, candles_15m, loc) -> Optional[dict]:
    a = loc["atr"]; buf = oc.SL_ATR_BUF * a
    lows  = [float(c[3]) for c in candles_15m[-oc.W_F:]]
    highs = [float(c[2]) for c in candles_15m[-oc.W_F:]]
    sw_low  = [float(c[3]) for c in candles_15m[-oc.W_L:]]
    sw_high = [float(c[2]) for c in candles_15m[-oc.W_L:]]
    d = direction.lower()
    if d == "long":
        sl = min(lows) - buf
        tp = loc["mean"] if polarity == "REV" else max(sw_high)
    else:
        sl = max(highs) + buf
        tp = loc["mean"] if polarity == "REV" else min(sw_low)
    if tp is None:
        return None
    sl_dist = abs(entry - sl); tp_dist = abs(tp - entry)
    if sl_dist <= 0:
        return None
    if d == "long" and tp <= entry:  return None
    if d == "short" and tp >= entry: return None
    rr = tp_dist / sl_dist
    if rr < oc.RR_MIN:
        return None
    return {"sl": _round_price(sl, entry), "tp": _round_price(tp, entry),
            "sl_dist": round(sl_dist, 8), "rr": round(rr, 2), "bars_limit": oc.T_MAX}


# ════════════════════════════════════════════════════════════════════
# 4. 폴라리티별 진리표
# ════════════════════════════════════════════════════════════════════
def _decide_direction(polarity, loc, flow, struct) -> Optional[str]:
    L, F, Lpct = loc["state"], flow["state"], loc["L_pct"]
    lo, mid, hi = oc.cont_pullback_band()
    if polarity == "REV":
        if L == "EXT_LOW" and F == "FLOW_UP" and not struct["broken_long"]:
            return "long"
        if L == "EXT_HIGH" and F == "FLOW_DOWN" and not struct["broken_short"]:
            return "short"
        return None
    if (lo <= Lpct < mid) and F == "FLOW_UP" and struct["aligned_up"]:
        return "long"
    if (mid < Lpct <= hi) and F == "FLOW_DOWN" and struct["aligned_down"]:
        return "short"
    return None


def macro_tag(candles_4h) -> str:
    u = _ema_up(candles_4h) if candles_4h and len(candles_4h) >= oc.EMA_SLOW else None
    return "FLAT" if u is None else ("UPLEG" if u else "DOWNLEG")


# ════════════════════════════════════════════════════════════════════
# 5. 진입점: 한 심볼 평가 → 가상 신호 리스트 (0~2건)
# ════════════════════════════════════════════════════════════════════
def evaluate(exchange, symbol: str, context: dict) -> List[Dict]:
    import ortho_data as od          # 지연 import (ccxt 의존)
    c15 = od.fetch_candles(exchange, symbol, oc.TF_ENTRY, oc.N_15M_FETCH)
    c5  = od.fetch_candles(exchange, symbol, oc.TF_FLOW,  oc.N_5M_FETCH)
    if len(c15) < oc.N_MEAN + oc.W_L or len(c5) < oc.W_F + 6:
        logger.info(f"[engine] {symbol} 캔들 부족 — 스킵")
        return []
    c1h = od.fetch_candles(exchange, symbol, oc.TF_MID,   oc.N_HTF_FETCH)
    c4h = od.fetch_candles(exchange, symbol, oc.TF_MACRO, oc.N_HTF_FETCH)

    entry = float(c15[-1][4])
    loc, flow = axis_location(c15), axis_flow(c5)
    if loc is None or flow is None:
        return []
    struct = axis_structure(c15, c1h, c4h)
    mtag = macro_tag(c4h)

    spread = None
    out: List[Dict] = []
    for polarity in oc.POLARITIES:
        direction = _decide_direction(polarity, loc, flow, struct)
        if direction is None:
            continue
        if spread is None:
            spread = od.fetch_spread_bps(exchange, symbol)
        veto = context_veto(direction, context, spread)
        if veto:
            logger.info(f"[engine] {symbol} {polarity} {direction} VETO:{veto}")
            continue
        b = build_barriers(polarity, direction, entry, c15, loc)
        if b is None:
            logger.info(f"[engine] {symbol} {polarity} {direction} RR<{oc.RR_MIN} 스킵")
            continue
        out.append({
            "symbol": symbol, "polarity": polarity, "direction": direction,
            "entry": _round_price(entry, entry), "tp": b["tp"], "sl": b["sl"],
            "r_dist": b["sl_dist"], "rr": b["rr"], "bars_limit": b["bars_limit"],
            "l_pct": loc["L_pct"], "f_pct": flow["F_pct"],
            "s_state": f"up{struct['ema_up_count']}/{struct['ema_tf_n']}",
            "macro_tag": mtag,
            "reason": (f"{polarity} {direction.upper()} | L={loc['state']}({loc['L_pct']}) "
                       f"F={flow['state']}({flow['F_pct']}) "
                       f"S=up{struct['ema_up_count']}/{struct['ema_tf_n']} RR={b['rr']}"),
        })
        logger.info(f"[engine] 🟦 {out[-1]['reason']}")
    return out
