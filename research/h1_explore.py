"""H1 EXPLORE build loop, step 0/1 (master §4.5.2): core grid -> plateau -> C0/C0-beta (200 seeds) -> C1@1h ->
catalog H1 kill criteria. EXPLORE only (runner refuses VALIDATION/HOLDOUT without the open_* flags, never passed).
Writes research/data/results/H1_trials.json and a markdown fragment file for the report. Steps 2+ run only if
the core survives (they do not, see report)."""
import json, os, statistics as st, sys
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import controls  # noqa: E402
import harness as h  # noqa: E402
import hyp_h1 as H  # noqa: E402
import runner as R  # noqa: E402

SYM = ["BTC", "ETH", "SOL", "SUI", "XRP"]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "results")
FOLDS = [("2023-12-01", "2024-03-01"), ("2024-03-01", "2024-06-01"), ("2024-06-01", "2024-09-01"),
         ("2024-09-01", "2024-12-01"), ("2024-12-01", "2025-03-01")]  # 6m train -> 3m test, step 3m (catalog §1)


def _c0(args):
    seeds, beta, ref = args
    data = {s: R.load(s, R.EXPLORE) for s in SYM}
    res = R.random_entries(ref, data, seeds, R.x0_barrier_fn("1h"), tf="1h", beta=beta)
    return {sd: dict(mean={c: st.mean(t["net_R"][c] for t in T) for c in "ABC"}, n=len(T),
                     shortfall=sum(T.info["shortfall"].values())) for sd, T in res.items()}


def c0_dist(ref, beta):
    chunks = [(list(range(i, 200, 4)), beta, ref) for i in range(4)]
    with Pool(4) as p:
        out = {}
        for d in p.map(_c0, chunks):
            out.update(d)
    return out


def pctl(vals, q):
    v = sorted(vals)
    return v[min(len(v) - 1, int(q * len(v)))]  # 0.95 -> 190th of 200 (catalog §3)


def slim(T):
    return [dict(ts=t["signal_ts_ms"], sym=t["symbol"], dir=t["direction"], exit=t["exit"], gross=round(t["gross_R"], 5),
                 A=round(t["net_R"]["A"], 5), B=round(t["net_R"]["B"], 5), C=round(t["net_R"]["C"], 5)) for t in T]


def main():
    os.makedirs(OUT, exist_ok=True)
    trials, runs = [], {}
    for v in H.GRIDS["P_pull"]["grid"]:
        cfg = {"P_pull": v}
        T = R.run(H.strategy, cfg, SYM, R.EXPLORE)
        runs[v] = T
        trials.append(dict(id=f"core_P_pull={v}", step="0 core grid", params=dict(H.DEFAULTS, **cfg), info=T.info,
                           summary={c: R.summary(T, c) for c in "ABC"}, trades=slim(T)))
    grid_A = [st.mean(t["net_R"]["A"] for t in runs[v]) for v in H.GRIDS["P_pull"]["grid"]]
    center = runs[20]
    res = dict(grid=H.GRIDS["P_pull"]["grid"], grid_A=grid_A,
               grid_B=[st.mean(t["net_R"]["B"] for t in runs[v]) for v in runs],
               grid_C=[st.mean(t["net_R"]["C"] for t in runs[v]) for v in runs],
               grid_n=[len(runs[v]) for v in runs], plateau=R.plateau_check(grid_A, 2))
    res["p_mean_gt0"] = {c: R.p_mean_gt0(center, c) for c in "ABC"}
    res["mde"] = R.mde(len(center))
    # controls
    res["c0"] = c0_dist(center, False)
    res["c0b"] = c0_dist(center, True)
    for k in ("c0", "c0b"):
        res[k + "_p95"] = {c: pctl([d["mean"][c] for d in res[k].values()], 0.95) for c in "ABC"}
        res[k + "_mean"] = {c: st.mean(d["mean"][c] for d in res[k].values()) for c in "ABC"}
        res[k + "_shortfall"] = sum(d["shortfall"] for d in res[k].values())
    C1 = R.run(controls.c1, {"tf": "1h"}, SYM, R.EXPLORE)
    res["c1"] = {c: R.summary(C1, c) for c in "ABC"}
    res["c1_vs_core"] = {c: R.p_delta(center, C1, c) for c in "ABC"}
    # walk-forward test folds (fixed centre, no fitting)
    res["wf"] = []
    for a, b in FOLDS:
        lo, hi = R._ms(a), R._ms(b)
        x = [t["net_R"]["A"] for t in center if lo <= t["signal_ts_ms"] < hi]
        res["wf"].append(dict(fold=f"{a}~{b}", n=len(x), mean=st.mean(x) if x else None))
    # PBO over every evaluated config, DSR for the (frozen) centre
    M = [R.block_means(runs[v], R.EXPLORE, 16) for v in runs]
    res["pbo"] = R.pbo_cscv(M, 16)
    srs = [R.moments([t["net_R"]["A"] for t in runs[v]])[0] for v in runs]
    sr, sk, ku, n = R.moments([t["net_R"]["A"] for t in center])
    res["dsr"] = dict(sr=sr, skew=sk, kurt=ku, T=n, n_trials=len(trials), sr_var=st.pvariance(srs),
                      dsr=R.deflated_sharpe(sr, len(trials), sk, ku, n, sr_var=st.pvariance(srs)))
    # u: realised 1.5*ATR14%(1H) median per symbol (replaces catalog placeholder 0.0094; measurement, not a result)
    res["u_med"] = {}
    for s in SYM:
        C = R.load(s, R.EXPLORE)["1h"]
        a = h.wilder_atr(C)
        lo = R._ms(R.EXPLORE[0])
        res["u_med"][s] = st.median(1.5 * a[i] / C[i][4] for i in range(len(C)) if a[i] and C[i][0] >= lo)
    json.dump(dict(hypothesis="H1", period=R.EXPLORE, funding_unavailable=True, n_trials=len(trials),
                   trials=trials, results={k: v for k, v in res.items() if k not in ("c0", "c0b")},
                   c0_seed_means={k: {str(s): d["mean"] for s, d in res[k].items()} for k in ("c0", "c0b")}),
              open(os.path.join(OUT, "H1_trials.json"), "w"), default=str)
    print(json.dumps({k: v for k, v in res.items() if k not in ("c0", "c0b")}, default=str, indent=1)[:6000])


if __name__ == "__main__":
    main()
