"""H1 (track T3) — 1H trend pullback. Strategy for research/runner.py (catalog 02 §4 H1, §5.1, §5.4, §5.5).

Core (long; short = every sign mirrored), decided at the CLOSE of 1H bar t, entry at the open of bar t+1:
  S  last CLOSED 4H bar: EMA9 > EMA21
  L  dev = (close - SMA20) / ATR14 (1H); L_pct = mid-rank percentile of dev in the last 72 bars; L_pct[t-1] <= P_pull
  T  close[t] > high[t-1]
  Exit X0 via runner.x0_barrier_fn("1h"): SL 1.5 ATR14(1H, Wilder), TP 1.5R, 16 bars, no BE.

params (all defaults = core; DEFAULTS):
  P_pull               core, grid GRIDS["P_pull"]
  P1 .. P8             candidate parts in the catalog's FIXED order; None/False = OFF, a grid value = ON at that value
  loc ma vol ref trigger trend   E6 calculation variants (first option = default)
  exit                 E7 exit variant ("X0" default)

Assumptions (catalog wording that needed a reading; stated, not tuned):
  * Percentiles are mid-rank (ortho_engine.percentile_rank) over a trailing window that includes the current value,
    computed only when >= half the window is available (ORTHO axis_location rule).
  * dev uses the ATR of each bar (series), not the latest ATR for the whole window as ORTHO's live code does.
  * E6 loc "z" = level of (close - anchor)/SD20 against the normal quantile of P_pull (inv_cdf(P_pull/100)), so the
    core parameter keeps one meaning. loc "bb_pctb" = percentile of %b; %b is monotone in z, so this equals the
    percentile of the SD-normalised deviation (differs from option 1 only by SD20 vs ATR14 scale).
  * E6 ref "vwap" replaces the anchor SMA20 with rolling VWAP(20) (ORTHO rolling_vwap); `ma` changes SMA20 -> EMA20.
  * E6 vol changes only the location scale; X0 barriers always use Wilder ATR14 (exit is E7's job).
  * E6 trigger "flow_turn": 15m flow percentile (P2's F_pct) crosses 50 between the previous and the current 1H close.
  * P2/P5 flow = ortho_engine.candle_momentum on 15m (W_F=6), percentile over the last 25 values (ORTHO axis_flow span).
  * P3 vol regime = mean normalised range (H-L)/C over 6 1H bars, percentile over 72 (ORTHO regime_vol_pct on 1H).
  * P7 uses the entry price (= open of t+1), known at the entry instant. On 24/7 perps open[t+1] ~= close[t],
    so P7 is expected to bind rarely.
  * P8 age = consecutive closed-4H bars in the current EMA9/21 state, percentile over 72 4H bars. The catalog gives
    NO grid for P8: GRIDS["P8"] is a PROPOSAL (marked) and needs user sign-off before any data is scored with it.
  * E7: X0, X1, X4 implemented. X2 (chandelier trail) is NOT: evaluate_outcome cannot trail and the runner forbids
    re-implementing scoring. X1 "no TP" = TP at 1000R (unreachable). X3 is T4-only per catalog.
Scale-free only: ATR/SD multiples, percentiles, normalised ranges, bar counts.
"""
import bisect, math, os, sys
from statistics import NormalDist

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as h  # noqa: E402  (also puts src/ on sys.path)
import runner as R  # noqa: E402
from ortho_engine import candle_momentum, efficiency_ratio, percentile_rank  # noqa: E402

H1, H4, D1, M15 = 3_600_000, 14_400_000, 86_400_000, 900_000
W_L, N_MEAN, N_ATR, W_F, FLOW_SPAN, ER_N, ER_W, T_MAX = 72, 20, 14, 6, 25, 20, 500, 16

