"""V5 shared experiment runner (master plan §4.5 / W7b). stdlib only. Imports harness + phase1_rescore; reimplements
neither scoring (harness.score -> src/ortho_resolver.evaluate_outcome) nor costs (phase1_rescore.cost_R).

STRATEGY INTERFACE ─────────────────────────────────────────────────────────────────────────────────────────────
  strategy(data, symbol, params) -> list[Entry]          called once per symbol by run()

  data   : Data (dict-like). data["15m"] / "5m" / "1h" / "4h" / "1d" ("1H"/"4H" also accepted) -> list of candle
           tuples (ts_open_ms, open, high, low, close, vol), ascending, READ-ONLY (tuples, shared cache). Holds only
           bars with open >= period start - warm-up (default 60 d) and CLOSE <= period end: no candle after the
           period end. data.start_ms / data.end_ms = period bounds (end exclusive), data.symbol = 'BTC'.
           Bar i is known at its close, ts_open + TF; decide on bar i, enter at the open of bar i+1.
  Entry  : dict
           symbol          'BTC' (base; must equal the symbol argument)
           direction       'long' | 'short'
           signal_ts_ms    CLOSE time of the decision bar (= open of the entry bar). Must be on a 5m boundary.
           entry, tp, sl   prices; long needs sl < entry < tp, short the mirror
           r_dist          price distance of 1R (normally |entry - sl|); gross R = signed PnL / r_dist
           bars_limit_15m  int >= 1, holding limit in 15m units (use harness.bars_limit_15m(n, tf))
           entry_maker     bool, default False  } recorded on the trade only (see COSTS); for H2's fill model
           tp_maker        bool, default True   }
           meta            dict, passed through untouched
  x0_barrier_fn(tf) builds X0 (catalog §2.1) entries from a decision bar; strategies may use it.

RUN RULES (run())
  * Entries before the period start (warm-up) are dropped; entries with signal_ts + bars_limit*15m > period end are
    dropped (catalog §1 boundary embargo). Counts in trades.info.
  * One open position per symbol (CLAUDE.md OPEN-duplicate rule): entries are taken in signal_ts order (ties: the
    strategy's order); an entry whose signal_ts < exit_ts of the open trade is skipped (info['skipped_open']).
    exit_ts = signal_ts + bars*15m, bars = evaluate_outcome's ceil(5m candles used / 3) -> rounded UP to the 15m unit
    (conservative: a 5m-TF strategy can lose up to two 5m re-entry slots per trade).
  * X0 has no BE: run() refuses if src/ortho_config BE_TRIGGER_R != 0.

COSTS: cost_R for A, B, C exactly as 01_PREREG §2.2-2.3 via phase1_rescore.cost_R, tickSz = phase1_rescore.TICK
  (= facts_okx.md §5 table, current tickSz assumed constant). Scenario A ASSUMES maker entry + maker TP for every
  trade regardless of entry_maker/tp_maker (those flags do NOT change any cost column). net_R[s] = gross - cost[s]
  - fund_R; trade['net'] = net_R[scenario].
FUNDING LIMITATION: OKX funding history exists only for 2026-08+, so for EXPLORE/VALIDATION (and most of HOLDOUT)
  funding is unknown -> fund_R = 0 and every trade/summary carries funding_unavailable=True (01 §2.4 V3 rule).
  Net R is therefore biased by the omitted carry (sign unknown; long-heavy strategies in positive-funding regimes
  are flattered).

PERIODS (UTC, end exclusive): EXPLORE, VALIDATION (needs open_validation=True: "one judgement only"), HOLDOUT
  (needs open_holdout=True, also unseals the harness cache). Opening either appends a line to
  <data_dir>/access.log. LEDGER.md is NOT written here.

STATS: summary, mde, bh_fdr, pbo_cscv (+block_means), deflated_sharpe (+moments), plateau_check, perturb,
  p_mean_gt0, p_delta. C0: random_entries. Methods documented per function.
Other limits: 5m data gaps are invisible to evaluate_outcome (it counts candles, not time); no spread/depth history.
"""
import bisect, inspect, json, logging, math, os, random, statistics as st, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import combinations
from statistics import NormalDist

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as h  # noqa: E402
import phase1_rescore as prp  # noqa: E402

