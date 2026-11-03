"""H2 (track T4) — 15m C2 mean-reversion signal x maker LIMIT entry, conservative candle fill model.
Catalog 02 §4 H2 (definition, delta grid, fill model, adverse-selection outputs), §3 C2, §2.1 X0, §2.2 costs.
Stage 1: signal + fill model + adverse-selection rows. Build loop (parts Q1..Q5, E-series) is stage 2. stdlib only.

SIGNAL (decided at the CLOSE of 15m bar i, data <= i only; tested with harness.assert_no_lookahead)
  C2: close < SMA20 - 2*SD20 (population SD) -> long; close > SMA20 + 2*SD20 -> short (mirror).
  Limit = close -/+ delta * ATR14 (Wilder, harness.wilder_atr at bar i), rounded AWAY from the market onto the tick
  grid (long floor, short ceil) — a real order must sit on the grid.
ORDER LIFE
  Valid for bars i+1 .. i+window (window = 2, fixed constant). A new signal on bar j re-quotes: the pending order is
  cancelled at the close of j, so it covers bars i+1 .. min(i+window, j). (Assumption: cancel/replace, not stacking.)
FILL MODEL (future bars i+1.. — legitimate for fill simulation only; the signal never reads them)
  "through" (conservative, judgement): long fills in the first bar whose low is MORE than 1 tick below the limit,
     i.e. low < limit - 1 tick (catalog literal; on the tick grid this means >= 2 ticks beyond). Short mirror on high.
     The brief said ">= 1 tick beyond"; the catalog (frozen prereg) is one tick stricter and is used.
  "touch" (optimistic, auxiliary): low <= limit (short: high >= limit).
  Fill price = limit (no price improvement, also on gaps). No queue / partial-fill modelling (candles cannot).
SCORING A FILLED ORDER (fill bar k)
  X0 relative to the FILL price: SL = L -/+ 1.5*ATR_i, TP = +1.5R, 16 bars, no BE; ATR from the decision bar.
  Evaluation starts at the CLOSE of k (signal_ts_ms = k close) — the remainder of the fill bar after the fill is
  unknown, so a TP inside the fill bar is never credited (conservative). EXCEPTION: if the fill bar's low reaches
  the SL (short: high), the SL was certainly hit AFTER the fill (price must cross L before reaching SL < L), so the
  evaluation starts at the OPEN of k and evaluate_outcome resolves it as an SL inside k. Residual ambiguity: in that
  case a 5m sub-candle of k that hit TP before the fill would be credited as TP (needs a > 2.25 ATR swing up then
  down inside one 15m bar; rare, not counted).
  TP is a maker limit too: under "through" it fills only when price trades 2 ticks beyond TP (tp is passed to the
  runner shifted by 2 ticks and the extra 2 ticks are removed from gross/net R of TP exits — run_h2 / _tp_fix).
COSTS: runner scenario A (maker entry + maker TP) on FILLED trades only. Unfilled orders = no trade (recorded in AS).
  Taker comparison (C2@15m, same signal set): X0 at the open of i+1 (runner.x0_barrier_fn), costs B/C.
ADVERSE SELECTION (adverse_selection): every complete order scored independently (no OPEN blocking — measurement):
  R_mkt  = taker entry at i+1 open (catalog AS definition: AS_fill = E[R_mkt|fill] - E[R_mkt|all], missed =
           E[R_mkt|unfilled]);
  R_lim  = filled: the H2 trade; unfilled: counterfactual entry AT THE LIMIT from the close of the last valid bar
           (fictional price — the market never traded there; measures what the fill rule excluded);
  drift1 / drift4 = signed (close[k] / close[k+3] - L) / ATR, k = fill bar (1 and 4 bars counting the fill bar).
Funding = 0 (runner limitation, unknown sign). All results are CONDITIONAL ON THE CANDLE FILL MODEL.
"""
import math, os, statistics as st, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as h  # noqa: E402
import runner as R  # noqa: E402
import phase1_rescore as prp  # noqa: E402

