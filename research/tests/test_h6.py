"""H6 tests: no lookahead (core + each part ON + E7 exits), long/short mirror, parts are filters, N=20 core == controls.c1."""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import controls
import harness as h
import hyp_h6 as H
from test_h1 import M5, T0, frames, mirror, walk  # synthetic 5m walk + exact additive mirror


def configs():
    yield "core", {}
    for n in H.GRIDS["N"]["grid"]:
        yield f"N={n}", {"N": n}
    for k in H.PARTS:
        yield k, {k: H.GRIDS[k]["center"]}
    for x in H.E7[1:]:
        yield f"exit={x}", {"exit": x}


def signal_fn(params):
    def fn(rows5):
        D = frames(rows5)
        out = [None] * len(rows5)
        for i, d, _ in H.decisions(D, params):
            j = (D["1h"][i][0] + 3_600_000 - T0) // M5 - 1  # 5m bar whose close = the 1H decision instant
            if j < len(out):
                out[j] = d
        return out
    return fn


class H6Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = walk(3)
        cls.D, cls.Dm = frames(cls.rows), frames(mirror(cls.rows))

    def test_core_equals_c1(self):
        a = [(e["signal_ts_ms"], e["direction"], e["entry"], e["sl"]) for e in H.strategy(self.D, "BTC", {})]
        b = [(e["signal_ts_ms"], e["direction"], e["entry"], e["sl"]) for e in controls.c1(self.D, "BTC")]
        self.assertGreater(len(a), 10)
        self.assertEqual(a, b)

    def test_parts_are_filters(self):
        core = {(i, d) for i, d, _ in H.decisions(self.D, {})}
        self.assertEqual({d for _, d in core}, {"long", "short"})
        for k in H.PARTS:
            on = {(i, d) for i, d, _ in H.decisions(self.D, {k: H.GRIDS[k]["center"]})}
            self.assertTrue(on < core, f"{k} ON must remove some signals and add none")
        self.assertEqual(H.param_cost({}), 1)
        self.assertEqual(H.param_cost({"P1": True, "P6": 70, "exit": "X1"}), 3)
        self.assertRaises(ValueError, H.decisions, self.D, {"P7": 0.5})

    def test_no_lookahead(self):
        for name, p in configs():
            with self.subTest(name):
                fn = signal_fn(p)
                sig = [j for j, x in enumerate(fn([list(r) for r in self.rows])) if x]
                idx = sig[::max(1, len(sig) // 12)] or [len(self.rows) * f // 5 for f in (2, 3, 4)]
                idx += [j - 1 for j in idx if j]
                h.assert_no_lookahead(fn, self.rows, decision_idx=sorted(set(idx)))

    def test_mirror_symmetry(self):
        # P3 uses (H-L)/C: scale-free, direction-free, but not invariant to the additive price mirror (as in test_h1).
        for name, p in configs():
            if name == "P3":
                continue
            with self.subTest(name):
                a = {(i, d) for i, d, _ in H.decisions(self.D, p)}
                b = {(i, {"long": "short", "short": "long"}[d]) for i, d, _ in H.decisions(self.Dm, p)}
                self.assertTrue(a, f"{name}: vacuous")
                self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
