import gzip, json, os, random, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import harness as h

T0 = 1704067200000  # 2024-01-01T00:00:00Z
ISO0 = "2024-01-01T00:00:00+00:00"
FIVE = 300_000


def bar(i, o, hi, lo, c):
    return [T0 + i * FIVE, o, hi, lo, c, 1.0]


def write_cache(root, sub, name, rows):
    os.makedirs(os.path.join(root, sub), exist_ok=True)
    with gzip.open(os.path.join(root, sub, name), "wt") as f:
        f.write("ts_ms,open,high,low,close,vol,volCcy,volCcyQuote\n")
        for r in rows:
            f.write(",".join(map(str, r + [0, 0])) + "\n")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def run_path(self, rows, direction, entry, tp, sl, bars_limit=1):
        write_cache(self.root, "ohlcv", "BTC_5m.csv.gz", rows)
        ex = h.CachedExchange(self.root)
        sig = dict(symbol="BTC/USDT", direction=direction, signaled_at=ISO0, entry=entry, tp=tp, sl=sl,
                   r_dist=abs(entry - sl), bars_limit=bars_limit)
        return h.score([sig], ex)[0]


class Outcomes(Base):
    def test_tp(self):
        r = self.run_path([bar(0, 100, 101, 99.5, 100.5), bar(1, 100.5, 102.1, 100, 102)], "long", 100, 102, 99)
        self.assertEqual((r["status"], r["exit_reason"], r["bars"]), ("WIN", "TP", 1))
        self.assertAlmostEqual(r["gross_r"], 2.0)

    def test_sl(self):
        r = self.run_path([bar(0, 100, 100.5, 98.9, 99)], "long", 100, 102, 99)
        self.assertEqual((r["status"], r["exit_reason"]), ("LOSS", "SL"))
        self.assertAlmostEqual(r["gross_r"], -1.0)

    def test_same_bar_tp_and_sl_is_loss(self):
        r = self.run_path([bar(0, 100, 102.5, 98.5, 101)], "long", 100, 102, 99)
        self.assertEqual((r["status"], r["exit_reason"]), ("LOSS", "SL"))

    def test_time_exit(self):
        rows = [bar(i, 100, 100.4, 99.8, 100.2) for i in range(3)]  # bars_limit=1 -> 3 five-minute candles
        rows.append(bar(3, 100, 105, 95, 100))  # beyond the limit; must be ignored
        r = self.run_path(rows, "long", 100, 102, 99)
        self.assertEqual((r["status"], r["exit_reason"], r["bars"]), ("WIN", "TIME", 1))
        self.assertAlmostEqual(r["gross_r"], 0.2)

    def test_candle_before_since_ignored(self):
        rows = [[T0 - FIVE, 100, 110, 90, 100, 1.0]] + [bar(0, 100, 102.1, 99.5, 102)]
        r = self.run_path(rows, "long", 100, 102, 99)
        self.assertEqual(r["exit_reason"], "TP")

    def test_insufficient_data_unresolved(self):
        r = self.run_path([bar(0, 100, 100.4, 99.8, 100.2)], "long", 100, 102, 99)
        self.assertFalse(r["resolved"]); self.assertIsNone(r["gross_r"])

    def test_long_short_mirror(self):
        rng = random.Random(7)
        for _ in range(40):
            p, rows, mrows = 100.0, [], []
            for i in range(30):
                o = p; c = p * (1 + rng.gauss(0, 0.004))
                hi = max(o, c) * (1 + abs(rng.gauss(0, 0.002))); lo = min(o, c) * (1 - abs(rng.gauss(0, 0.002)))
                rows.append(bar(i, o, hi, lo, c)); mrows.append(bar(i, 200 - o, 200 - lo, 200 - hi, 200 - c))
                p = c
            a = self.run_path(rows, "long", 100, 101.5, 99, bars_limit=10)
            b = self.run_path(mrows, "short", 100, 98.5, 101, bars_limit=10)
            for k in ("resolved", "status", "exit_reason", "bars", "mfe_r", "mae_r", "pnl_r"):
                self.assertEqual(a[k], b[k], k)
            self.assertAlmostEqual(a["gross_r"], b["gross_r"])


