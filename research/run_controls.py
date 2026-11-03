"""W6: run C1/C2 on EXPLORE (never validation/holdout) at 1h and 15m + matched C0/C0-beta (200 seeds); write report + trades."""
import json, os, statistics as st, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runner as R, controls as C

SYMS = ["BTC", "ETH", "SOL", "SUI", "XRP"]
HERE = os.path.dirname(os.path.abspath(__file__))
f = lambda x, d=3: "-" if x is None else f"{x:.{d}f}"
HEAD = ["# Controls C1/C2 — EXPLORE only (2023-06-01..2025-03-01)", "",
     "Catalog 02 §3, X0 exit, scenario A/B/C. CI = wider of percentile-bootstrap and UTC-day block CI (95%). Funding unavailable (fund_R=0). "
     "Labels: CI excludes 0 -> 'sig +/-', else 'CI includes 0'. C0 pctl = 95th of 200 per-seed mean Net_A (190th rank).", ""]
L = []
res = {}
for name, fn in (("c1", C.c1), ("c2", C.c2)):
    for tf in ("1h", "15m"):
        if len(sys.argv) > 2 and (name, tf) != tuple(sys.argv[1:3]):
            continue
        T = R.run(fn, {"tf": tf}, SYMS, R.EXPLORE, "A")
        json.dump(dict(info=T.info, trades=list(T)), open(f"{HERE}/data/results/controls_{name}_{tf}_explore.json", "w"))
        S = {s: R.summary(T, s) for s in "ABC"}
        data = {s: R.load(s, R.EXPLORE) for s in SYMS}
        bf = R.x0_barrier_fn(tf)
        c0 = {}
        for beta in (False, True):
            runs = {}  # one seed at a time, keep only the mean (memory: 15m has ~40k trades/seed)
            for sd_ in range(200):
                r = R.random_entries(T, data, [sd_], bf, tf=tf, beta=beta)[sd_]
                runs[sd_] = (st.mean(t["net"] for t in r) if r else None, sum(r.info["shortfall"].values()))
            m = sorted(v[0] for v in runs.values() if v[0] is not None)
            c0[beta] = dict(mean=st.mean(m), sd=st.pstdev(m), p95=m[189], p5=m[9], min=m[0], max=m[-1],
                            shortfall=sum(v[1] for v in runs.values()))
        a = S["A"]
        L += [f"## {name.upper()} @ {tf}", "", f"n={a['n']}  info={T.info}", "",
              "| scen | gross | cost | Net mean | CI (wider) | label |", "|---|---|---|---|---|---|"]
        for s in "ABC":
            x = S[s]
            ci = x.get("ci")
            lab = "n<30" if ci is None else "sig +" if ci[0] > 0 else "sig -" if ci[1] < 0 else "CI includes 0"
            L.append(f"| {s} | {f(x.get('gross'))} | {f(x.get('cost'))} | {f(x.get('mean'))} | "
                     f"{'-' if ci is None else f'[{ci[0]:.3f}, {ci[1]:.3f}]'} | {lab} |")
        L += ["", f"Win rate (net_A>0) {f(a.get('win'))}, Wilson [{f(a['win_ci'][0])}, {f(a['win_ci'][1])}], breakeven WR {f(a['breakeven_wr'])}; exits {a['exits']}", "",
              "| side/symbol | n | Net_A mean |", "|---|---|---|"]
        for k in ("by_direction", "by_symbol"):
            L += [f"| {g} | {v['n']}{' (n<30)' if v['small'] else ''} | {f(v['mean'])} |" for g, v in a[k].items()]
        L += ["", "| C0 | mean | SD | 5th | 95th | min | max | shortfall slots |", "|---|---|---|---|---|---|---|---|"]
        for beta, v in c0.items():
            L.append(f"| {'C0-beta' if beta else 'C0'} | {f(v['mean'])} | {f(v['sd'])} | {f(v['p5'])} | {f(v['p95'])} | {f(v['min'])} | {f(v['max'])} | {v['shortfall']} |")
        L.append("")
        res[(name, tf)] = (a["n"], a["mean"], a.get("ci"), c0[False]["p95"], c0[True]["p95"])
        print(name, tf, res[(name, tf)], flush=True)
        open(f"{HERE}/reports/_controls_{name}_{tf}.part", "w").write("\n".join(L))
        L = []
parts = [f"{HERE}/reports/_controls_{n}_{t}.part" for n in ("c1", "c2") for t in ("1h", "15m")]
if all(os.path.exists(p) for p in parts):
    open(f"{HERE}/reports/controls_explore.md", "w").write("\n".join(HEAD + [open(p).read() for p in parts]))