orp = prp.orp
log = logging.getLogger("v5.runner")

EXPLORE = ("2023-06-01", "2025-03-01")
VALIDATION = ("2025-03-01", "2026-01-01")
HOLDOUT = ("2026-01-01", "2026-11-01")
WARMUP_DAYS = 60
M15, M5, DAY = 900_000, 300_000, 86_400_000
SEED = prp.SEED


def _ms(d):
    return int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def _base(s):
    return h._base(s)


# ── period gate + data ──────────────────────────────────────────────
_EX, _ROWS = {}, {}


def _ctx(period, open_validation, open_holdout, data_dir):
    s, e = _ms(period[0]), _ms(period[1])
    if s >= e:
        raise ValueError(f"empty period {period}")
    hit_v = s < _ms(VALIDATION[1]) and e > _ms(VALIDATION[0])
    hit_h = e > _ms(HOLDOUT[0])
    if hit_v and not open_validation:
        raise h.HarnessError(f"period {period} overlaps VALIDATION: pass open_validation=True (one judgement only)")
    if hit_h and not open_holdout:
        raise h.HarnessError(f"period {period} overlaps sealed HOLDOUT: pass open_holdout=True")
    if hit_v or hit_h:
        caller = next((f"{f.filename}:{f.lineno}" for f in inspect.stack()[1:] if f.filename != __file__), "?")
        with open(os.path.join(data_dir, "access.log"), "a") as f:
            f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "period": list(period),
                                "validation": hit_v, "holdout": hit_h, "caller": caller}) + "\n")
    key = (data_dir, hit_h)
    if key not in _EX:
        _EX[key] = h.CachedExchange(data_dir, allow_holdout=hit_h)
    return s, e, _EX[key]


def _rows(ex, sym, tf):
    k = (id(ex), sym, tf)
    if k not in _ROWS:
        ts, rows = ex._load(sym, tf)
        _ROWS[k] = (ts, [tuple(r) for r in rows])
    return _ROWS[k]


class Data(dict):
    """Lazy per-TF candle lists for one symbol, restricted to [start - warm-up, end] (see module docstring)."""

    def __init__(self, ex, symbol, start_ms, end_ms, warmup_ms):
        super().__init__()
        self.ex, self.symbol, self.start_ms, self.end_ms, self.warmup_ms = ex, symbol, start_ms, end_ms, warmup_ms

    def __missing__(self, tf):
        t = h._tf(tf)
        ts, rows = _rows(self.ex, self.symbol, t)
        v = rows[bisect.bisect_left(ts, self.start_ms - self.warmup_ms):bisect.bisect_right(ts, self.end_ms - h.TF_MS[t])]
        self[tf] = v
        return v


def load(symbol, period, *, open_validation=False, open_holdout=False, data_dir=h.DATA, warmup_days=WARMUP_DAYS):
    """Data for one symbol/period (same gate as run()). Use for C0 and for inspecting what a strategy sees."""
    s, e, ex = _ctx(period, open_validation, open_holdout, data_dir)
    return Data(ex, _base(symbol), s, e, warmup_days * DAY)


# ── scoring ─────────────────────────────────────────────────────────
class Trades(list):
    """list of trade dicts + .info (counts: emitted, warmup_dropped, embargoed, skipped_open, unresolved, ...)."""
    info = {}


