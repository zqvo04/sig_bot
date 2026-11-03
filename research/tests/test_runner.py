import gzip, json, os, random, statistics as st, sys, tempfile, time, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import harness as h
import runner as R

P0 = 30000  # integer prices -> the 60000 - p mirror is exact in floating point
PERIOD = ("2023-06-01", "2023-09-01")  # inside EXPLORE
T_START = R._ms("2023-04-01")
N5 = (R._ms("2023-09-15") - T_START) // R.M5


def walk(seed):
    rng, p, out = random.Random(seed), P0, []
    for i in range(N5):
        o, path = p, [p]
        for _ in range(5):
            p += rng.randint(-12, 12)
            path.append(p)
        out.append([T_START + i * R.M5, o, max(path), min(path), p, 1])
    return out


def agg15(rows):
    return [[g[0][0], g[0][1], max(c[2] for c in g), min(c[3] for c in g), g[-1][4], sum(c[5] for c in g)]
            for g in (rows[i:i + 3] for i in range(0, len(rows) - 2, 3))]


def write(root, sym, rows5):
    os.makedirs(os.path.join(root, "ohlcv"), exist_ok=True)
    for tf, rows in (("5m", rows5), ("15m", agg15(rows5))):
        with gzip.open(os.path.join(root, "ohlcv", f"{sym}_{tf}.csv.gz"), "wt") as f:
            f.write("ts_ms,open,high,low,close,vol,volCcy,volCcyQuote\n")
            f.writelines(",".join(map(str, r + [0, 0])) + "\n" for r in rows)


X0 = R.x0_barrier_fn("15m")


def coin_strategy(data, sym, params):
    """zero-edge: direction from a seeded coin per bar, a signal on every 15m bar."""
    C = data["15m"]
    out = []
    for i in range(len(C) - 1):
        d = "long" if random.Random(f"{params['seed']}-{sym}-{C[i][0]}").random() < 0.5 else "short"
        b = X0(C, i, d)
        if b:
            out.append(dict(b, symbol=sym, direction=d, signal_ts_ms=C[i][0] + R.M15))
    return out


def candle_strategy(data, sym, params):
    """mirror-symmetric: long after an up bar, short after a down bar."""
    C = data["15m"]
    out = []
    for i in range(len(C) - 1):
        if C[i][4] != C[i][1]:
            d = "long" if C[i][4] > C[i][1] else "short"
            b = X0(C, i, d)
            if b:
                out.append(dict(b, symbol=sym, direction=d, signal_ts_ms=C[i][0] + R.M15))
    return out


class Synthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = os.path.join(cls.tmp.name, "a")
        cls.mroot = os.path.join(cls.tmp.name, "mirror")
        cls.w = {"BTC": walk(1), "ETH": walk(2)}
        for s, rows in cls.w.items():
            write(cls.root, s, rows)
        write(cls.mroot, "BTC", [[r[0], 2 * P0 - r[1], 2 * P0 - r[3], 2 * P0 - r[2], 2 * P0 - r[4], r[5]]
                                 for r in cls.w["BTC"]])
        t = time.time()
        cls.T = R.run(coin_strategy, {"seed": 7}, ["BTC", "ETH"], PERIOD, data_dir=cls.root)
        cls.secs = time.time() - t

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_zero_edge_net_is_minus_cost(self):
        T = self.T
        n = len(T)
        self.assertGreater(n, 1000)
        g = [t["gross_R"] for t in T]
        se = st.pstdev(g) / n ** 0.5
        self.assertLess(abs(st.mean(g)), 4 * se)
        for t in T:
            self.assertAlmostEqual(t["net"], t["gross_R"] - t["cost_R"]["A"])
            self.assertEqual(t["fund_R"], 0.0)
            self.assertTrue(t["funding_unavailable"])
        s = R.summary(T)
        self.assertAlmostEqual(s["mean"], s["gross"] - s["cost"])
        self.assertGreater(s["cost"], 0)
        self.assertLessEqual(s["ci"][0], s["ci_pct"][0])
        self.assertEqual(set(s["by_direction"]), {"long", "short"})
        print(f"\n[runner timing] {n} trades in {self.secs:.2f}s -> {1000 * self.secs / n:.2f}s per 1,000 trades "
              f"(info {T.info})")

    def test_open_duplicate_skip(self):
        T, info = self.T, self.T.info
        self.assertGreater(info["skipped_open"], 0)
        self.assertEqual(info["emitted"], info["n"] + info["skipped_open"] + info.get("warmup_dropped", 0)
                         + info.get("embargoed", 0) + info.get("unresolved", 0))
        for sym in ("BTC", "ETH"):
            L = sorted((t for t in T if t["symbol"] == sym), key=lambda t: t["signal_ts_ms"])
            for a, b in zip(L, L[1:]):
                self.assertGreaterEqual(b["signal_ts_ms"], a["exit_ts_ms"])

    def test_period_bounds_and_embargo(self):
        s, e = R._ms(PERIOD[0]), R._ms(PERIOD[1])
        for t in self.T:
            self.assertGreaterEqual(t["signal_ts_ms"], s)
            self.assertLessEqual(t["signal_ts_ms"] + t["bars_limit_15m"] * R.M15, e)
        D = R.load("BTC", PERIOD, data_dir=self.root)
        for tf, ms in (("15m", R.M15), ("5m", R.M5)):
            self.assertLessEqual(D[tf][-1][0] + ms, e)
            self.assertEqual(D[tf][0][0], s - R.WARMUP_DAYS * R.DAY)

    def test_mirror_symmetry(self):
        a = R.run(candle_strategy, {}, ["BTC"], PERIOD, data_dir=self.root)
        b = R.run(candle_strategy, {}, ["BTC"], PERIOD, data_dir=self.mroot)
        self.assertGreater(len(a), 300)
        self.assertEqual(len(a), len(b))
        for x, y in zip(a, b):
            self.assertEqual(x["signal_ts_ms"], y["signal_ts_ms"])
            self.assertNotEqual(x["direction"], y["direction"])
            self.assertEqual(x["exit"], y["exit"])
            self.assertAlmostEqual(x["gross_R"], y["gross_R"], places=9)
        la = [t["gross_R"] for t in a if t["direction"] == "long"]
        sb = [t["gross_R"] for t in b if t["direction"] == "short"]
        self.assertAlmostEqual(sum(la), sum(sb), places=6)
        # cost_R takes no direction argument (direction-free by construction; mirrored prices differ in level only)

    def test_period_flags(self):
        for per, kw in ((R.VALIDATION, "open_validation"), (R.HOLDOUT, "open_holdout"),
                        (("2025-02-01", "2025-04-01"), "open_validation")):
            with self.assertRaises(h.HarnessError):
                R.run(coin_strategy, {"seed": 1}, ["BTC"], per, data_dir=self.root)
            with self.assertRaises(h.HarnessError):
                R.load("BTC", per, data_dir=self.root)
        log = os.path.join(self.root, "access.log")
        self.assertFalse(os.path.exists(log))
        T = R.run(coin_strategy, {"seed": 1}, ["BTC"], R.VALIDATION, open_validation=True, data_dir=self.root)
        self.assertEqual(len(T), 0)
        with self.assertRaises(h.HarnessError):  # validation flag does not open the holdout
            R.run(coin_strategy, {"seed": 1}, ["BTC"], ("2025-12-01", "2026-02-01"), open_validation=True,
                  data_dir=self.root)
        with open(log) as f:
            lines = [json.loads(x) for x in f]
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0]["validation"])

    def test_c0_random_entries(self):
        data = {s: R.load(s, PERIOD, data_dir=self.root) for s in ("BTC", "ETH")}
        ref = self.T[::4]
        out = R.random_entries(ref, data, [0, 1], X0)
        again = R.random_entries(ref, data, [0], X0)
        self.assertEqual([t["signal_ts_ms"] for t in out[0]], [t["signal_ts_ms"] for t in again[0]])
        self.assertNotEqual([t["signal_ts_ms"] for t in out[0]], [t["signal_ts_ms"] for t in out[1]])
        want = {}
        for t in ref:
            want[(t["symbol"], t["month"])] = want.get((t["symbol"], t["month"]), 0) + 1
        for seed, T in out.items():
            got = {}
            for t in T:
                got[(t["symbol"], t["month"])] = got.get((t["symbol"], t["month"]), 0) + 1
            self.assertEqual(got, want)
            self.assertEqual(T.info["shortfall"], {})
            for sym in ("BTC", "ETH"):
                L = sorted((t for t in T if t["symbol"] == sym), key=lambda t: t["signal_ts_ms"])
                for a, b in zip(L, L[1:]):
                    self.assertGreaterEqual(b["signal_ts_ms"], a["exit_ts_ms"])
        beta = R.random_entries([dict(t, direction="long") for t in ref], data, [3], X0, beta=True)[3]
        self.assertTrue(all(t["direction"] == "long" for t in beta))

    def test_pvalues(self):
        p = R.p_mean_gt0(self.T)
        self.assertGreater(p, 0.5)  # net = -cost < 0
        shifted = [dict(t, net_R={"A": t["net_R"]["A"] + 0.5}) for t in self.T]
        self.assertLess(R.p_mean_gt0(shifted), 0.01)
        d, p = R.p_delta(self.T, self.T)
        self.assertEqual(d, 0.0)
        self.assertGreater(p, 0.4)
        d, p = R.p_delta(shifted, self.T)
        self.assertAlmostEqual(d, 0.5)
        self.assertLess(p, 0.01)

    def test_block_means(self):
        row = R.block_means(self.T, PERIOD, S=16)
        self.assertEqual(len(row), 16)
        self.assertTrue(all(isinstance(v, float) for v in row))


