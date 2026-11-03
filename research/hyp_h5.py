#!/usr/bin/env python3
"""H5 (track T2) — did ORTHO V4's logged entries beat random entries under identical exits and costs?
DIAGNOSTIC ONLY, never an adoption candidate (docs/v5/02_HYPOTHESIS_CATALOG.md H5, §3 C0/C0-beta, D3).

Reference = V4 logged trades (Entry/TP/SL/R Dist/Bars Limit as logged; gross R from PnL%, 01 §2.1), after the
Phase 1 integrity quarantine. --pooled adds V3 entries RESCORED with the V4 exit (no BE) as the secondary sample.
C0 = runner.random_entries (seeds 0..199): same (symbol, UTC month) counts, uniform 15m decision bar inside the
reference period, non-overlapping per symbol, direction Bernoulli(0.5) (C0-beta: cell long share). Barriers copy the
geometry of a reference trade of the same symbol drawn with replacement: u = R Dist/Entry, RR = |TP-Entry|/R Dist,
bars limit -> only timing and direction differ. Scored by harness.score (src/ortho_resolver, unmodified).
Costs: phase1_rescore.cost_R scenarios A/B/C on both sides. Funding is EXCLUDED on both sides (fund_R = 0): C0
funding would need per-trade rates; the omission is symmetric only to first order (direction-dependent carry).
Candles: OKX REST via harness.ReplayExchange -> research/data/ortho_replay/ (never research/data/holdout/).
Verdict (pre-stated): informative iff reference Net_A > C0 AND C0-beta 95th pct (190th of 200 seed means) AND
the wider (percentile, UTC-day block) 95% CI of (reference - C0 mean) excludes 0 for both; n<30 or C0 shortfall
>5% of slots -> undecidable.
Usage: python3 research/hyp_h5.py [--pooled]   (needs research/data/notion_all.csv)"""
import bisect, json, math, os, random, statistics as st, sys
from collections import Counter, defaultdict
from datetime import timezone
from statistics import NormalDist

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as h  # noqa: E402
import runner as R  # noqa: E402
import phase1_rescore as prp  # noqa: E402
from ortho_resolver import _parse_signaled_at  # noqa: E402  (harness put src/ on sys.path)

orp = prp.orp
SEEDS = range(200)
GEOM_SEED = 1_000_000  # geometry-pairing stream = random.Random(GEOM_SEED + seed), separate from timing
SCENS = ("A", "B", "C")
M15 = R.M15
CSV = os.path.join(h.DATA, "notion_all.csv")


# ── reference ───────────────────────────────────────────────────────
def _entry(t):
    return {k: t[k] for k in ("symbol", "direction", "signal_ts_ms", "entry", "tp", "sl", "r_dist", "bars_limit_15m")}


def load_reference(csv_path, cohorts=("V4",)):
    """-> (trades, info). V4 gross/exit = logged (R from PnL%, exit via Note/§2.5 inference); V3 trades carry
    rescore=True (gross filled by rescore()). Quarantined rows (orp.integrity_reasons) are excluded."""
    rows = [r for r in orp.load(csv_path) if r.get("Status") in ("WIN", "LOSS")]
    T, _ = prp.build_trades(rows, {s: {} for s in prp.SYMS})  # empty rates -> fund_R = 0 (excluded, see docstring)
    out, info = [], Counter()
    for t in T:
        if t["coh"] not in cohorts:
            continue
        if t["reasons"]:
            info[f"quarantined_{t['coh']}"] += 1
            continue
        r = t["row"]
        dt = _parse_signaled_at(r["Signaled At (ISO)"])
        e, rd, tp = r["entry"], r["rdist"], float(r["TP"])
        out.append(dict(
            cohort=t["coh"], symbol=t["sym"], direction=t["dir"].lower(), signal_ts_ms=int(dt.timestamp() * 1000),
            day=dt.astimezone(timezone.utc).date().isoformat(), month=dt.astimezone(timezone.utc).strftime("%Y-%m"),
            entry=e, tp=tp, sl=float(r["SL"]), r_dist=rd, bars_limit_15m=int(float(r["Bars Limit"])),
            u=rd / e, rr=abs(tp - e) / rd, exit=t["x"], inferred=t["inferred"], gross_R=t["gross"],
            cost_R=t["cost"], net_R={s: t["gross"] - t["cost"][s] for s in SCENS}, rescore=t["coh"] != "V4"))
        info[f"n_{t['coh']}"] += 1
    return out, dict(info)


