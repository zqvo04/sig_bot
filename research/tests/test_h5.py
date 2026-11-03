import csv, os, random, sys, tempfile, unittest
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import hyp_h5 as H
import phase1_rescore as prp
import runner as R

T0 = R._ms("2026-08-19")
N5 = (R._ms("2026-09-14") - T0) // R.M5
SYMS = ("BTC", "SOL")


def walk(seed, p0):
    rng, p, out = random.Random(seed), p0, []
    for i in range(N5):
        o, path = p, [p]
        for _ in range(5):
            p *= 1 + rng.uniform(-0.001, 0.001)
            path.append(p)
        out.append([T0 + i * R.M5, o, max(path), min(path), p, 1.0])
    return out


def agg15(rows):
    return [[g[0][0], g[0][1], max(c[2] for c in g), min(c[3] for c in g), g[-1][4], sum(c[5] for c in g)]
            for g in (rows[i:i + 3] for i in range(0, len(rows) - 2, 3))]


def mem_exchange():
    series = {}
    for k, sym in enumerate(SYMS):
        r5 = walk(k, 60000.0 if sym == "BTC" else 150.0)
        for tf, rows in (("5m", r5), ("15m", agg15(r5))):
            series[(sym, tf)] = ([r[0] for r in rows], rows)
    return H.MemExchange(series)


