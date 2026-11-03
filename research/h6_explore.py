"""H6 EXPLORE build loop, step 0 (master §4.5.2, catalog §4.6): core N grid {10,20,40} -> plateau -> C0/C0-beta
(200 seeds each, matched to the centre) -> kill criteria ①④⑤⑥⑦⑧ (H1's, ③ n/a). EXPLORE only: runner refuses
VALIDATION/HOLDOUT without open_* flags, never passed here. Writes research/data/results/H6_trials.json.
Steps 1+ (E2 add-one ... E8) run only if the core survives; they are not implemented because it does not (see report).
C0 runs one seed at a time per worker and keeps only the per-seed means (memory)."""
import json, os, statistics as st, sys
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hyp_h6 as H  # noqa: E402
import runner as R  # noqa: E402
from h1_explore import FOLDS, SYM, pctl, slim  # noqa: E402  same folds / C0 percentile / trade slimming as H1

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "results")


def _c0(args):
    seeds, beta, ref = args
    data = {s: R.load(s, R.EXPLORE) for s in SYM}
    bf, out = R.x0_barrier_fn("1h"), {}
    for sd in seeds:
        T = R.random_entries(ref, data, [sd], bf, tf="1h", beta=beta)[sd]
        out[sd] = dict(mean={c: st.mean(t["net_R"][c] for t in T) for c in "ABC"}, n=len(T),
                       shortfall=sum(T.info["shortfall"].values()))
    return out


def c0_dist(ref, beta):
    ref = [dict(symbol=t["symbol"], month=t["month"], direction=t["direction"]) for t in ref]  # only what C0 reads
    out = {}
    with Pool(4) as p:
        for d in p.map(_c0, [(list(range(i, 200, 4)), beta, ref) for i in range(4)]):
            out.update(d)
    return out


def main():
    grid = H.GRIDS["N"]["grid"]
    runs, trials = {}, []
    for n in grid:
        T = R.run(H.strategy, {"N": n}, SYM, R.EXPLORE)
        runs[n] = T
        trials.append(dict(id=f"core_N={n}", step="0 core grid", params=dict(H.DEFAULTS, N=n), info=T.info,
                           summary={c: R.summary(T, c) for c in "ABC"}, trades=slim(T)))
        print(n, T.info, flush=True)
    center = runs[H.GRIDS["N"]["center"]]
    res = dict(grid=grid, grid_n=[len(runs[n]) for n in grid],
               **{f"grid_{c}": [st.mean(t["net_R"][c] for t in runs[n]) for n in grid] for c in "ABC"},
               grid_gross=[st.mean(t["gross_R"] for t in runs[n]) for n in grid])
    res["plateau"] = {c: R.plateau_check(res[f"grid_{c}"], 1) for c in "ABC"}
    res["p_mean_gt0"] = {c: R.p_mean_gt0(center, c) for c in "ABC"}
    res["mde"] = R.mde(len(center))
    c0 = {}
    for k, beta in (("c0", False), ("c0b", True)):
        c0[k] = c0_dist(center, beta)
        m = {c: [d["mean"][c] for d in c0[k].values()] for c in "ABC"}
        res[k] = dict(p95={c: pctl(m[c], 0.95) for c in "ABC"}, mean={c: st.mean(m[c]) for c in "ABC"},
                      sd={c: st.pstdev(m[c]) for c in "ABC"}, shortfall=sum(d["shortfall"] for d in c0[k].values()))
        print(k, res[k], flush=True)
    res["wf"] = []
    for a, b in FOLDS:
        lo, hi = R._ms(a), R._ms(b)
        x = [t["net_R"]["A"] for t in center if lo <= t["signal_ts_ms"] < hi]
        res["wf"].append(dict(fold=f"{a}~{b}", n=len(x), mean=st.mean(x) if x else None))
    res["pbo"] = R.pbo_cscv([R.block_means(runs[n], R.EXPLORE, 16) for n in grid], 16)
    srs = [R.moments([t["net_R"]["A"] for t in runs[n]])[0] for n in grid]
    sr, sk, ku, nT = R.moments([t["net_R"]["A"] for t in center])
    res["dsr"] = dict(sr=sr, skew=sk, kurt=ku, T=nT, n_trials=len(trials), sr_var=st.pvariance(srs),
                      dsr=R.deflated_sharpe(sr, len(trials), sk, ku, nT, sr_var=st.pvariance(srs)))
    sA = trials[grid.index(H.GRIDS["N"]["center"])]["summary"]["A"]
    kill = {
        "1_c0": not (sA["mean"] > res["c0"]["p95"]["A"] and sA["mean"] > res["c0b"]["p95"]["A"]),
        "4_plateau": not res["plateau"]["A"]["passed"],
        "5_long_short": any(v["mean"] <= 0 for v in sA["by_direction"].values()),
        "6_symbols": sum(v["mean"] > 0 for v in sA["by_symbol"].values()) < 4,
        "7_mde": res["mde"] > 0.10,
        "8_pbo": res["pbo"]["pbo"] > 0.5,
    }
    res["kill"], res["killed"] = kill, any(kill.values())
    json.dump(dict(hypothesis="H6", period=R.EXPLORE, funding_unavailable=True, n_trials=len(trials), trials=trials,
                   results=res, c0_seed_means={k: {str(s): d["mean"] for s, d in v.items()} for k, v in c0.items()}),
              open(os.path.join(OUT, "H6_trials.json"), "w"), default=str)
    print(json.dumps(res, default=str, indent=1))
    if not res["killed"]:
        print("core survived step 0: steps 1+ are not implemented in this driver")


if __name__ == "__main__":
    main()