class Stats(unittest.TestCase):
    def test_mde_matches_master_table(self):
        self.assertAlmostEqual(R.mde(1207, alpha=0.05, de=1.0), 0.10, places=3)
        self.assertAlmostEqual(R.mde(302, alpha=0.05, de=1.0), 0.20, places=2)

    def test_pbo_null_near_half(self):
        # one null matrix's PBO is very noisy (seeds 0..29 range 0.15-0.87), so test the mean over 10 matrices
        v = []
        for s in range(10):
            rng = random.Random(s)
            r = R.pbo_cscv([[rng.gauss(0, 1) for _ in range(64)] for _ in range(20)], S=16)
            v.append(r["pbo"])
        self.assertEqual(r["n_splits"], 12870)
        self.assertLess(abs(st.mean(v) - 0.5), 0.15)

    def test_pbo_true_edge_is_low(self):
        rng = random.Random(12)
        M = [[rng.gauss(0.8 if i == 0 else 0, 1) for _ in range(64)] for i in range(20)]
        self.assertLess(R.pbo_cscv(M)["pbo"], 0.1)

    def test_bh_fdr_null(self):
        rng = random.Random(0)
        self.assertEqual(sum(R.bh_fdr([rng.random() for _ in range(100)])), 0)
        any_disc = sum(any(R.bh_fdr([random.Random(s).random() for _ in range(100)])) for s in range(200))
        self.assertLessEqual(any_disc / 200, 0.15)  # under the global null BH controls FWER at q
        self.assertEqual(R.bh_fdr([0.001, 0.002, 0.9, 0.5]), [True, True, False, False])

    def test_dsr_best_of_null(self):
        rng = random.Random(5)
        trials = [R.moments([rng.gauss(0, 1) for _ in range(500)]) for _ in range(100)]
        best = max(trials)
        var = st.pvariance([t[0] for t in trials])
        self.assertLess(R.deflated_sharpe(best[0], 100, best[1], best[2], best[3], sr_var=var), 0.95)
        self.assertLess(R.deflated_sharpe(best[0], 100, best[1], best[2], best[3]), 0.95)
        self.assertGreater(R.deflated_sharpe(0.3, 1, 0, 3, 500), 0.95)  # a real single-trial edge passes

    def test_plateau(self):
        self.assertTrue(R.plateau_check([0.05, 0.10, 0.08], 1)["passed"])
        self.assertTrue(R.plateau_check([-0.2, 0.06, 0.10, 0.07, -0.1], 2)["passed"])
        self.assertFalse(R.plateau_check([0.04, 0.10, 0.08], 1)["passed"])  # spike: neighbour < 50%
        self.assertFalse(R.plateau_check([-0.01, 0.10, 0.08], 1)["passed"])
        self.assertFalse(R.plateau_check([0.10, 0.08, 0.07], 0)["passed"])  # edge centre
        self.assertFalse(R.plateau_check([0.05, -0.01, 0.05], 1)["passed"])


if __name__ == "__main__":
    unittest.main()
