"""H6 (track T3') — 1H Donchian breakout. Strategy for research/runner.py (catalog 02 §4.6, parts §5.1, exits §5.5).

Core (long; short = mirror), decided at the CLOSE of 1H bar i, entry at the open of bar i+1:
  close[i] > max(high[i-N..i-1]) -> long / close[i] < min(low[i-N..i-1]) -> short   (= controls.c1 with N free)
  Exit X0 (SL 1.5 ATR14 1H, TP 1.5R, 16 bars, no BE). E7 options X1 / X4 via hyp_h1's exit builder.

params (defaults = core): N (grid {10, 20, 40}, centre 20), parts P1 P6 P4 P3 P5 P2 P8 (catalog FIXED order; P7
excluded by the catalog), exit. A part is None/False = OFF, or a value from hyp_h1.GRIDS = ON at that value.

Part reuse: hyp_h1.decisions fuses the parts into the pullback loop (S4 trend + L_pct gate), so it cannot be
called for a breakout core. The series helpers, grids and exit builder are imported from hyp_h1; only the per-bar
part checks below are restated, each a line-for-line copy of the hyp_h1 rule with d = breakout direction.
One reading needed: P8 (regime age) in H1 is the age of the 4H EMA9/21 state the trade is aligned with (H1's core
forces alignment). H6 has no 4H trend in the core, so P8 = age percentile of the CURRENT 4H EMA state, direction-
free (same series as H1, no alignment added - adding it would be a second change). Scale-free: bar counts,
percentiles, ATR multiples only.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as h  # noqa: E402
import hyp_h1 as H  # noqa: E402
from ortho_engine import efficiency_ratio  # noqa: E402

GRIDS = {"N": dict(grid=[10, 20, 40], center=20, cost=1), **{k: H.GRIDS[k] for k in ("P1", "P2", "P3", "P4", "P5", "P6", "P8")}}
PARTS = ["P1", "P6", "P4", "P3", "P5", "P2", "P8"]  # catalog §4.6 fixed order
E7 = ["X0", "X1", "X4"]
DEFAULTS = dict(N=20, **{p: None for p in PARTS}, exit="X0")


def decisions(data, params):
    """-> list of (i, direction, entry_fields or None) for 1H decision bars i. Inputs at bar i are closed bars only."""
    p = dict(DEFAULTS, **params)
    if set(p) - set(DEFAULTS):
        raise ValueError(f"unknown params {set(p) - set(DEFAULTS)}")
    C, N = data["1h"], p["N"]
    if len(C) <= N:
        return []
    if p["P2"] is not None or p["P5"] is not None:
        C15 = data["15m"]
        ts15, F = [c[0] for c in C15], H._flow_pct(C15)
    if p["P1"]:
        C1d = data["1d"]
        ts1d, cl1d = [c[0] for c in C1d], [c[4] for c in C1d]
        S1d = [H._sign(a, b) for a, b in zip(H._ema(cl1d, 9), H._ema(cl1d, 21))]
    if p["P3"] is not None:
        VR = H._pct(H._sma([(c[2] - c[3]) / c[4] for c in C], H.W_F), H.W_L)
    if p["P4"] is not None:
        cl = [c[4] for c in C]
        ER = H._pct([efficiency_ratio(cl[i - H.ER_N:i + 1], H.ER_N) if i >= H.ER_N else None for i in range(len(C))], H.ER_W)
    if p["P6"] is not None:
        VP = H._pct([c[5] for c in C], H.W_L)
    if p["P8"] is not None:
        C4 = data["4h"]
        ts4, st4 = [c[0] for c in C4], H._trend(C4, "ema")
        age = [None] * len(C4)
        for j, s in enumerate(st4):
            if s is not None:
                age[j] = age[j - 1] + 1 if j and st4[j - 1] == s and age[j - 1] is not None else 1
        AGE = H._pct(age, H.W_L)
    atr = h.wilder_atr(C, H.N_ATR)
    build = H._exit_builder(p["exit"])

    out = []
    for i in range(N, len(C)):
        w, c = C[i - N:i], C[i][4]
        d = 1 if c > max(x[2] for x in w) else -1 if c < min(x[3] for x in w) else 0
        if not d:
            continue
        T = C[i][0] + H.H1  # decision instant = close of bar i
        if p["P1"]:
            jd = H._closed(ts1d, T, H.D1)
            if jd < 0 or S1d[jd] != d:
                continue
        if p["P2"] is not None or p["P5"] is not None:
            k = H._closed(ts15, T, H.M15)
            f = F[k] if k >= 0 else None
        if p["P2"] is not None and (f is None or not (f >= 100 - p["P2"] if d > 0 else f <= p["P2"])):
            continue
        if p["P3"] is not None and (VR[i] is None or VR[i] >= p["P3"]):
            continue
        if p["P4"] is not None and (ER[i] is None or ER[i] < p["P4"]):
            continue
        if p["P5"] is not None and (f is None or (f >= 100 - p["P5"] if d > 0 else f <= p["P5"])):
            continue
        if p["P6"] is not None and (VP[i] is None or VP[i] < p["P6"]):
            continue
        if p["P8"] is not None:
            j4 = H._closed(ts4, T, H.H4)
            if j4 < 0 or AGE[j4] is None or AGE[j4] < p["P8"]:
                continue
        direction = "long" if d > 0 else "short"
        out.append((i, direction, build(C, i, direction, atr)))
    return out


def strategy(data, symbol, params):
    """runner interface: strategy(data, symbol, params) -> list[Entry]."""
    C = data["1h"]
    return [dict(b, symbol=symbol, direction=d, signal_ts_ms=C[i][0] + H.H1, meta={"exit": params.get("exit", "X0")})
            for i, d, b in decisions(data, params) if b is not None]


def param_cost(params):
    """budget units: core N 1 + parts + non-X0 exit (catalog D6). Budget <= 5."""
    p = dict(DEFAULTS, **params)
    return 1 + sum(GRIDS[k]["cost"] for k in PARTS if p[k] not in (None, False)) + (p["exit"] != "X0")
