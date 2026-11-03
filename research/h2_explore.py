"""H2 Stage 2 — EXPLORE-only build loop driver (master §4.5.2, catalog §4 H2). Writes research/data/results/H2_trials.json
({trials, report}; the report numbers feed research/reports/H2_explore.md). Never opens VALIDATION/HOLDOUT (runner defaults).
Usage: python3 research/h2_explore.py [c0_cache.json]"""
import json, math, os, random, statistics as st, sys
from collections import defaultdict
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runner as R  # noqa: E402
import hyp_h2 as H  # noqa: E402

SYMS = ["BTC", "ETH", "SOL", "SUI", "XRP"]
P = R.EXPLORE
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "results")
FOLDS = [("2023-12-01", "2024-03-01"), ("2024-03-01", "2024-06-01"), ("2024-06-01", "2024-09-01"),
         ("2024-09-01", "2024-12-01"), ("2024-12-01", "2025-03-01")]  # 6m train -> 3m test, 3m steps
HALVES = [("2023-06-01", "2024-04-15"), ("2024-04-15", "2025-03-01")]  # 50/50 by calendar
TRIALS = []


def mean(xs):
    xs = list(xs)
    return st.mean(xs) if xs else None


def slim(s):
    return {k: s.get(k) for k in ("n", "mean", "gross", "cost", "sd", "ci", "ci_pct", "ci_blk", "win", "win_ci",
                                  "breakeven_wr", "exits")}


def by_period(T, periods, scen="A"):
    out = []
    for a, b in periods:
        lo, hi = R._ms(a), R._ms(b)
        v = [t["net_R"][scen] for t in T if lo <= t["signal_ts_ms"] < hi]
        out.append(dict(period=(a, b), n=len(v), mean=mean(v)))
    return out


def as_scen(T, scen):
    """copy with net_R['A'] := net_R[scen] so runner.p_delta can compare A (H2) vs B (taker)."""
    return [dict(t, net_R={"A": t["net_R"][scen]}) for t in T]


def delta_ci(Ta, Tb, iters=2000, alpha=0.05, seed=R.SEED):
    """paired UTC-day block bootstrap percentile CI of mean_net(a) - mean_net(b) (same day-resampling as p_delta)."""
    ga, gb = R._days(Ta, "A"), R._days(Tb, "A")
    days = sorted(set(ga) | set(gb))
    A = [ga.get(d, (0.0, 0)) for d in days]
    B = [gb.get(d, (0.0, 0)) for d in days]
    rnd, v = random.Random(seed), []
    for _ in range(iters):
        idx = [rnd.randrange(len(days)) for _ in days]
        ca, cb = sum(A[i][1] for i in idx), sum(B[i][1] for i in idx)
        if ca and cb:
            v.append(sum(A[i][0] for i in idx) / ca - sum(B[i][0] for i in idx) / cb)
    v.sort()
    return v[int(alpha / 2 * len(v))], v[int((1 - alpha / 2) * len(v)) - 1]


def vol_pct_map(C):
    """ORTHO regime_vol_pct per 15m bar (mean (H-L)/C over 6 bars, mid-rank percentile within the last 72 means)."""
    nr = [(c[2] - c[3]) / c[4] for c in C]
    m = [None] * len(C)
    for i in range(5, len(C)):
        m[i] = sum(nr[i - 5:i + 1]) / 6
    out = {}
    for i in range(5 + 71, len(C)):
        w = m[i - 71:i + 1]
        x = m[i]
        out[C[i][0] + R.M15] = (sum(v < x for v in w) + 0.5 * sum(v == x for v in w)) / 72 * 100
    return out


def _c0(args):
    ref, seeds, beta = args
    data = {s: R.load(s, P) for s in SYMS}
    res = R.random_entries(ref, data, seeds, R.x0_barrier_fn("15m"), beta=beta, scenario="A")
    return {s: (mean(t["net_R"]["A"] for t in T), len(T), sum(T.info["shortfall"].values())) for s, T in res.items()}