def _check(en, sym):
    s = {"long": 1, "short": -1}.get(en.get("direction"))
    if s is None or _base(en["symbol"]) != sym:
        raise ValueError(f"bad direction/symbol {en}")
    if not (en["r_dist"] > 0 and s * (en["tp"] - en["entry"]) > 0 and s * (en["entry"] - en["sl"]) > 0):
        raise ValueError(f"barriers on the wrong side for {en['direction']}: {en}")
    if en["signal_ts_ms"] % M5 or not isinstance(en["bars_limit_15m"], int) or en["bars_limit_15m"] < 1:
        raise ValueError(f"signal_ts_ms must be on a 5m boundary and bars_limit_15m an int >= 1: {en}")


def _score(ex, en, scenario):
    t0 = en["signal_ts_ms"]
    dt = datetime.fromtimestamp(t0 / 1000, timezone.utc)
    res = h.score([dict(symbol=en["symbol"], direction=en["direction"], signaled_at=dt.isoformat(), entry=en["entry"],
                        tp=en["tp"], sl=en["sl"], r_dist=en["r_dist"], bars_limit=en["bars_limit_15m"])], ex)[0]
    if not res["resolved"]:
        return None
    sym, x, g = _base(en["symbol"]), res["exit_reason"], res["gross_r"]
    cost = {c: prp.cost_R(en["entry"], en["r_dist"], x, c, prp.TICK[sym]) for c in "ABC"}
    net = {c: g - cost[c] for c in "ABC"}  # fund_R = 0 (unavailable)
    return dict(symbol=sym, direction=en["direction"], signal_ts_ms=t0, day=dt.date().isoformat(),
                month=dt.strftime("%Y-%m"), entry=en["entry"], tp=en["tp"], sl=en["sl"], r_dist=en["r_dist"],
                bars_limit_15m=en["bars_limit_15m"], exit=x, status=res["status"], bars=res["bars"],
                exit_ts_ms=t0 + res["bars"] * M15, gross_R=g, cost_R=cost, fund_R=0.0, funding_unavailable=True,
                net_R=net, net=net[scenario], entry_maker=bool(en.get("entry_maker", False)),
                tp_maker=bool(en.get("tp_maker", True)), meta=en.get("meta", {}))


def run(strategy, params, symbols, period, scenario="A", *, open_validation=False, open_holdout=False,
        data_dir=h.DATA, warmup_days=WARMUP_DAYS):
    """-> Trades (list of trade dicts, see _score) with .info counts. Rules in the module docstring."""
    if h.oc.BE_TRIGGER_R:
        raise h.HarnessError("BE active in ortho_config (ORTHO_V4_ENABLED=false?) - X0 has no BE")
    if scenario not in "ABC" or len(scenario) != 1:
        raise ValueError(scenario)
    s, e, ex = _ctx(period, open_validation, open_holdout, data_dir)
    out, info = Trades(), Counter()
    for sym in map(_base, symbols):
        last_exit = -1
        for en in sorted(strategy(Data(ex, sym, s, e, warmup_days * DAY), sym, params), key=lambda x: x["signal_ts_ms"]):
            _check(en, sym)
            info["emitted"] += 1
            t0 = en["signal_ts_ms"]
            if t0 < s:
                info["warmup_dropped"] += 1
            elif t0 + en["bars_limit_15m"] * M15 > e:
                info["embargoed"] += 1
            elif t0 < last_exit:
                info["skipped_open"] += 1
            else:
                tr = _score(ex, en, scenario)
                if tr is None:
                    info["unresolved"] += 1
                else:
                    out.append(tr)
                    last_exit = tr["exit_ts_ms"]
    out.info = dict(info, n=len(out), period=tuple(period), scenario=scenario, funding_unavailable=True)
    log.info("run %s: %s", period, out.info)
    return out


