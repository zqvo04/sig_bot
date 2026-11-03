import os, random, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import harness as h
import controls as C


def candles(seed, n=300, tfm=3_600_000):
    rng, p, out = random.Random(seed), 30000, []
    for i in range(n):
        o = p
        p += rng.randint(-60, 60)
        out.append([i * tfm, o, max(o, p) + rng.randint(0, 20), min(o, p) - rng.randint(0, 20), p, 1])
    return out


def mirror(cs, P=60000):  # integer prices: exact
    return [[c[0], P - c[1], P - c[3], P - c[2], P - c[4], c[5]] for c in cs]


FLIP = {"long": "short", "short": "long", None: None}


class T(unittest.TestCase):
    def test_no_lookahead(self):
        cs = candles(1, 120)
        for f in (C.c1_signal, C.c2_signal):
            h.assert_no_lookahead(f, cs, decision_idx=range(0, 120, 7))

    def test_mirror(self):
        cs = candles(2)
        for f in (C.c1_signal, C.c2_signal):
            self.assertEqual([FLIP[x] for x in f(cs)], f(mirror(cs)))

    def test_counts(self):
        cs = candles(3, 2000)
        a, b = C.c1_signal(cs), C.c2_signal(cs)
        self.assertTrue(all(x is None for x in a[:C.N]) and all(x is None for x in b[:C.N - 1]))
        for s in (a, b):
            n = sum(x is not None for x in s)
            self.assertTrue(10 < n < 600, n)
            self.assertTrue({"long", "short"} <= set(s))

    def test_entries(self):
        cs = candles(4, 300)
        for f in (C.c1, C.c2):
            es = f({"1h": cs}, "BTC", {"tf": "1h"})
            self.assertTrue(es)
            for e in es:
                self.assertEqual(e["bars_limit_15m"], 64)
                self.assertEqual(e["signal_ts_ms"] % 300_000, 0)
                s = 1 if e["direction"] == "long" else -1
                self.assertTrue(s * (e["tp"] - e["entry"]) > 0 > s * (e["sl"] - e["entry"]))


if __name__ == "__main__":
    unittest.main()