def c0_dist(ref, beta):
    ref = [dict(symbol=t["symbol"], month=t["month"], direction=t["direction"]) for t in ref]
    chunks = [(ref, list(range(k, 200, 4)), beta) for k in range(4)]
    with Pool(4) as pool:
        out = {}
        for d in pool.map(_c0, chunks):
            out.update(d)
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    rep = {}
    # 0. core delta grid x fill model (conservative = judgement; touch = auxiliary sensitivity)
    runs = {}
    for fill in ("through", "touch"):
        for d in H.DELTA_GRID["grid"]:
            p = dict(H.CORE, delta=d, fill=fill)
            T = H.run_h2(p, SYMS, P)
            runs[(fill, d)] = T
            s = R.summary(T)
            TRIALS.append(dict(id=f"core_{fill}_d{d}", params=p, role="judgement" if fill == "through" else "aux",
                               info=T.info, summary=slim(s), p_gt0=R.p_mean_gt0(T),
                               by_direction=s["by_direction"], by_symbol=s["by_symbol"]))
    grid_net = [mean(t["net"] for t in runs[("through", d)]) for d in H.DELTA_GRID["grid"]]
    rep["plateau"] = dict(grid=H.DELTA_GRID["grid"], net=grid_net, **R.plateau_check(grid_net, 1))
    rep["plateau_touch"] = [mean(t["net"] for t in runs[("touch", d)]) for d in H.DELTA_GRID["grid"]]
    core = runs[("through", 0.25)]
    s = R.summary(core)
    rep["core"] = dict(slim(s), by_direction=s["by_direction"], by_symbol=s["by_symbol"], by_month=s["by_month"],
                       info=core.info, scen_means={c: mean(t["net_R"][c] for t in core) for c in "ABC"},
                       sl_in_fill_bar=sum(t["meta"]["sl_in_fill_bar"] for t in core),
                       mde=R.mde(len(core)), mde_nominal=R.mde(len(core), de=1.0),
                       wf_folds=by_period(core, FOLDS), halves=by_period(core, HALVES),
                       loso={x: mean(t["net"] for t in core if t["symbol"] != x) for x in SYMS})
    for d in ("long", "short"):
        sd = R.summary([t for t in core if t["direction"] == d])
        rep["core"][f"summary_{d}"] = slim(sd)
    # 1a. C2 taker baseline on the same signal set (hyp_h2.taker_strategy == controls.c2 tf=15m signals)
    tk = R.run(H.taker_strategy, H.CORE, SYMS, P, scenario="B")
    stk = R.summary(tk, "B")
    rep["taker"] = dict(slim(stk), scen_means={c: mean(t["net_R"][c] for t in tk) for c in "ABC"},
                        by_direction=stk["by_direction"], info=tk.info)
    TRIALS.append(dict(id="c2_taker_15m", role="control", summary=slim(stk)))
    tkB = as_scen(tk, "B")
    dlt, pd = R.p_delta(core, tkB)
    rep["paired_vs_taker_B"] = dict(delta=dlt, p=pd, ci=delta_ci(core, tkB))
    tkA = as_scen(tk, "A")
    d2, p2 = R.p_delta(core, tkA)
    rep["paired_vs_taker_A"] = dict(delta=d2, p=p2, ci=delta_ci(core, tkA))
    for d in ("long", "short"):
        a = [t for t in core if t["direction"] == d]
        b = [t for t in tkB if t["direction"] == d]
        rep["paired_vs_taker_B"][d] = R.p_delta(a, b)[0]
    # 2. fill / adverse selection, conservative + touch
    vmaps = {x: vol_pct_map(R.load(x, P)["15m"]) for x in SYMS}
    for fill in ("through", "touch"):
        a = H.adverse_selection(SYMS, P, dict(H.CORE, fill=fill))
        rows = a["rows"]
        sm = a["summary"]
        sm["by_symbol"] = {x: H.as_summary([r for r in rows if r["symbol"] == x]) for x in SYMS}
        sm["by_direction"] = {d: H.as_summary([r for r in rows if r["direction"] == d]) for d in ("long", "short")}
        terc = defaultdict(list)
        for r in rows:
            v = vmaps[r["symbol"]].get(r["decision_ts_ms"])
            terc["na" if v is None else "low(<33)" if v < 100 / 3 else "mid" if v < 200 / 3 else "high(>=67)"].append(r)
        sm["by_vol_tercile"] = {k: H.as_summary(v) for k, v in sorted(terc.items())}
        F = [r for r in rows if r["filled"]]
        sm["per_signal_econ"] = dict(h2_netA_unfilled0=mean(r["lim_net_A"] if r["filled"] else 0.0 for r in rows),
                                     taker_netB=mean(r["mkt_net_B"] for r in rows),
                                     filled_lim_minus_mkt_gross=mean(r["lim_gross"] - r["mkt_gross"] for r in F))
        sm["fill_latency"] = {k: sum(r["fill_latency"] == k for r in F) for k in (1, 2)}
        rep[f"as_{fill}"] = sm
    # 3. C0 / C0-beta (200 seeds each)
    cache = sys.argv[1] if len(sys.argv) > 1 else None
    if cache and os.path.exists(cache):
        c0 = json.load(open(cache))
    else:
        c0 = {"c0": c0_dist(core, False), "beta": c0_dist(core, True)}
        if cache:
            json.dump(c0, open(cache, "w"))
    m0 = mean(t["net"] for t in core)
    for k, dist in c0.items():
        v = sorted(x[0] for x in dist.values())
        rep[f"C0_{k}"] = dict(n_seeds=len(v), mean=st.mean(v), sd=st.pstdev(v), p95=v[189], p05=v[9],
                              h2_rank=sum(x < m0 for x in v), shortfall=sum(x[2] for x in dist.values()),
                              beats_p95=m0 > v[189])
    # 4. PBO over the judgement configs (+ touch as a second family), DSR for the frozen core
    M = [R.block_means(runs[("through", d)], P, S=32) for d in H.DELTA_GRID["grid"]]
    rep["pbo_judgement"] = R.pbo_cscv(M, S=16)
    rep["pbo_all6"] = R.pbo_cscv(M + [R.block_means(runs[("touch", d)], P, S=32) for d in H.DELTA_GRID["grid"]], S=16)
    srs = [R.moments([t["net"] for t in runs[k]])[0] for k in runs]
    sr, sk, ku, Tn = R.moments([t["net"] for t in core])
    rep["dsr"] = dict(sr=sr, skew=sk, kurt=ku, T=Tn, n_trials=len(runs), sr_var=st.pvariance(srs),
                      dsr=R.deflated_sharpe(sr, len(runs), sk, ku, Tn, sr_var=st.pvariance(srs)))
    rep["n_trials_configs"] = len(TRIALS)
    json.dump(dict(trials=TRIALS, report=rep), open(os.path.join(OUT, "H2_trials.json"), "w"), indent=1, default=str)
    print(json.dumps(rep, indent=1, default=str)[:20000])


if __name__ == "__main__":
    main()