# ── X0 barriers + C0 random-entry control (catalog §2.1, §3) ─────────
def x0_barrier_fn(tf="15m", n_bars=16, sl_atr=1.5, rr=1.5):
    """-> fn(candles, i, direction) -> Entry fields (entry = open of bar i+1, X0 SL/TP from Wilder ATR14 at the close
    of bar i, bars_limit = n_bars of tf) or None (ATR warm-up / no next bar). Caller adds symbol/direction/signal_ts."""
    cache, bl = {}, h.bars_limit_15m(n_bars, tf)

    def fn(candles, i, direction):
        c = cache.get(id(candles))
        if c is None or c[0] is not candles:
            c = cache[id(candles)] = (candles, h.wilder_atr(candles))
        atr = c[1][i]
        if atr is None or not atr > 0 or i + 1 >= len(candles):
            return None
        return dict(h.x0_barriers(direction, candles[i + 1][1], atr, sl_atr, rr), bars_limit_15m=bl)
    return fn


def _month_ms(m):
    y, mo = map(int, m.split("-"))
    return _ms(f"{y}-{mo:02d}-01"), _ms(f"{y + mo // 12}-{mo % 12 + 1:02d}-01")


def random_entries(reference_trades, data, seeds, barrier_fn, tf="15m", beta=False, scenario="A", max_tries=1000):
    """C0 / C0-beta (catalog §3). data = {symbol: Data} from load() over the SAME period as the reference.
    Uses only the reference's (symbol, month) counts and, for beta=True, its (symbol, month) long share — never its R.
    Per seed (random.Random(seed); catalog says numpy PCG64 - stdlib deviation, still seed-reproducible), per symbol,
    cells in month order: draw a decision bar uniformly among bars whose close lies in the month and the period,
    direction Bernoulli(0.5) (beta: cell long share), exits via barrier_fn (same construction as the reference),
    score; reject + redraw if ineligible (warm-up, embargo, unresolved) or its [signal, exit) overlaps an already
    placed trade of the same symbol; after max_tries failed draws the slot is recorded in info['shortfall'].
    -> {seed: Trades} (same trade schema as run())."""
    cells = defaultdict(lambda: [0, 0])
    for t in reference_trades:
        c = cells[(t["symbol"], t["month"])]
        c[0] += 1
        c[1] += t["direction"] == "long"
    tfm = h.TF_MS[h._tf(tf)]
    out = {}
    for seed in seeds:
        rng, T, short = random.Random(seed), Trades(), Counter()
        for sym in sorted({s for s, _ in cells}):
            D = data[sym]
            C = D[tf]
            ts = [c[0] for c in C]
            starts, ends = [], []
            for (s_, month), (n, nl) in sorted(cells.items()):
                if s_ != sym:
                    continue
                lo, hi = _month_ms(month)
                i0 = bisect.bisect_left(ts, max(lo, D.start_ms) - tfm)
                i1 = bisect.bisect_left(ts, min(hi, D.end_ms) - tfm)
                p_long = nl / n if beta else 0.5
                for _ in range(n):
                    for _try in range(max_tries if i1 > i0 else 0):
                        i = rng.randrange(i0, i1)
                        d = "long" if rng.random() < p_long else "short"
                        b = barrier_fn(C, i, d)
                        t0 = ts[i] + tfm
                        if b is None or t0 + b["bars_limit_15m"] * M15 > D.end_ms:
                            continue
                        j = bisect.bisect_right(starts, t0)
                        if j and ends[j - 1] > t0:
                            continue
                        tr = _score(D.ex, dict(b, symbol=sym, direction=d, signal_ts_ms=t0), scenario)
                        if tr is None or (j < len(starts) and starts[j] < tr["exit_ts_ms"]):
                            continue
                        starts.insert(j, t0)
                        ends.insert(j, tr["exit_ts_ms"])
                        T.append(tr)
                        break
                    else:
                        short[(sym, month)] += 1
        T.sort(key=lambda t: (t["signal_ts_ms"], t["symbol"]))
        T.info = dict(n=len(T), seed=seed, beta=beta, shortfall=dict(short), funding_unavailable=True)
        out[seed] = T
    return out