# pre-registered grids, centre = adopted value (master §5.3). cost = parameter-budget units (catalog §5.1, D6).
GRIDS = {
    "P_pull": dict(grid=[10, 15, 20, 25, 30], center=20, cost=1),
    "P1": dict(grid=[True], center=True, cost=0),
    "P2": dict(grid=[20, 30, 40], center=30, cost=1),
    "P3": dict(grid=[80, 90, 95], center=90, cost=1),
    "P4": dict(grid=[40, 50, 60], center=50, cost=1),
    "P5": dict(grid=[10, 15, 20], center=15, cost=1),
    "P6": dict(grid=[50, 70, 85], center=70, cost=1),
    "P7": dict(grid=[0.25, 0.5, 1.0], center=0.5, cost=1),
    "P8": dict(grid=[30, 50, 70], center=50, cost=1, proposal=True),  # NOT in catalog - needs sign-off
}
PARTS = ["P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8"]  # catalog fixed order
E6 = {"loc": ["atr_pct", "z", "bb_pctb"], "ma": ["sma", "ema"], "vol": ["wilder", "tr_mean", "parkinson"],
      "ref": ["close", "vwap"], "trigger": ["prev_high", "prev_close", "flow_turn"],
      "trend": ["ema", "sma50", "donchian_mid"]}
E7 = ["X0", "X1", "X4"]
DEFAULTS = dict(P_pull=20, **{p: None for p in PARTS}, **{k: v[0] for k, v in E6.items()}, exit="X0")