def reference(seed=7, flip=False, all_long=False):
    """synthetic reference: per symbol 12 trades in Aug and 10 in Sep (counts only matter), varied geometry."""
    rng, out = random.Random(seed), []
    for sym in SYMS:
        for month, (a, b), n in (("2026-08", ("2026-08-20", "2026-09-01"), 12), ("2026-09", ("2026-09-01", "2026-09-12"), 10)):
            lo, hi = R._ms(a), R._ms(b)
            for _ in range(n):
                t0 = lo + rng.randrange((hi - lo) // R.M15) * R.M15
                d = "long" if all_long or rng.random() < 0.7 else "short"
                if flip:
                    d = "short" if d == "long" else "long"
                g = rng.uniform(-1, 1.5)
                out.append(dict(symbol=sym, month=month, direction=d, signal_ts_ms=t0, day=f"{month}-{rng.randrange(1, 29):02d}",
                                u=rng.choice((0.004, 0.006)), rr=rng.choice((1.0, 2.0)),
                                bars_limit_15m=rng.choice((4, 8)), gross_R=g, net_R={s: g - 0.1 for s in "ABC"},
                                exit="TP"))
    return out


class TestC0(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ex = mem_exchange()
        cls.ref = reference()
        cls.s, cls.e = H.period(cls.ref)
        cls.data = H.make_data(cls.ex, SYMS, cls.s, cls.e)

    def run_c0(self, seeds, ref=None, beta=False):
        return H.c0(ref or self.ref, self.data, seeds, beta=beta)

    def key(self, T):
        return [(t["symbol"], t["signal_ts_ms"], t["direction"], t["entry"], t["tp"], t["sl"], t["bars_limit_15m"]) for t in T]

    def test_reproducible_by_seed(self):
        a, b, c = self.run_c0([3])[3], self.run_c0([3])[3], self.run_c0([4])[4]
        self.assertEqual(self.key(a), self.key(b))
        self.assertNotEqual(self.key(a), self.key(c))

    def test_non_overlap_and_monthly_counts(self):
        want = Counter((t["symbol"], t["month"]) for t in self.ref)
        for seed, T in self.run_c0([0, 1, 2]).items():
            self.assertEqual(T.info["shortfall"], {})
            self.assertEqual(Counter((t["symbol"], t["month"]) for t in T), want)
            for sym in SYMS:
                L = sorted((t for t in T if t["symbol"] == sym), key=lambda t: t["signal_ts_ms"])
                for a, b in zip(L, L[1:]):
                    self.assertGreaterEqual(b["signal_ts_ms"], a["exit_ts_ms"])
                for t in L:  # inside the reference period, embargo respected
                    self.assertGreaterEqual(t["signal_ts_ms"], self.s)
                    self.assertLessEqual(t["signal_ts_ms"] + t["bars_limit_15m"] * R.M15, self.e)

    def test_geometry_preserved(self):
        geoms = {sym: {(t["u"], t["rr"], t["bars_limit_15m"]) for t in self.ref if t["symbol"] == sym} for sym in SYMS}
        for T in self.run_c0([5]).values():
            for t in T:
                u = t["r_dist"] / t["entry"]
                rr = abs(t["tp"] - t["entry"]) / t["r_dist"]
                self.assertAlmostEqual(abs(t["entry"] - t["sl"]), t["r_dist"], delta=1e-9 * t["entry"])
                self.assertTrue(any(abs(u - g[0]) < 1e-12 and abs(rr - g[1]) < 1e-9 and t["bars_limit_15m"] == g[2]
                                    for g in geoms[t["symbol"]]), t)
                # entry = open of the 15m bar starting at the signal time
                ts, rows = self.ex._load(t["symbol"], "15m")
                self.assertEqual(t["entry"], rows[ts.index(t["signal_ts_ms"])][1])

    def test_mirror_barriers(self):
        C = self.data["BTC"]["15m"]
        g = {id(C): [(0.005, 1.7, 8)]}
        lo = H.geom_barrier_fn(g, random.Random(1))(C, 100, "long")
        sh = H.geom_barrier_fn(g, random.Random(1))(C, 100, "short")
        self.assertEqual(lo["entry"], sh["entry"])
        self.assertEqual(lo["r_dist"], sh["r_dist"])
        self.assertAlmostEqual(lo["tp"] - lo["entry"], sh["entry"] - sh["tp"], places=9)
        self.assertAlmostEqual(lo["entry"] - lo["sl"], sh["sl"] - sh["entry"], places=9)
        self.assertTrue(lo["sl"] < lo["entry"] < lo["tp"] and sh["tp"] < sh["entry"] < sh["sl"])

    def test_direction_symmetry(self):
        c0 = [t["direction"] for T in self.run_c0(range(5)).values() for t in T]
        self.assertTrue(0.4 < c0.count("long") / len(c0) < 0.6)  # C0: Bernoulli(0.5), not the ref's 70% long
        up = self.run_c0([0], ref=reference(all_long=True), beta=True)[0]
        dn = self.run_c0([0], ref=reference(all_long=True, flip=True), beta=True)[0]
        self.assertTrue(all(t["direction"] == "long" for t in up))
        self.assertTrue(all(t["direction"] == "short" for t in dn))


class TestStats(unittest.TestCase):
    def test_p95_is_190th_of_200(self):
        d = H.dist(189.5, list(range(200)))
        self.assertEqual(d["c0_p95"], 189)
        self.assertEqual(d["pct"], 95.0)

    def test_analyse_smoke(self):
        ex, ref = mem_exchange(), reference()
        s, e = H.period(ref)
        data = H.make_data(ex, SYMS, s, e)
        res = H.analyse(ref, H.c0(ref, data, range(20)), H.c0(ref, data, range(20), beta=True))
        self.assertEqual(res["n"], len(ref))
        self.assertIn(res["verdict"], ("ORTHO 진입 정보 있음", "ORTHO 진입 정보 없음", "판정 불가"))
        a = res["C0"]["A"]
        self.assertAlmostEqual(a["diff"]["mean"], a["ref"] - a["c0_mean"], places=12)
        self.assertLessEqual(a["diff"]["ci"][0], a["diff"]["mean"])


class TestLoadReference(unittest.TestCase):
    def test_v4_row(self):
        cols = ["Signal", "Symbol", "Direction", "Status", "Strategy ID", "Entry", "SL", "TP", "R Dist", "RR",
                "Bars Limit", "Bars To Exit", "PnL %", "Note", "Signaled At", "Signaled At (ISO)", "Resolved At"]
        rows = [["s1", "SOL/USDT", "SHORT", "WIN", "ORTHO-4.SIM0", "200", "202", "197", "2", "1.5", "8", "3", "1.5",
                 "R=+1.50 | TP | MFE1.6", "2026년 9월 1일 오전 9:15", "2026-09-01T00:15:00+00:00", "2026년 9월 1일 오전 10:00"],
                ["s2", "BTC/USDT", "LONG", "LOSS", "", "60000", "59700", "60600", "300", "2", "8", "2", "-0.5",
                 "", "2026년 7월 1일 오전 9:15", "2026-07-01T00:15:00+00:00", ""]]
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "n.csv")
            with open(p, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f); w.writerow(cols); w.writerows(rows)
            ref, info = H.load_reference(p)
            both, _ = H.load_reference(p, ("V3", "V4"))
        self.assertEqual(len(ref), 1)
        self.assertEqual(len(both), 2)
        t = ref[0]
        self.assertEqual((t["symbol"], t["direction"], t["month"], t["day"], t["exit"]), ("SOL", "short", "2026-09", "2026-09-01", "TP"))
        self.assertEqual(t["signal_ts_ms"], R._ms("2026-09-01") + 15 * 60_000)
        self.assertAlmostEqual(t["gross_R"], 1.5)
        self.assertAlmostEqual(t["u"], 0.01)
        self.assertAlmostEqual(t["rr"], 1.5)
        self.assertEqual(t["bars_limit_15m"], 8)
        self.assertAlmostEqual(t["net_R"]["A"], 1.5 - prp.cost_R(200, 2, "TP", "A", prp.TICK["SOL"]))
        self.assertFalse(t["rescore"])
        self.assertTrue(both[0]["rescore"] if both[0]["cohort"] == "V3" else both[1]["rescore"])


if __name__ == "__main__":
    unittest.main()