M15 = R.M15
N_BB, K_BB, T_MAX, THROUGH_TICKS = 20, 2.0, 16, 2
CORE = dict(delta=0.25, window=2, fill="through")
DELTA_GRID = dict(grid=[0.0, 0.25, 0.5], center=0.25, cost=1)  # catalog §4 H2, centre pre-registered


def decisions(C, delta, tick):
    """-> list aligned with C: None or (s, limit_ticks, atr) decided at the close of bar i from bars <= i."""
    atr = h.wilder_atr(C)
    out = [None] * len(C)
    for i in range(N_BB - 1, len(C)):
        a = atr[i]
        if not a or a <= 0:
            continue
        w = [c[4] for c in C[i - N_BB + 1:i + 1]]
        m = sum(w) / N_BB
        sd = math.sqrt(sum((x - m) ** 2 for x in w) / N_BB)
        c = C[i][4]
        s = 1 if c < m - K_BB * sd else -1 if c > m + K_BB * sd else 0
        if s:
            x = (c - s * delta * a) / tick
            out[i] = (s, math.floor(x + 1e-9) if s > 0 else math.ceil(x - 1e-9), a)
    return out


def fill_bar(C, i, s, n_lim, tick, end, through=True):
    """First bar k in i+1..end whose price trades beyond the limit (see module doc); None = not filled."""
    for k in range(i + 1, min(end, len(C) - 1) + 1):
        beyond = s * (n_lim - round((C[k][3] if s > 0 else C[k][2]) / tick))  # ticks past the limit
        if (beyond >= THROUGH_TICKS) if through else (beyond >= 0):
            return k
    return None


def orders(C, params, tick):
    """Every C2 signal bar -> order dict (i, s, limit, atr, end, k = fill bar or None, complete)."""
    W, through = params["window"], params["fill"] == "through"
    D = decisions(C, params["delta"], tick)
    sig = [i for i, d in enumerate(D) if d]
    out = []
    for j, i in enumerate(sig):
        s, n, a = D[i]
        end = min(i + W, sig[j + 1]) if j + 1 < len(sig) else i + W  # next signal cancels/re-quotes
        k = fill_bar(C, i, s, n, tick, end, through)
        out.append(dict(i=i, s=s, n_lim=n, limit=n * tick, atr=a, end=end, k=k,
                        complete=k is not None or end <= len(C) - 1))
    return out


def _limit_entry(o, C, sym, tick, through, start_bar, start_open):
    d = "long" if o["s"] > 0 else "short"
    b = h.x0_barriers(d, o["limit"], o["atr"])
    shift = THROUGH_TICKS * tick if through else 0.0
    return dict(b, tp=b["tp"] + o["s"] * shift, symbol=sym, direction=d,
                signal_ts_ms=C[start_bar][0] + (0 if start_open else M15), bars_limit_15m=T_MAX,
                entry_maker=True, tp_maker=True,
                meta=dict(decision_ts=C[o["i"]][0] + M15, fill_bar_ts=C[o["k"]][0] if o["k"] is not None else None,
                          tp_shift=shift, sl_in_fill_bar=start_open))


def fill_entry(o, C, sym, tick, through=True):
    """Entry for a filled order (see SCORING in module doc)."""
    k, s = o["k"], o["s"]
    sl = o["limit"] - s * 1.5 * o["atr"]
    sl_hit = C[k][3] <= sl if s > 0 else C[k][2] >= sl
    return _limit_entry(o, C, sym, tick, through, k, sl_hit)


def strategy(data, sym, params):
    """runner strategy: filled H2 orders only. Use run_h2 (applies the TP trade-through correction)."""
    C, tick = data["15m"], prp.TICK[sym]
    through = params["fill"] == "through"
    return [fill_entry(o, C, sym, tick, through) for o in orders(C, params, tick) if o["k"] is not None]


def taker_strategy(data, sym, params):
    """C2@15m taker execution on the SAME signal bars (X0 at the open of i+1) — paired baseline."""
    C, x0 = data["15m"], R.x0_barrier_fn("15m")
    out = []
    for i, d in enumerate(decisions(C, params["delta"], prp.TICK[sym])):
        b = d and x0(C, i, "long" if d[0] > 0 else "short")
        if b:
            out.append(dict(b, symbol=sym, direction="long" if d[0] > 0 else "short", signal_ts_ms=C[i][0] + M15))
    return out