# ── statistics ──────────────────────────────────────────────────────
def _pt(T, scen):
    n = len(T)
    return dict(n=n, small=n < prp.MIN_N, mean=st.mean(t["net_R"][scen] for t in T) if n else None)


def summary(trades, scen="A", alpha=0.05):
    """n, mean gross/cost/net, percentile bootstrap CI (ortho_report.bootstrap_ci) + UTC-day block CI
    (phase1_rescore.block_ci), judgement CI = wider of the two, net win rate (net>0) + Wilson, breakeven WR,
    point estimates per direction / symbol / month. alpha: pass 0.05/k for Bonferroni (master §5.1).
    n < 30 -> small=True and no CI (n-gate)."""
    n = len(trades)
    out = dict(n=n, small=n < prp.MIN_N, scenario=scen, alpha=alpha, funding_unavailable=True)
    if not n:
        return out
    net = [t["net_R"][scen] for t in trades]
    out.update(mean=st.mean(net), gross=st.mean(t["gross_R"] for t in trades),
               cost=st.mean(t["cost_R"][scen] for t in trades), sd=st.pstdev(net))
    if n >= prp.MIN_N:
        cp = orp.bootstrap_ci(net, alpha=alpha)
        cb = prp.block_ci(net, [t["day"] for t in trades], alpha=alpha)
        out.update(ci_pct=cp, ci_blk=cb, ci=(min(cp[0], cb[0]), max(cp[1], cb[1])))
    w = sum(x > 0 for x in net)
    lo, hi = orp.wilson(w, n, z=NormalDist().inv_cdf(1 - alpha / 2))
    out.update(win=w / n, win_ci=(lo / 100, hi / 100), breakeven_wr=prp.breakeven_wr(net),
               exits=dict(Counter(t["exit"] for t in trades)))
    for key, name in (("direction", "by_direction"), ("symbol", "by_symbol"), ("month", "by_month")):
        g = defaultdict(list)
        for t in trades:
            g[t[key]].append(t)
        out[name] = {k: _pt(g[k], scen) for k in sorted(g)}
    return out


def mde(n, sigma=1.24, alpha=0.05, power=0.8, de=1.5):
    """Catalog §2.3: (z_{1-a/2} + z_power) * sigma / sqrt(n / DE), two-sided."""
    z = NormalDist().inv_cdf
    return (z(1 - alpha / 2) + z(power)) * sigma / math.sqrt(n / de)