# ── series helpers (index-aligned, None in warm-up, value i uses bars <= i only) ─────────────────────
def _sma(v, n):
    out, s = [None] * len(v), 0.0
    for i, x in enumerate(v):
        s += x
        if i >= n:
            s -= v[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def _ema(v, n):  # seeded with the SMA of the first n values (ortho_engine.ema)
    out, k = [None] * len(v), 2.0 / (n + 1)
    if len(v) >= n:
        e = out[n - 1] = sum(v[:n]) / n
        for i in range(n, len(v)):
            e = out[i] = v[i] * k + e * (1 - k)
    return out


def _sd(v, n):
    m = _sma(v, n)
    return [None if m[i] is None else math.sqrt(sum((x - m[i]) ** 2 for x in v[i - n + 1:i + 1]) / n)
            for i in range(len(v))]


def _pct(v, w):
    out = [None] * len(v)
    for i, x in enumerate(v):
        if x is not None:
            win = [y for y in v[max(0, i - w + 1):i + 1] if y is not None]
            if len(win) >= w // 2:
                out[i] = percentile_rank(x, win)
    return out


def _sign(a, b):
    return None if a is None or b is None else (a > b) - (a < b)


def _closed(ts, T, tf_ms):
    """index of the last bar of `ts` (open times) that is CLOSED at time T, or -1."""
    return bisect.bisect_right(ts, T - tf_ms) - 1


def _vol_scale(C, kind):
    if kind == "wilder":
        return h.wilder_atr(C, N_ATR)
    if kind == "tr_mean":
        tr = [None] + [max(C[i][2] - C[i][3], abs(C[i][2] - C[i - 1][4]), abs(C[i][3] - C[i - 1][4]))
                       for i in range(1, len(C))]
        return [None] + _sma(tr[1:], N_ATR)
    if kind == "parkinson":  # sigma(20) of log range, in price units
        lr = [math.log(c[2] / c[3]) ** 2 for c in C]
        m = _sma(lr, 20)
        return [None if m[i] is None else math.sqrt(m[i] / (4 * math.log(2))) * C[i][4] for i in range(len(C))]
    raise ValueError(kind)


def _trend(C, kind):
    cl = [c[4] for c in C]
    if kind == "ema":
        return [_sign(a, b) for a, b in zip(_ema(cl, 9), _ema(cl, 21))]
    if kind == "sma50":
        return [_sign(c, m) for c, m in zip(cl, _sma(cl, 50))]
    if kind == "donchian_mid":
        return [None if i < 19 else _sign(cl[i], (max(c[2] for c in C[i - 19:i + 1]) + min(c[3] for c in C[i - 19:i + 1])) / 2)
                for i in range(len(C))]
    raise ValueError(kind)


def _flow_pct(C15):
    m = [candle_momentum(C15[max(0, k - W_F + 1):k + 1], W_F) for k in range(len(C15))]
    return _pct(m, FLOW_SPAN)


def _location(C, p):
    """-> (series x, long_ok(x), short_ok(x)) for the L axis."""
    cl = [c[4] for c in C]
    if p["ref"] == "vwap":
        anchor = [None] * len(C)
        for i in range(N_MEAN - 1, len(C)):
            num = sum((c[2] + c[3] + c[4]) / 3 * c[5] for c in C[i - N_MEAN + 1:i + 1])
            den = sum(c[5] for c in C[i - N_MEAN + 1:i + 1])
            anchor[i] = num / den if den > 0 else None
    else:
        anchor = (_sma if p["ma"] == "sma" else _ema)(cl, N_MEAN)
    P = p["P_pull"]
    if p["loc"] == "atr_pct":
        sc = _vol_scale(C, p["vol"])
        dev = [None if a is None or s is None or not s > 0 else (c - a) / s for c, a, s in zip(cl, anchor, sc)]
        return _pct(dev, W_L), (lambda x: x <= P), (lambda x: x >= 100 - P)
    sd = _sd(cl, N_MEAN)
    z = [None if a is None or s is None or not s > 0 else (c - a) / s for c, a, s in zip(cl, anchor, sd)]
    if p["loc"] == "z":
        q = NormalDist().inv_cdf(P / 100)
        return z, (lambda x: x <= q), (lambda x: x >= -q)
    if p["loc"] == "bb_pctb":
        return _pct([None if x is None else 0.5 + x / 4 for x in z], W_L), (lambda x: x <= P), (lambda x: x >= 100 - P)
    raise ValueError(p["loc"])


# ── decisions ────────────────────────────────────────────────────────────────────────────────────────
def decisions(data, params):
    """-> list of (i, direction, entry_fields) for 1H decision bars i (entry_fields = barriers, or None when the
    exit builder cannot build them). Every input at bar i is known at the close of bar i, except P7 which also uses
    the entry price (open of bar i+1, known at the entry instant)."""
    p = dict(DEFAULTS, **params)
    unknown = set(p) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"unknown params {unknown}")
    C, C4 = data["1h"], data["4h"]
    if len(C) < 3 or not C4:
        return []
    ts4 = [c[0] for c in C4]
    S4 = _trend(C4, p["trend"])
    L, l_ok, s_ok = _location(C, p)
    atr = h.wilder_atr(C, N_ATR)
    need_flow = p["P2"] is not None or p["P5"] is not None or p["trigger"] == "flow_turn"
    if need_flow:
        C15 = data["15m"]
        ts15, F = [c[0] for c in C15], _flow_pct(C15)
    if p["P1"]:
        C1d = data["1d"]
        ts1d = [c[0] for c in C1d]
        cl1d = [c[4] for c in C1d]
        S1d = [_sign(a, b) for a, b in zip(_ema(cl1d, 9), _ema(cl1d, 21))]
    if p["P3"] is not None:
        nr = [(c[2] - c[3]) / c[4] for c in C]
        VR = _pct(_sma(nr, W_F), W_L)
    if p["P4"] is not None:
        cl = [c[4] for c in C]
        ER = _pct([efficiency_ratio(cl[i - ER_N:i + 1], ER_N) if i >= ER_N else None for i in range(len(C))], ER_W)
    if p["P6"] is not None:
        VP = _pct([c[5] for c in C], W_L)
    if p["P8"] is not None:
        st = _trend(C4, "ema")
        age = [None] * len(C4)
        for j, s in enumerate(st):
            if s is not None:
                age[j] = age[j - 1] + 1 if j and st[j - 1] == s and age[j - 1] is not None else 1
        AGE = _pct(age, W_L)
    build = _exit_builder(p["exit"])

    out = []
    for i in range(1, len(C)):
        T = C[i][0] + H1  # decision instant = close of bar i
        j4 = _closed(ts4, T, H4)
        if j4 < 0 or S4[j4] is None or S4[j4] == 0 or L[i - 1] is None:
            continue
        d = S4[j4]  # +1 long, -1 short
        if not (l_ok(L[i - 1]) if d > 0 else s_ok(L[i - 1])):
            continue
        if need_flow:
            k = _closed(ts15, T, M15)
            f_now = F[k] if k >= 0 else None
        tr = p["trigger"]
        if tr == "prev_high":
            ok = C[i][4] > C[i - 1][2] if d > 0 else C[i][4] < C[i - 1][3]
        elif tr == "prev_close":
            ok = C[i][4] > C[i - 1][4] if d > 0 else C[i][4] < C[i - 1][4]
        elif tr == "flow_turn":
            f_prev = F[k - 4] if k >= 4 else None
            ok = f_now is not None and f_prev is not None and (
                (f_prev < 50 < f_now) if d > 0 else (f_prev > 50 > f_now))
        else:
            raise ValueError(tr)
        if not ok:
            continue
        if p["P1"]:
            jd = _closed(ts1d, T, D1)
            if jd < 0 or S1d[jd] != d:
                continue
        if p["P2"] is not None and (f_now is None or not (f_now >= 100 - p["P2"] if d > 0 else f_now <= p["P2"])):
            continue
        if p["P3"] is not None and (VR[i] is None or VR[i] >= p["P3"]):
            continue
        if p["P4"] is not None and (ER[i] is None or ER[i] < p["P4"]):
            continue
        if p["P5"] is not None and (f_now is None or (f_now >= 100 - p["P5"] if d > 0 else f_now <= p["P5"])):
            continue
        if p["P6"] is not None and (VP[i] is None or VP[i] < p["P6"]):
            continue
        if p["P8"] is not None and (AGE[j4] is None or AGE[j4] < p["P8"]):
            continue
        direction = "long" if d > 0 else "short"
        b = build(C, i, direction, atr)
        if p["P7"] is not None and (b is None or abs(b["entry"] - C[i][4]) > p["P7"] * atr[i]):
            continue
        out.append((i, direction, b))
    return out


