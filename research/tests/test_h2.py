import os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as h
import runner as R
import hyp_h2 as H
from test_runner import P0, PERIOD, agg15, walk, write

TICK = 0.1  # BTC


def bar(lo, hi, o=None, c=None, ts=0):
    o = (lo + hi) / 2 if o is None else o
    return (ts, o, hi, lo, (lo + hi) / 2 if c is None else c, 1)


class FillModel(unittest.TestCase):
    # decision bar 0, long limit 100.0 (1000 ticks); short limit 100.0 mirrored on the highs
    def test_touch_no_fill(self):
        C = [bar(100, 101), bar(100.0, 101)]
        self.assertIsNone(H.fill_bar(C, 0, 1, 1000, TICK, 2, through=True))
        self.assertEqual(H.fill_bar(C, 0, 1, 1000, TICK, 2, through=False), 1)
        C = [bar(99, 100), bar(99, 100.0)]
        self.assertIsNone(H.fill_bar(C, 0, -1, 1000, TICK, 2, through=True))
        self.assertEqual(H.fill_bar(C, 0, -1, 1000, TICK, 2, through=False), 1)

    def test_one_tick_beyond_is_not_enough(self):  # catalog: low < limit - 1 tick
        self.assertIsNone(H.fill_bar([bar(100, 101), bar(99.9, 101)], 0, 1, 1000, TICK, 2))
        self.assertIsNone(H.fill_bar([bar(99, 100), bar(99, 100.1)], 0, -1, 1000, TICK, 2))

    def test_trade_through_fill(self):
        C = [bar(100, 101), bar(100.5, 101), bar(99.8, 101)]
        self.assertEqual(H.fill_bar(C, 0, 1, 1000, TICK, 2), 2)
        C = [bar(99, 100), bar(99, 99.5), bar(99, 100.2)]
        self.assertEqual(H.fill_bar(C, 0, -1, 1000, TICK, 2), 2)

    def test_expiry(self):
        C = [bar(100, 101), bar(100.5, 101), bar(100.5, 101), bar(99, 101)]
        self.assertIsNone(H.fill_bar(C, 0, 1, 1000, TICK, 2))  # trades through only on bar 3 > window 2
        self.assertEqual(H.fill_bar(C, 0, 1, 1000, TICK, 4), 3)
        self.assertIsNone(H.fill_bar(C, 0, 1, 1000, TICK, 0))  # cancelled before the first bar

    def test_fill_bar_sl_starts_at_open(self):
        o = dict(i=0, s=1, limit=100.0, atr=1.0, k=1)
        C = [bar(100, 101, ts=0), bar(98.4, 101, ts=R.M15)]  # SL = 98.5 reached inside the fill bar
        e = H.fill_entry(o, C, "BTC", TICK)
        self.assertEqual(e["signal_ts_ms"], R.M15)
        self.assertTrue(e["meta"]["sl_in_fill_bar"])
        C = [bar(100, 101, ts=0), bar(99.0, 101, ts=R.M15)]
        e = H.fill_entry(o, C, "BTC", TICK)
        self.assertEqual(e["signal_ts_ms"], 2 * R.M15)  # evaluation starts after the fill bar
        self.assertEqual((e["entry"], e["sl"]), (100.0, 98.5))
        self.assertAlmostEqual(e["tp"], 100 + 2.25 + 2 * TICK)  # TP trade-through shift


class Synthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root, cls.mroot = os.path.join(cls.tmp.name, "a"), os.path.join(cls.tmp.name, "m")
        w = walk(1)
        write(cls.root, "BTC", w)
        write(cls.mroot, "BTC", [[r[0], 2 * P0 - r[1], 2 * P0 - r[3], 2 * P0 - r[2], 2 * P0 - r[4], r[5]] for r in w])
        cls.C = [tuple(r) for r in agg15(w)]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_signal_no_lookahead(self):
        C = self.C[:400]
        for d in H.DELTA_GRID["grid"]:
            h.assert_no_lookahead(lambda cs: H.decisions(cs, d, TICK), C, decision_idx=range(20, 400, 7))
        self.assertTrue(any(H.decisions(C, 0.25, TICK)))

    def test_entries_after_decision_and_subset(self):
        T = H.orders(self.C, H.CORE, TICK)
        U = H.orders(self.C, dict(H.CORE, fill="touch"), TICK)
        f, g = [o for o in T if o["k"] is not None], [o for o in U if o["k"] is not None]
        self.assertGreater(len(f), 30)
        self.assertLess(len(f), len(T))
        self.assertLessEqual({o["i"] for o in f}, {o["i"] for o in g})
        for o in f:
            self.assertTrue(o["i"] < o["k"] <= min(o["end"], o["i"] + H.CORE["window"]))
            e = H.fill_entry(o, self.C, "BTC", TICK)
            self.assertGreaterEqual(e["signal_ts_ms"], e["meta"]["decision_ts"])

    def test_mirror_symmetry(self):
        a = H.run_h2(H.CORE, ["BTC"], PERIOD, data_dir=self.root)
        b = H.run_h2(H.CORE, ["BTC"], PERIOD, data_dir=self.mroot)
        self.assertGreater(len(a), 30)
        self.assertEqual(len(a), len(b))
        for x, y in zip(a, b):
            self.assertEqual(x["signal_ts_ms"], y["signal_ts_ms"])
            self.assertNotEqual(x["direction"], y["direction"])
            self.assertEqual(x["exit"], y["exit"])
            self.assertAlmostEqual(x["gross_R"], y["gross_R"], places=6)
        for t in a:
            if t["exit"] == "TP":
                self.assertAlmostEqual(t["gross_R"], 1.5, places=9)

    def test_adverse_selection_runs(self):
        r = H.adverse_selection(["BTC"], PERIOD, H.CORE, data_dir=self.root)
        s = r["summary"]
        self.assertGreater(s["n_orders"], 30)
        self.assertTrue(0 < s["fill_rate"] < 1)
        self.assertEqual(s["n_filled"], sum(x["filled"] for x in r["rows"]))
        self.assertIsNotNone(s["AS_fill"])
        tk = R.run(H.taker_strategy, H.CORE, ["BTC"], PERIOD, scenario="B", data_dir=self.root)
        self.assertGreater(len(tk), 30)


if __name__ == "__main__":
    unittest.main()