def bh_fdr(pvalues, q=0.10):
    """Benjamini-Hochberg step-up. -> list[bool] rejections in input order (master §5.6 layer 1, q = 10%)."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    k = max((r + 1 for r, i in enumerate(order) if pvalues[i] <= (r + 1) * q / m), default=0)
    rej = [False] * m
    for i in order[:k]:
        rej[i] = True
    return rej


def block_means(trades, period, S=16, scen="A"):
    """Per-time-block mean net R over `period` cut into S equal blocks (by signal_ts). Empty block -> 0.0
    (no trades = no PnL). One row of the pbo_cscv matrix; build every config's row with the same period/S."""
    s, e = _ms(period[0]), _ms(period[1])
    g = [[] for _ in range(S)]
    for t in trades:
        g[min(S - 1, (t["signal_ts_ms"] - s) * S // (e - s))].append(t["net_R"][scen])
    return [sum(x) / len(x) if x else 0.0 for x in g]


def pbo_cscv(matrix, S=16):
    """Probability of Backtest Overfitting, CSCV (Bailey, Borwein, Lopez de Prado, Zhu 2015).
    matrix: N configs x T time blocks (per-block mean net R, e.g. block_means). Columns are cut into S contiguous
    groups (S even, T >= S); for every C(S, S/2) split, IS = S/2 groups, OOS = the rest; performance = mean of the
    columns (stand-in for the paper's Sharpe). n* = argmax IS; w = OOS relative rank of n* / (N+1) (ties = mid-rank);
    lambda = logit(w); PBO = share of splits with lambda <= 0."""
    N, T = len(matrix), len(matrix[0])
    if N < 2 or S % 2 or T < S or any(len(r) != T for r in matrix):
        raise ValueError("need N>=2 configs, S even, T>=S, rectangular matrix")
    b = [round(j * T / S) for j in range(S + 1)]
    G = [[sum(r[b[j]:b[j + 1]]) for j in range(S)] for r in matrix]
    tot = [sum(g) for g in G]
    lam = []
    for IS in combinations(range(S), S // 2):
        ci = sum(b[j + 1] - b[j] for j in IS)
        isum = [sum(g[j] for j in IS) for g in G]
        best = max(range(N), key=lambda n: isum[n])
        oos = [(tot[n] - isum[n]) / (T - ci) for n in range(N)]
        rank = sum(v < oos[best] for v in oos) + 0.5 * (sum(v == oos[best] for v in oos) - 1) + 1
        w = rank / (N + 1)
        lam.append(math.log(w / (1 - w)))
    return dict(pbo=sum(x <= 0 for x in lam) / len(lam), n_splits=len(lam), lambda_median=st.median(lam))


def moments(vals):
    """-> (sharpe, skew, kurt (non-excess, normal = 3), T) of per-trade (or per-period) returns, for deflated_sharpe."""
    T, m = len(vals), st.mean(vals)
    sd = st.pstdev(vals)
    sk = sum((x - m) ** 3 for x in vals) / T / sd ** 3
    ku = sum((x - m) ** 4 for x in vals) / T / sd ** 4
    return m / sd, sk, ku, T


def deflated_sharpe(sr, n_trials, skew, kurt, T, sr_var=None):
    """Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014). sr = non-annualised SR of the selected config over T
    observations; kurt non-excess. SR0 = sqrt(V) * ((1-g) * Phi^-1(1 - 1/N) + g * Phi^-1(1 - 1/(N e))), g = Euler-
    Mascheroni; DSR = Phi((sr - SR0) sqrt(T-1) / sqrt(1 - skew sr + (kurt-1)/4 sr^2)). V = variance of the SRs across
    the N trials - pass the empirical value; default 1/(T-1) is the iid-normal null approximation."""
    nd, g = NormalDist(), 0.5772156649015329
    v = sr_var if sr_var is not None else 1 / (T - 1)
    sr0 = math.sqrt(v) * ((1 - g) * nd.inv_cdf(1 - 1 / n_trials) + g * nd.inv_cdf(1 - 1 / (n_trials * math.e))) \
        if n_trials > 1 else 0.0
    return nd.cdf((sr - sr0) * math.sqrt(T - 1) / math.sqrt(1 - skew * sr + (kurt - 1) / 4 * sr * sr))


def plateau_check(grid_results, center_idx):
    """Master §5.3. grid_results = Net point estimates on the pre-registered 3-5 point grid (in grid order),
    center_idx = pre-registered centre. Pass iff centre > 0 and BOTH neighbours exist, are > 0 and >= 50% of the
    centre. (centre > 0 is implied by the adoption rules; a non-positive centre makes the 50% test meaningless.)"""
    g, c = grid_results, center_idx
    why = []
    if not 3 <= len(g) <= 5:
        why.append("grid must have 3-5 points")
    if not (0 < c < len(g) - 1):
        why.append("centre needs a neighbour on both sides")
    if not g[c] > 0:
        why.append("centre Net <= 0")
    for i in (c - 1, c + 1):
        if 0 <= i < len(g) and not (g[i] > 0 and g[i] >= 0.5 * g[c]):
            why.append(f"neighbour {i} = {g[i]:+.4f} not > 0 and >= 50% of centre")
    return dict(passed=not why, reasons=why)


def perturb(strategy, params, specs, n=200, seed=0, *, symbols, period, scenario="A", **run_kw):
    """E4. specs = {param: r} (r = 0.10 or 0.20). Each draw multiplies EVERY listed param at once by
    (1 + U(-r, r)) (ints rounded, >= 1). Pass iff share of draws with mean Net > 0 is >= 80% and median(draw mean) /
    base mean >= 0.5. Draws with no trades count as Net <= 0. Measurement only - never pick values from the draws."""
    rng = random.Random(seed)
    T0 = run(strategy, params, symbols, period, scenario, **run_kw)
    base = st.mean(t["net"] for t in T0) if T0 else None
    draws = []
    for _ in range(n):
        p = dict(params)
        for k, r in specs.items():
            v = params[k] * (1 + rng.uniform(-r, r))
            p[k] = max(1, round(v)) if isinstance(params[k], int) and not isinstance(params[k], bool) else v
        T = run(strategy, p, symbols, period, scenario, **run_kw)
        draws.append(dict(params=p, n=len(T), mean=st.mean(t["net"] for t in T) if T else None))
    vals = [d["mean"] for d in draws if d["mean"] is not None]
    share = sum(v > 0 for v in vals) / n
    ratio = st.median(vals) / base if vals and base and base > 0 else None
    return dict(base=base, n_base=len(T0), share_pos=share, median_ratio=ratio,
                passed=share >= 0.8 and ratio is not None and ratio >= 0.5, draws=draws)


def _days(T, scen):
    g = defaultdict(lambda: [0.0, 0])
    for t in T:
        g[t["day"]][0] += t["net_R"][scen]
        g[t["day"]][1] += 1
    return g


def p_mean_gt0(trades, scen="A", iters=2000, seed=SEED):
    """One-sided p for H0: mean net <= 0. UTC-day block bootstrap under the null: every trade is centred
    (net - observed mean), days resampled with replacement, mean* = sum/count of the drawn days;
    p = (1 + #{mean* >= observed}) / (iters + 1)."""
    m = st.mean(t["net_R"][scen] for t in trades)
    g = _days(trades, scen)
    S = [s - c * m for s, c in g.values()]
    C = [c for _, c in g.values()]
    D, rnd, hit = len(S), random.Random(seed), 0
    for _ in range(iters):
        idx = [rnd.randrange(D) for _ in range(D)]
        hit += sum(S[i] for i in idx) / sum(C[i] for i in idx) >= m
    return (1 + hit) / (iters + 1)


def p_delta(trades_a, trades_b, scen="A", iters=2000, seed=SEED):
    """One-sided p for H0: mean_net(a) - mean_net(b) <= 0 (e.g. a = config with the part, b = without).
    Paired UTC-day block bootstrap: days = union of both configs' signal days; each draw resamples days with
    replacement and recomputes BOTH means on the same days, so trades/days the two configs share move together and
    cancel to first order - the spread comes from the days where their trades differ. Shift method:
    p = (1 + #{delta* - delta >= delta}) / (iters + 1); draws where either side has no trade are counted as not
    extreme. -> (delta, p)."""
    ga, gb = _days(trades_a, scen), _days(trades_b, scen)
    days = sorted(set(ga) | set(gb))
    A = [ga.get(d, (0.0, 0)) for d in days]
    B = [gb.get(d, (0.0, 0)) for d in days]
    delta = sum(s for s, _ in A) / sum(c for _, c in A) - sum(s for s, _ in B) / sum(c for _, c in B)
    D, rnd, hit = len(days), random.Random(seed), 0
    for _ in range(iters):
        idx = [rnd.randrange(D) for _ in range(D)]
        ca, cb = sum(A[i][1] for i in idx), sum(B[i][1] for i in idx)
        if ca and cb:
            hit += sum(A[i][0] for i in idx) / ca - sum(B[i][0] for i in idx) / cb - delta >= delta
    return delta, (1 + hit) / (iters + 1)