def _exit_builder(kind):
    if kind in ("X0", "X1"):
        fn = R.x0_barrier_fn("1h", T_MAX) if kind == "X0" else R.x0_barrier_fn("1h", T_MAX, sl_atr=3.0, rr=1000.0)
        return lambda C, i, d, atr: fn(C, i, d)
    if kind == "X4":  # structure SL: extreme of the last 6 bars -/+ 0.25 ATR, TP 1.5R, 16 bars
        bl = h.bars_limit_15m(T_MAX, "1h")

        def x4(C, i, d, atr):
            if atr[i] is None or i < 5 or i + 1 >= len(C):
                return None
            e = C[i + 1][1]
            sl = min(c[3] for c in C[i - 5:i + 1]) - 0.25 * atr[i] if d == "long" else \
                max(c[2] for c in C[i - 5:i + 1]) + 0.25 * atr[i]
            r = e - sl if d == "long" else sl - e
            if not r > 0:
                return None
            return dict(entry=e, sl=sl, tp=e + 1.5 * r if d == "long" else e - 1.5 * r, r_dist=r, bars_limit_15m=bl)
        return x4
    raise ValueError(f"exit {kind!r} not supported (X2 trailing needs a trailing scorer the runner does not have)")


def strategy(data, symbol, params):
    """runner interface: strategy(data, symbol, params) -> list[Entry]."""
    C = data["1h"]
    return [dict(b, symbol=symbol, direction=d, signal_ts_ms=C[i][0] + H1, meta={"exit": params.get("exit", "X0")})
            for i, d, b in decisions(data, params) if b is not None]


def param_cost(params):
    """parameter-budget units used by a config (core 1 + parts + non-X0 exit; catalog D6). Budget <= 5."""
    p = dict(DEFAULTS, **params)
    return GRIDS["P_pull"]["cost"] + sum(GRIDS[k]["cost"] for k in PARTS if p[k] not in (None, False)) \
        + (p["exit"] != "X0")
