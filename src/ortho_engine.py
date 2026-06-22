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

폴라리티 (R1 레짐 라우터가 국면별로 택일; 라우터 OFF면 POLARITIES 환경변수):
  REV   회귀형 : L=극단 ∧ F=반전 ∧ ¬S_broken → 평균 회귀   (RANGE 국면)
  CONT  연속형 : L=눌림 ∧ F=동조 ∧ S=정렬 ∧ ¬추격 → 추세 지속 (TREND 국면)
  BREAKOUT 돌파형: VWAP 신선 재탈환 ∧ 거래량 서지 ∧ F=동조 → 확장 추종 (EXPANSION 국면)

반환: 가상 신호 dict 리스트 (ortho_notion/ortho_notify가 소비). 실주문 없음.
"""
import logging
import math
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
# 1-R. 레짐 라우터 (R1 — 국면 전문가만 켠다, 신규 fetch 0)  ★ 기본 ON
# ────────────────────────────────────────────────────────────────────
#   현 시스템 최대 누수: 모든 셋업을 모든 국면에서 대칭 난사(추세장 역행 −41R).
#   각 폴라리티를 "맞는 국면"에서만 허용 → 카운터트렌드/혼탁 진입을 구조 차단.
#   판정은 전부 그 코인 자기분포 백분위(절대숫자 금지) + 롱숏 대칭 유지.
#     RANGE     (저효율·저변동)        → REV  만 (평균회귀; S1 BB+RSI 대응)
#     TREND     (고효율 방향성 or 만장일치) → CONT 만 (추세동행; S3 정배열 대응)
#     EXPANSION (고변동·저효율=혼탁확장)  → BREAKOUT 만 (거래량 VWAP 돌파; S2/R4 대응)
#   기존 _decide_direction이 폴라리티 안에서 롱/숏을 거울 대칭으로 결정하므로
#   라우터는 "어떤 폴라리티를 평가할지"만 좁힌다(방향 대칭 불변).
#
#   ── 정교화(2축 국면 판정) ───────────────────────────────────────────
#   다TF EMA `up` 카운트만으로는 부족하다: 3TF에서 up∈{1,2}(부분정렬)가 가장 길고,
#   "추세 초입(15m·1h 정렬, 4h 지연)"과 "혼탁 레인지"를 구분하지 못한다(4h EMA21은
#   ~3.5일 지연 → 전 TF 만장일치는 너무 늦다). 두 자기정규화 축을 교차한다:
#     ① 추세효율(Kaufman ER 레벨, 0~1 스케일프리) — RR류처럼 백분위化 없이 레벨 임계
#     ② 변동성(정규화 변동폭 백분위) — 코인·가격대 무관 자기정규화
#   → 고효율+다수TF동조 = TREND(조기 포착) / 고변동+저효율 = EXPANSION / 나머지 = RANGE.
#   ER은 방향 무관 강도(|순변화|/Σ|봉변화|)이고 방향은 진리표가 결정 → 롱숏 대칭 불변.
# ════════════════════════════════════════════════════════════════════
def _norm_range_series(candles_15m) -> list:
    """봉별 정규화 변동폭 (high−low)/close — 코인·가격대 무관 자기정규화."""
    out = []
    for c in candles_15m:
        h, l, cl = float(c[2]), float(c[3]), float(c[4])
        if cl > 0:
            out.append((h - l) / cl)
    return out


def regime_vol_pct(candles_15m) -> Optional[float]:
    """변동성 상태 = 최근 W_F봉 평균변동폭의 W_L 분포 내 백분위(0=수축, 100=확장)."""
    vals = _norm_range_series(candles_15m)
    if len(vals) < oc.W_L:
        return None
    means = [sum(vals[end - oc.W_F:end]) / oc.W_F
             for end in range(oc.W_F, len(vals) + 1)]
    means = means[-oc.W_L:]
    return percentile_rank(means[-1], means)


def efficiency_ratio(closes, window) -> Optional[float]:
    """Kaufman 효율성 비율 = |순변화| / Σ|봉간변화|. 0=완전노이즈(레인지), 1=완전추세.
    방향 무관 강도(부호 없음) — 방향은 진리표가 결정하므로 롱숏 대칭 불변.
    ※ ER은 [0,1]로 정규화된 스케일프리 비율(코인·가격대 무관 동일 의미; RR_MIN/RR_MAX 류)이라
       백분위화하지 않고 '레벨'을 직접 임계한다. (백분위화하면 지속추세=항상高ER→백분위 50으로 붕괴)."""
    if len(closes) < window + 1:
        return None
    seg = closes[-(window + 1):]
    net = abs(seg[-1] - seg[0])
    noise = sum(abs(seg[i] - seg[i - 1]) for i in range(1, len(seg)))
    return (net / noise) if noise > 0 else None


def classify_regime(candles_15m, struct) -> str:
    """2축(추세효율 × 변동성) 국면 판정. 우선순위: TREND > EXPANSION > RANGE.

      TREND     : 전 TF 만장일치(up==n|0)  OR  (ER 레벨 ≥ TREND_ER ∧ 다수TF가 순변화 방향과 일치)
      EXPANSION : (TREND 아님) ∧ 고변동(vol≥VOL_HI) ∧ 저효율 = 방향 없이 크게 흔들리는 혼탁확장
      RANGE     : 그 외(저효율·저변동) — 평균회귀 토양

    주의: aligned_up/down은 3TF에서 항상 한쪽이 참이라 레짐 분리 불가. 만장일치(up==n|0)는
    4h EMA21(~3.5일) 지연으로 너무 늦다 → ER 레벨로 '초입 추세'(up∈{1,2})를 조기 승격하되,
    EMA 다수 방향과 순변화 부호가 일치할 때만(역추세 반등을 추세로 오인하지 않게) 승격한다.
    랜덤워크 ER≈1/√window(≈0.22@20), 깨끗한 추세 ER→1 → TREND_ER≈0.4가 둘을 분리.
    """
    n, up = struct["ema_tf_n"], struct["ema_up_count"]
    if up == n or up == 0:                       # 전 타임프레임 동일방향 = 강추세
        return "TREND"
    closes = _closes(candles_15m)
    er = efficiency_ratio(closes, oc.N_MEAN)     # 스케일프리 레벨(0~1) — 백분위화 금지
    if er is not None and er >= oc.TREND_ER and len(closes) > oc.N_MEAN:
        net_up = closes[-1] >= closes[-1 - oc.N_MEAN]
        # 3TF: up∈{1,2} 중 up>=n-1(=2)=상승 다수, up<=1=하락 다수. 순변화 부호와 일치 시만 승격.
        if net_up and up >= n - 1:
            return "TREND"
        if (not net_up) and up <= 1:
            return "TREND"
    vol = regime_vol_pct(candles_15m)
    if vol is not None and vol >= oc.VOL_HI:      # 고변동 + (위에서 걸러진) 저효율 = 혼탁확장
        return "EXPANSION"
    return "RANGE"


def regime_polarities(regime: str) -> tuple:
    """국면 → 허용 폴라리티(롱숏은 폴라리티 내부에서 대칭 결정)."""
    return {
        "RANGE":     ("REV",),
        "TREND":     ("CONT",),
        "EXPANSION": ("BREAKOUT",),   # R4 — 거래량 동반 VWAP 돌파 전문
    }.get(regime, ())


def routed_polarities(regime: str, candles_15m) -> tuple:
    """L2 라우터 모드 적용. STRICT=레짐당 1폴라리티(현행). SOFT=ER 모호구간만 양폴라리티.

    SOFT 근거: RANGE↔TREND 경계는 ER≈TREND_ER에서 갈리는데, 코인이 이 값 근처로 진동하면
    적격 폴라리티가 봉마다 깜빡여 "경계 반대편" 셋업(추세초입 눌림/레인지끝 반전)을 놓친다.
    |ER−TREND_ER|≤ROUTER_SOFT_ER 인 모호구간에서만 (REV,CONT) 둘 다 평가하고, 최종 판정은
    기존 AND축·VETO·배리어가 한다(난사 아님). 명확한 추세/레인지/EXPANSION은 STRICT와 동일.
    ER은 방향무관 강도라 롱숏 대칭 불변.
    """
    base = regime_polarities(regime)
    if oc.ROUTER_MODE != "SOFT" or regime not in ("RANGE", "TREND"):
        return base
    er = efficiency_ratio(_closes(candles_15m), oc.N_MEAN)
    if er is not None and abs(er - oc.TREND_ER) <= oc.ROUTER_SOFT_ER:
        return ("REV", "CONT")        # 모호구간 — 양폴라리티 (중복 제거는 평가 루프가 처리)
    return base


# ════════════════════════════════════════════════════════════════════
# 1-B. BREAKOUT 셋업 (R4 — S2: VWAP 재탈환 + 거래량 서지, 신규 fetch 0)
# ────────────────────────────────────────────────────────────────────
#   EXPANSION 국면에서만 가동. 약한 구조의 저확신 확장 진입(데이터 up1/3 −21R)을
#   "거래량 동반 VWAP 돌파"로 대체. 모든 컷은 자기정규화(VWAP·거래량 백분위) + 롱숏 대칭.
#     롱: 직전 종가<VWAP ≤ 현재 종가(신선 재탈환) ∧ 거래량서지 ∧ F=상승 ∧ L≠과열고
#     숏: 거울쌍
#   거래량 "20SMA·150%" 같은 절대비율 대신 그 코인 자기분포 백분위(P_VOL)로 치환 → 과적합 방지.
# ════════════════════════════════════════════════════════════════════
def rolling_vwap(candles_15m, window) -> Optional[float]:
    """롤링 VWAP(전형가격 Σtp·v/Σv) — 일중 자기정규화 앵커. window=W_L 재사용(신규 파라미터 0)."""
    rows = candles_15m[-window:]
    num = den = 0.0
    for c in rows:
        h, l, cl, v = float(c[2]), float(c[3]), float(c[4]), float(c[5])
        tp = (h + l + cl) / 3.0
        num += tp * v; den += v
    return num / den if den > 0 else None


def vol_surge_pct(candles_15m, window) -> Optional[float]:
    """현재 봉 거래량의 최근 window 분포 내 백분위(0~100). 절대 150% 대신 자기정규화."""
    vols = [float(c[5]) for c in candles_15m[-window:]]
    if len(vols) < max(2, window // 2):
        return None
    return percentile_rank(vols[-1], vols)


def _range_break(candles_15m, cur) -> Optional[str]:
    """L3 신선 W_F 신고/신저 레인지 돌파. 직전 W_F봉 고저를 현재 종가가 갱신하면 방향 반환.
    18h VWAP 앵커로는 '신선 재탈환'이 거의 안 켜져 EXPANSION 신호가 ~0 → 고전 레인지 돌파를 OR로 보강."""
    if not oc.BREAKOUT_RANGE or len(candles_15m) < oc.W_F + 1:
        return None
    prior = candles_15m[-(oc.W_F + 1):-1]       # 현재봉 직전 W_F봉
    hi = max(float(c[2]) for c in prior)
    lo = min(float(c[3]) for c in prior)
    if cur > hi:
        return "long"
    if cur < lo:
        return "short"
    return None


def _decide_breakout(candles_15m, loc, flow) -> Optional[str]:
    surge = vol_surge_pct(candles_15m, oc.W_L)
    if surge is None or surge < oc.P_VOL:      # 거래량 동반 없으면 돌파 무시
        return None
    closes = _closes(candles_15m)
    if len(closes) < 2:
        return None
    prev, cur = closes[-2], closes[-1]
    F, L = flow["state"], loc["state"]
    vwap = loc.get("vwap")
    # 트리거 ①: VWAP 신선 재탈환(기존). 트리거 ②(L3): 신선 W_F 레인지 돌파. 둘은 OR.
    vwap_long  = vwap is not None and prev < vwap <= cur
    vwap_short = vwap is not None and prev > vwap >= cur
    rng = _range_break(candles_15m, cur)
    if (vwap_long or rng == "long") and F == "FLOW_UP" and L != "EXT_HIGH":
        return "long"
    if (vwap_short or rng == "short") and F == "FLOW_DOWN" and L != "EXT_LOW":
        return "short"
    return None


# 1-C. 추격 방지 (R5 — S3: 정배열 초입만, 연장 추세 진입 차단)
#   데이터 up3/3 −0.21R = 성숙·연장 추세 진입. 진입가와 빠른 EMA의 이격을 ATR로 제한해
#   "추세 초입(EMA 근처)"만 허용. 0.5% 같은 절대% 대신 CHASE_K·ATR(자기정규화) + 롱숏 대칭.
def _within_chase(candles_15m, entry, atr) -> bool:
    if not oc.CHASE_K or oc.CHASE_K <= 0:
        return True
    ef = ema(_closes(candles_15m), oc.EMA_FAST)
    if ef is None or atr <= 0:
        return True
    return abs(entry - ef) <= oc.CHASE_K * atr


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
    # BREAKOUT(R4): 무효화=VWAP 재이탈 → SL=VWAP±buf(정적, triple-barrier 정합). TP=직전 스윙.
    vwap = loc.get("vwap")
    if d == "long":
        sl = (vwap - buf) if (polarity == "BREAKOUT" and vwap) else (min(lows) - buf)
        tp = loc["mean"] if polarity == "REV" else max(sw_high)
    else:
        sl = (vwap + buf) if (polarity == "BREAKOUT" and vwap) else (max(highs) + buf)
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
    # A-4: 타임스톱(T_MAX봉≈2h) 안에 닿지 못할 먼 목표(RR 과대)를 RR_MAX로 당겨 TP·청산을 정합.
    #      SL=구조 그대로(리스크 불변), TP만 도달가능 거리로 축소. 진입 가부는 불변(RR_MAX≥RR_MIN).
    #      RR은 스케일프리 비율 → 특정 가격/변동성에 곡선맞춤하지 않음(과적합 표면 아님).
    if oc.RR_MAX and oc.RR_MAX >= oc.RR_MIN and rr > oc.RR_MAX:
        tp_dist = oc.RR_MAX * sl_dist
        rr = oc.RR_MAX
    # R2 도달가능 TP: 명목 RR≠실현 R(데이터 캡처효율 52%·타임스톱 32%·RR≥3 손실)의 본체.
    #     "타임스톱 내 못 닿는 TP는 가짜 목표." TP거리를 ATR·√T_MAX(확산 스케일)로 상한.
    #     SL=구조 그대로(리스크 불변). TP_REACH_K=0이면 비활성(현 동작 보존, A/B용).
    #     ATR 자기정규화 → 특정 가격/코인에 곡선맞춤 아님. 롱·숏 동일식(대칭).
    #     축소 후 RR<RR_MIN이면 "현실적 목표가 손익비 미달" → 선별 스킵.
    if oc.TP_REACH_K and oc.TP_REACH_K > 0:
        reach = oc.TP_REACH_K * a * math.sqrt(oc.T_MAX)
        if tp_dist > reach:
            tp_dist = reach
            rr = tp_dist / sl_dist
            if rr < oc.RR_MIN:
                return None
    tp = (entry + tp_dist) if d == "long" else (entry - tp_dist)
    return {"sl": _round_price(sl, entry), "tp": _round_price(tp, entry),
            "sl_dist": round(sl_dist, 8), "rr": round(rr, 2), "bars_limit": oc.T_MAX}


# ════════════════════════════════════════════════════════════════════
# 4. 폴라리티별 진리표
# ════════════════════════════════════════════════════════════════════
def _flow_up_ok(flow, context) -> bool:
    """흐름 상승 동조. 기본=캔들프록시 F==FLOW_UP. L4②: taker CVD 매수우위면 OR-확인
    (단 캔들 F가 명백히 하락(FLOW_DOWN)일 땐 무효 — taker가 명백한 역흐름을 덮어쓰지 않음)."""
    if flow["state"] == "FLOW_UP":
        return True
    if oc.FLOW_TAKER_CONFIRM and flow["state"] != "FLOW_DOWN":
        tk = (context or {}).get("taker") or {}
        if tk.get("available") and tk.get("buy_ratio", 0.5) >= oc.FLOW_TAKER_MIN:
            return True
    return False


def _flow_down_ok(flow, context) -> bool:
    """_flow_up_ok 의 거울쌍(롱숏 대칭)."""
    if flow["state"] == "FLOW_DOWN":
        return True
    if oc.FLOW_TAKER_CONFIRM and flow["state"] != "FLOW_UP":
        tk = (context or {}).get("taker") or {}
        if tk.get("available") and tk.get("sell_ratio", 0.5) >= oc.FLOW_TAKER_MIN:
            return True
    return False


def _decide_direction(polarity, loc, flow, struct, context=None) -> Optional[str]:
    L, Lpct = loc["state"], loc["L_pct"]
    f_up, f_dn = _flow_up_ok(flow, context), _flow_down_ok(flow, context)
    lo, mid, hi = oc.cont_pullback_band()
    if polarity == "REV":
        if L == "EXT_LOW" and f_up and not struct["broken_long"]:
            return "long"
        if L == "EXT_HIGH" and f_dn and not struct["broken_short"]:
            return "short"
        return None
    if (lo <= Lpct < mid) and f_up and struct["aligned_up"]:
        return "long"
    if (mid < Lpct <= hi) and f_dn and struct["aligned_down"]:
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
    loc["vwap"] = rolling_vwap(c15, oc.W_L)     # R4 — BREAKOUT 트리거/SL 앵커
    struct = axis_structure(c15, c1h, c4h)
    mtag = macro_tag(c4h)

    # R1 레짐 라우터: 켜져 있으면 라우터가 폴라리티를 결정(REV/CONT/BREAKOUT). 방향 대칭 불변.
    #   라우터 ON 시 라우터가 권위(POLARITIES 환경변수 대체) → EXPANSION→BREAKOUT 평가 가능.
    if oc.REGIME_ROUTER:
        regime = classify_regime(c15, struct)
        polarities = routed_polarities(regime, c15)   # L2 STRICT/SOFT
        if not polarities:
            logger.info(f"[engine] {symbol} 레짐={regime} → 허용 폴라리티 없음, 스킵")
            return []
    else:
        regime = None
        polarities = oc.POLARITIES

    spread = None
    out: List[Dict] = []
    for polarity in polarities:
        if polarity == "BREAKOUT":
            direction = _decide_breakout(c15, loc, flow)
        else:
            direction = _decide_direction(polarity, loc, flow, struct, context)
        if direction is None:
            continue
        # R5 추격 방지: CONT(추세동행)는 빠른 EMA 근처(초입)에서만. 연장 추세 진입 차단.
        if polarity == "CONT" and not _within_chase(c15, entry, loc["atr"]):
            logger.info(f"[engine] {symbol} CONT {direction} 추격(>CHASE_K·ATR) 스킵")
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
        # C-1 등가-R 사이징: SL 거리(=1R)로 수량을 역산 → 모든 신호가 동일 금액(RISK_PER_TRADE) 위험.
        #     변동성 큰 코인=작은 수량, 작은 코인=큰 수량 → PnL%가 아니라 R로 자동 정규화.
        rdist = b["sl_dist"]
        risk_pct = round(rdist / entry * 100.0, 3) if entry else None      # 1R 크기(진입가 대비 %)
        size     = round(oc.RISK_PER_TRADE / rdist, 6) if rdist > 0 else None
        notional = round(size * entry, 2) if size else None
        out.append({
            "symbol": symbol, "polarity": polarity, "direction": direction,
            "entry": _round_price(entry, entry), "tp": b["tp"], "sl": b["sl"],
            "r_dist": b["sl_dist"], "rr": b["rr"], "bars_limit": b["bars_limit"],
            "risk_quote": oc.RISK_PER_TRADE, "risk_pct": risk_pct,
            "size": size, "notional": notional,
            "l_pct": loc["L_pct"], "f_pct": flow["F_pct"],
            "s_state": f"up{struct['ema_up_count']}/{struct['ema_tf_n']}",
            "macro_tag": mtag,
            "regime": regime or "OFF",     # R7 레짐 코호트 기록
            "reason": (f"{polarity} {direction.upper()} | L={loc['state']}({loc['L_pct']}) "
                       f"F={flow['state']}({flow['F_pct']}) "
                       f"S=up{struct['ema_up_count']}/{struct['ema_tf_n']} "
                       f"RG={regime or 'OFF'} RR={b['rr']}"),
        })
        logger.info(f"[engine] 🟦 {out[-1]['reason']}")
    return out