class Exchange(Base):
    def test_since_limit_semantics(self):
        write_cache(self.root, "ohlcv", "ETH_15m.csv.gz", [[T0 + i * 900_000, 1, 2, 0.5, 1.5, 3] for i in range(10)])
        ex = h.CachedExchange(self.root)
        got = ex.fetch_ohlcv("ETH/USDT:USDT", "15m", since=T0 + 2 * 900_000, limit=3)
        self.assertEqual([c[0] for c in got], [T0 + i * 900_000 for i in (2, 3, 4)])
        got = ex.fetch_ohlcv("ETH/USDT:USDT", "15m", since=T0 + 2 * 900_000 + 1, limit=2)  # mid-bar since
        self.assertEqual([c[0] for c in got], [T0 + 3 * 900_000, T0 + 4 * 900_000])
        self.assertEqual(len(ex.fetch_ohlcv("ETH/USDT", "15m", since=T0 + 8 * 900_000, limit=5)), 2)
        self.assertEqual(ex.fetch_ohlcv("ETH/USDT", "15m", since=T0 + 99 * 900_000, limit=5), [])
        self.assertEqual(ex.fetch_ohlcv("ETH/USDT", "15m", limit=2)[-1][0], T0 + 9 * 900_000)

    def test_holdout_refused_without_flag(self):
        hts = h.HOLDOUT_START_MS
        write_cache(self.root, "ohlcv", "BTC_5m.csv.gz", [[hts - FIVE, 1, 1, 1, 1, 1]])
        write_cache(self.root, "holdout", "BTC_5m.csv.gz", [[hts, 1, 1, 1, 1, 1]])
        log = os.path.join(self.root, "holdout_access.log")
        ex = h.CachedExchange(self.root)
        with self.assertRaises(h.HarnessError):
            ex.fetch_ohlcv("BTC/USDT:USDT", "5m", since=hts, limit=5)
        # must also escape evaluate_outcome's `except Exception`
        with self.assertRaises(h.HarnessError):
            h.score([dict(symbol="BTC/USDT", direction="long", signaled_at="2026-01-01T00:00:00+00:00",
                          entry=1, tp=2, sl=0.5, r_dist=0.5, bars_limit=1)], ex)
        self.assertEqual(len(ex.fetch_ohlcv("BTC/USDT", "5m")), 1)  # holdout rows never loaded
        self.assertFalse(os.path.exists(log))
        ex2 = h.CachedExchange(self.root, allow_holdout=True)
        self.assertEqual(len(ex2.fetch_ohlcv("BTC/USDT", "5m", since=hts, limit=5)), 1)
        with open(log) as f:
            rec = json.loads(f.readline())
        self.assertIn("test_harness.py", rec["caller"]); self.assertTrue(rec["files"][0].endswith("BTC_5m.csv.gz"))


class Helpers(unittest.TestCase):
    def test_bars_limit(self):
        self.assertEqual(h.bars_limit_15m(16, "15m"), 16)
        self.assertEqual(h.bars_limit_15m(16, "1H"), 64)
        self.assertEqual(h.bars_limit_15m(16, "4h"), 256)
        self.assertEqual(h.bars_limit_15m(3, "5m"), 1)
        with self.assertRaises(ValueError):
            h.bars_limit_15m(16, "5m")

    def test_atr_and_x0(self):
        c = [[i, 10, 11, 9, 10, 1] for i in range(20)]  # TR = 2 every bar
        atr = h.wilder_atr(c)
        self.assertIsNone(atr[12]); self.assertAlmostEqual(atr[13], 2.0); self.assertAlmostEqual(atr[19], 2.0)
        lo, sh = h.x0_barriers("long", 100, 2.0), h.x0_barriers("SHORT", 100, 2.0)
        self.assertEqual((lo["sl"], lo["tp"], lo["r_dist"]), (97.0, 104.5, 3.0))
        self.assertEqual((sh["sl"], sh["tp"], sh["r_dist"]), (103.0, 95.5, 3.0))

    def test_atr_causal(self):
        rng = random.Random(1)
        c = [[i, 10, 10 + rng.random(), 10 - rng.random(), 10 + rng.uniform(-.5, .5), 1] for i in range(60)]
        h.assert_no_lookahead(lambda cs: h.wilder_atr(cs), c)


class Lookahead(unittest.TestCase):
    def setUp(self):
        rng = random.Random(3); p = 100.0; self.c = []
        for i in range(80):
            o = p; p *= 1 + rng.gauss(0, 0.01)
            self.c.append([i, o, max(o, p) * 1.001, min(o, p) * 0.999, p, 10.0])

    def test_clean_passes(self):
        h.assert_no_lookahead(lambda cs: [i > 0 and cs[i][4] > cs[i - 1][4] for i in range(len(cs))], self.c)

    def test_next_bar_peek_caught(self):
        leaky = lambda cs: [i + 1 < len(cs) and cs[i + 1][4] > cs[i][4] for i in range(len(cs))]
        with self.assertRaises(AssertionError):
            h.assert_no_lookahead(leaky, self.c)

    def test_full_sample_normalisation_caught(self):
        leaky = lambda cs: [c[4] > sum(x[4] for x in cs) / len(cs) for c in cs]  # whole-series mean
        with self.assertRaises(AssertionError):
            h.assert_no_lookahead(leaky, self.c)


if __name__ == "__main__":
    unittest.main()