def rescore(ref, ex, tol=0.01):
    """V3 (rescore=True): replace gross/exit/costs by the V4 exit replayed (no BE). V4: report-only agreement of the
    replay with the logged gross R. -> info dict. Unresolved V3 entries are dropped (counted)."""
    info, keep = Counter(), []
    for t in ref:
        tr = R._score(ex, _entry(t), "A")
        if t["rescore"]:
            if tr is None:
                info["v3_unresolved"] += 1
                continue
            t.update(gross_R=tr["gross_R"], exit=tr["exit"], cost_R=tr["cost_R"], net_R=tr["net_R"])
        else:
            info["v4_replayed"] += 1
            info["v4_match"] += tr is not None and abs(tr["gross_R"] - t["gross_R"]) < tol
        keep.append(t)
    ref[:] = keep
    return dict(info)


# ── candles (OKX REST, cached in research/data/ortho_replay) ────────
class MemExchange:
    """Serves whole-period series fetched once; same fetch_ohlcv semantics as harness.CachedExchange."""

    def __init__(self, series):
        self.series = series  # {(sym, tf): (ts, rows)}

    def _load(self, sym, tf):
        return self.series[(sym, tf)]

    def fetch_ohlcv(self, symbol, timeframe="5m", since=None, limit=None, params=None):
        ts, rows = self.series[(h._base(symbol), h._tf(timeframe))]
        return [list(c) for c in h._window(ts, rows, since, limit)]