def _tp_fix(tr):
    """Remove the TP trade-through shift from a TP exit (gross was computed at the shifted price)."""
    sh = tr["meta"].get("tp_shift", 0.0)
    if tr["exit"] == "TP" and sh:
        x = sh / tr["r_dist"]
        tr["gross_R"] -= x
        tr["net_R"] = {c: v - x for c, v in tr["net_R"].items()}
        tr["net"] -= x
    return tr


def run_h2(params, symbols, period, scenario="A", **kw):
    """runner.run(strategy) + TP trade-through correction. Same flags/gates as runner.run."""
    T = R.run(strategy, params, symbols, period, scenario, **kw)
    for t in T:
        _tp_fix(t)
    return T


def adverse_selection(symbols, period, params, **kw):
    """Per-order rows (complete orders inside the period, embargo applied) + summary. See module doc."""
    through, rows = params["fill"] == "through", []
    x0 = R.x0_barrier_fn("15m")
    for sym in symbols:
        D = R.load(sym, period, **kw)
        C, tick = D["15m"], prp.TICK[sym]
        for o in orders(C, params, tick):
            t_dec = C[o["i"]][0] + M15
            if not o["complete"] or t_dec < D.start_ms or t_dec + (params["window"] + T_MAX) * M15 > D.end_ms:
                continue
            d = "long" if o["s"] > 0 else "short"
            mk = x0(C, o["i"], d)
            if mk is None:
                continue
            mkt = R._score(D.ex, dict(mk, symbol=sym, direction=d, signal_ts_ms=t_dec), "B")
            if o["k"] is not None:
                lim = fill_entry(o, C, sym, tick, through)
            else:
                lim = _limit_entry(o, C, sym, tick, through, o["end"], False)
            lt = R._score(D.ex, lim, "A")
            if mkt is None or lt is None:
                continue
            _tp_fix(lt)
            row = dict(symbol=sym, direction=d, decision_ts_ms=t_dec, filled=o["k"] is not None,
                       fill_latency=None if o["k"] is None else o["k"] - o["i"],
                       mkt_gross=mkt["gross_R"], mkt_net_B=mkt["net_R"]["B"], mkt_net_C=mkt["net_R"]["C"],
                       lim_gross=lt["gross_R"], lim_net_A=lt["net_R"]["A"], lim_exit=lt["exit"])
            k = o["k"]
            if k is not None and k + 3 < len(C):
                row.update(drift1=o["s"] * (C[k][4] - o["limit"]) / o["atr"],
                           drift4=o["s"] * (C[k + 3][4] - o["limit"]) / o["atr"])
            rows.append(row)
    return dict(rows=rows, summary=as_summary(rows))


def as_summary(rows):
    def m(xs):
        xs = [x for x in xs if x is not None]
        return dict(n=len(xs), mean=st.mean(xs) if xs else None)
    F = [r for r in rows if r["filled"]]
    U = [r for r in rows if not r["filled"]]
    out = dict(n_orders=len(rows), n_filled=len(F), fill_rate=len(F) / len(rows) if rows else None,
               mkt_gross_all=m(r["mkt_gross"] for r in rows), mkt_gross_filled=m(r["mkt_gross"] for r in F),
               mkt_gross_unfilled=m(r["mkt_gross"] for r in U),  # = catalog "missed opportunity"
               lim_gross_filled=m(r["lim_gross"] for r in F), lim_gross_unfilled_cf=m(r["lim_gross"] for r in U),
               lim_net_A_filled=m(r["lim_net_A"] for r in F), mkt_net_B_all=m(r["mkt_net_B"] for r in rows),
               drift1=m(r.get("drift1") for r in F), drift4=m(r.get("drift4") for r in F))
    a, f = out["mkt_gross_all"]["mean"], out["mkt_gross_filled"]["mean"]
    out["AS_fill"] = None if a is None or f is None else f - a
    return out