def period(ref):
    """[first reference entry, last reference time-limit end] on the 15m grid."""
    s = min(t["signal_ts_ms"] for t in ref)
    e = max(t["signal_ts_ms"] + t["bars_limit_15m"] * M15 for t in ref)
    return s - s % M15, -(-e // M15) * M15


def fetch(ref, ex=None):
    """-> (MemExchange, gaps). One ReplayExchange call per symbol x TF covering the period (+1 bar before, +2 after)."""
    ex = ex or h.ReplayExchange()
    s, e = period(ref)
    series, gaps = {}, {}
    for sym in sorted({t["symbol"] for t in ref}):
        for tf in ("5m", "15m"):
            dur = h.TF_MS[tf]
            rows = ex.fetch_ohlcv(sym, tf, since=s - M15, limit=(e - s + 3 * M15) // dur)
            ts = [r[0] for r in rows]
            gaps[f"{sym}_{tf}"] = sum(b - a != dur for a, b in zip(ts, ts[1:]))
            series[(sym, tf)] = (ts, rows)
    return MemExchange(series), gaps


def make_data(ex, syms, start, end):
    """{sym: runner.Data} with data['15m'] pre-filled (bars opening in [start-15m, end-15m])."""
    out = {}
    for sym in syms:
        ts, rows = ex._load(sym, "15m")
        d = R.Data(ex, sym, start, end, M15)
        d["15m"] = [tuple(r) for r in rows[bisect.bisect_left(ts, start - M15):bisect.bisect_right(ts, end - M15)]]
        out[sym] = d
    return out


# ── C0 ──────────────────────────────────────────────────────────────
def geom_barrier_fn(geoms_by_candles, rng):
    """barrier_fn for runner.random_entries: entry = open of bar i+1, geometry (u, rr, bars) drawn with replacement
    from the reference trades of the symbol owning `candles`. Mirror-symmetric around entry by construction."""
    def fn(C, i, d):
        if i + 1 >= len(C) or C[i + 1][0] != C[i][0] + M15:  # entry bar must be the next 15m bar (no gap)
            return None
        u, rr, bl = rng.choice(geoms_by_candles[id(C)])
        e, s = C[i + 1][1], 1 if d == "long" else -1
        r = u * e
        return dict(entry=e, sl=e - s * r, tp=e + s * rr * r, r_dist=r, bars_limit_15m=bl)
    return fn


def c0(ref, data, seeds=SEEDS, beta=False):
    """-> {seed: Trades}. One random_entries call per seed so the geometry stream is seeded per seed too."""
    geoms = defaultdict(list)
    for t in ref:
        geoms[t["symbol"]].append((t["u"], t["rr"], t["bars_limit_15m"]))
    by_c = {id(data[s]["15m"]): g for s, g in geoms.items()}
    out = {}
    for seed in seeds:
        out.update(R.random_entries(ref, data, [seed], geom_barrier_fn(by_c, random.Random(GEOM_SEED + seed)),
                                    beta=beta))
    return out


# ── statistics ──────────────────────────────────────────────────────
def val(key):
    return (lambda t: t["gross_R"]) if key == "gross" else (lambda t: t["net_R"][key])


def dist(ref_mean, seed_means):
    """Reference mean vs C0 seed-mean distribution. p95 = 190th of 200 (ceil(.95 N)-th order statistic)."""
    sv = sorted(seed_means)
    mu, sd = st.mean(sv), st.stdev(sv)
    z = (ref_mean - mu) / sd if sd > 0 else None
    return dict(ref=ref_mean, c0_mean=mu, c0_sd=sd, c0_p05=sv[max(0, math.ceil(.05 * len(sv)) - 1)],
                c0_p95=sv[math.ceil(.95 * len(sv)) - 1], pct=100 * sum(x < ref_mean for x in sv) / len(sv),
                z=z, p_norm=None if z is None else 1 - NormalDist().cdf(z))


def diff(ref, c0_mean, f):
    """Reference per-trade value minus the C0 grand mean: mean, wider of percentile / UTC-day block 95% CI,
    one-sided day-block p (runner.p_mean_gt0)."""
    vals = [f(t) - c0_mean for t in ref]
    days = [t["day"] for t in ref]
    cp, cb = orp.bootstrap_ci(vals), prp.block_ci(vals, days)
    p = R.p_mean_gt0([dict(day=d, net_R={"x": v}) for d, v in zip(days, vals)], "x")
    return dict(mean=st.mean(vals), ci_pct=cp, ci_blk=cb, ci=(min(cp[0], cb[0]), max(cp[1], cb[1])), p=p)


def compare(ref, runs, keys=("gross",) + SCENS):
    out = {}
    for k in keys:
        f = val(k)
        d = dist(st.mean(map(f, ref)), [st.mean(map(f, T)) for T in runs.values()])
        d["diff"] = diff(ref, d["c0_mean"], f)
        out[k] = d
    return out


def by_direction(ref, runs, keys=("gross", "A")):
    out = {}
    for side in ("long", "short"):
        r = [t for t in ref if t["direction"] == side]
        sub = {s: [t for t in T if t["direction"] == side] for s, T in runs.items()}
        out[side] = dict(n=len(r), small=len(r) < prp.MIN_N, c0_n_mean=st.mean(map(len, sub.values())))
        if r and all(sub.values()):
            out[side].update({k: dist(st.mean(map(val(k), r)), [st.mean(map(val(k), T)) for T in sub.values()])
                              for k in keys})
    return out


def analyse(ref, runs_c0, runs_beta):
    slots = len(ref) * len(runs_c0)
    short = {name: sum(sum(T.info["shortfall"].values()) for T in runs.values())
             for name, runs in (("C0", runs_c0), ("C0b", runs_beta))}
    res = dict(n=len(ref), mde=R.mde(len(ref), de=1), mde_de15=R.mde(len(ref)), seeds=len(runs_c0),
               shortfall={k: dict(n=v, rate=v / slots) for k, v in short.items()},
               ref_long_share=sum(t["direction"] == "long" for t in ref) / len(ref),
               ref_exits=dict(Counter(t["exit"] for t in ref)),
               C0=compare(ref, runs_c0), C0b=compare(ref, runs_beta),
               dir_C0=by_direction(ref, runs_c0), dir_C0b=by_direction(ref, runs_beta))
    res["verdict"] = verdict(res)
    return res


def verdict(res):
    if res["n"] < prp.MIN_N or max(s["rate"] for s in res["shortfall"].values()) > 0.05:
        return "판정 불가"
    ok = all(res[c]["A"]["ref"] > res[c]["A"]["c0_p95"] and res[c]["A"]["diff"]["ci"][0] > 0 for c in ("C0", "C0b"))
    return "ORTHO 진입 정보 있음" if ok else "ORTHO 진입 정보 없음"


# ── main ────────────────────────────────────────────────────────────
def main(argv=()):
    pooled = "--pooled" in argv
    if not os.path.exists(CSV):
        print(f"stage 2 pending: {CSV} not found")
        return 2
    if h.oc.BE_TRIGGER_R:
        raise h.HarnessError("BE active in ortho_config (ORTHO_V4_ENABLED=false?) - V4 exits have no BE")
    ref, info = load_reference(CSV, ("V3", "V4") if pooled else ("V4",))
    ex, gaps = fetch(ref)
    info.update(rescore(ref, ex))
    s, e = period(ref)
    data = make_data(ex, sorted({t["symbol"] for t in ref}), s, e)
    res = analyse(ref, c0(ref, data), c0(ref, data, beta=True))
    res.update(info=info, gaps=gaps, period_ms=(s, e), pooled=pooled)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports",
                       f"H5_results{'_pooled' if pooled else ''}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(res, f, indent=1, ensure_ascii=False, default=str)
    a, b = res["C0"]["A"], res["C0b"]["A"]
    print(f"{res['verdict']} | n={res['n']} ref Net_A={a['ref']:+.4f} C0 p95={a['c0_p95']:+.4f} "
          f"C0b p95={b['c0_p95']:+.4f} pct={a['pct']:.1f} diff CI={a['diff']['ci']} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
