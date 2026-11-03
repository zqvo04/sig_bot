"""H1 strategy tests: no lookahead (core + every part ON + every E6/E7 option), long/short mirror, parts OFF = core."""
import gzip, os, random, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import harness as h
import hyp_h1 as H
import runner as R

P0 = 30000  # integer prices -> the 2*P0 - p mirror is exact
M5 = 300_000
T0 = R._ms("2023-04-01")
DAYS = 40


def walk(seed, n=DAYS * 288):
    """5m random walk with slow drift regimes (so 4H/1D trends and 1H pullbacks occur)."""
    rng, p, out, drift = random.Random(seed), P0, [], 0
    for i in range(n):
        if i % 2000 == 0:
            drift = rng.choice([-1, 0, 1])
        o, path = p, [p]
        for _ in range(5):
            p += rng.randint(-12, 12) + (drift if rng.random() < 0.2 else 0)
            path.append(p)
        out.append([T0 + i * M5, o, max(path), min(path), p, rng.randint(1, 100)])
    return out


def agg(rows, tf_ms):
    g = {}
    for r in rows:
        g.setdefault(r[0] // tf_ms * tf_ms, []).append(r)
    return [(k, v[0][1], max(c[2] for c in v), min(c[3] for c in v), v[-1][4], sum(c[5] for c in v))
            for k, v in sorted(g.items()) if len(v) == tf_ms // M5]


def frames(rows5):
    return {"5m": [tuple(r) for r in rows5], **{k: agg(rows5, ms) for k, ms in
                                                 (("15m", 900_000), ("1h", H.H1), ("4h", H.H4), ("1d", H.D1))}}


def mirror(rows5):
    return [[r[0], 2 * P0 - r[1], 2 * P0 - r[3], 2 * P0 - r[2], 2 * P0 - r[4], r[5]] for r in rows5]


def configs():
    """core, each part ON at its grid centre, each non-default E6 option, each E7 exit."""
    yield "core", {}
    for k in H.PARTS:
        yield k, {k: H.GRIDS[k]["center"]}
    for k, opts in H.E6.items():
        for o in opts[1:]:
            yield f"{k}={o}", {k: o}
    for x in H.E7[1:]:
        yield f"exit={x}", {"exit": x}


def signal_fn(params, at_entry=False):
    """assert_no_lookahead adapter on the 5m base series: every TF is rebuilt from (perturbed) 5m bars, and each 1H
    decision is written at the 5m bar whose CLOSE is the decision instant (at_entry: the next 5m bar, for P7 which
    legitimately reads the entry open)."""
    def fn(rows5):
        D = frames(rows5)
        out = [None] * len(rows5)
        for i, d, b in H.decisions(D, params):
            j = (D["1h"][i][0] + H.H1 - T0) // M5 - 1 + at_entry
            if j < len(out):
                out[j] = d
        return out
    return fn


class H1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = walk(3)
        cls.D = frames(cls.rows)
        cls.Dm = frames(mirror(cls.rows))

    def test_core_fires_and_parts_are_filters(self):
        core = {(i, d) for i, d, _ in H.decisions(self.D, {})}
        self.assertGreater(len(core), 10)
        self.assertEqual({d for _, d in core}, {"long", "short"})
        for k in H.PARTS:
            on = {(i, d) for i, d, _ in H.decisions(self.D, {k: H.GRIDS[k]["center"]})}
            off = {(i, d) for i, d, _ in H.decisions(self.D, {k: False if k == "P1" else None})}
            self.assertEqual(off, core, f"{k} OFF must equal core")
            self.assertTrue(on <= core, f"{k} ON must only remove signals")

    def test_defaults_are_core(self):
        self.assertEqual(H.decisions(self.D, {}), H.decisions(self.D, dict(H.DEFAULTS)))
        self.assertEqual(H.param_cost({}), 1)
        self.assertRaises(ValueError, H.decisions, self.D, {"P9": 1})
        self.assertRaises(ValueError, H.decisions, self.D, {"exit": "X2"})

    def test_no_lookahead(self):
        for name, p in configs():
            with self.subTest(name):
                at_entry = "P7" in p
                fn = signal_fn(p, at_entry)
                base = fn([list(r) for r in self.rows])
                sig = [j for j, x in enumerate(base) if x]
                idx = sig[::max(1, len(sig) // 12)]
                if not idx:  # still check a few decision instants when the config never fires here
                    idx = [len(self.rows) * f // 5 for f in (2, 3, 4)]
                idx += [j - 1 for j in idx if j]  # the bar just before each signal too
                h.assert_no_lookahead(fn, self.rows, decision_idx=sorted(set(idx)))

    def test_lookahead_check_has_power(self):
        """a planted leak (forming higher-TF bar treated as closed) must be caught for each TF the config reads."""
        orig = H._closed
        for tf, p in ((H.H4, {}), (H.D1, {"P1": True}), (900_000, {"P2": 30})):
            with self.subTest(tf=tf):
                H._closed = lambda ts, T_, t, tf=tf: orig(ts, T_ + t, t) if t == tf else orig(ts, T_, t)
                try:
                    fn = signal_fn(p)
                    sig = [j for j, x in enumerate(fn([list(r) for r in self.rows])) if x]
                    with self.assertRaises(AssertionError):
                        h.assert_no_lookahead(fn, self.rows, decision_idx=sig[::max(1, len(sig) // 12)])
                finally:
                    H._closed = orig

    def test_p7_reads_only_the_entry_open(self):
        fn = signal_fn({"P7": H.GRIDS["P7"]["center"]}, at_entry=True)
        base = fn([list(r) for r in self.rows])
        rng = random.Random(1)
        for j in [j for j, x in enumerate(base) if x][:6]:
            pert = [list(r) for r in self.rows]
            o = pert[j][1]  # keep the entry open, scramble the rest of that bar and everything after
            pert[j][2], pert[j][3], pert[j][4] = o + rng.randint(0, 900), o - rng.randint(0, 900), o + rng.randint(-900, 900)
            pert[j][2], pert[j][3] = max(pert[j][2], pert[j][4]), min(pert[j][3], pert[j][4])
            for r in pert[j + 1:]:
                r[1:5] = [x + 777 for x in r[1:5]]
            self.assertEqual(fn(pert)[:j + 1], base[:j + 1])

    def test_mirror_symmetry(self):
        # P3 and vol=parkinson use (H-L)/C or ln(H/L): scale-free but not invariant to the additive price mirror.
        # Both are direction-free by construction (no direction argument; same rule for long and short).
        for name, p in configs():
            if name in ("P3", "vol=parkinson"):
                continue
            with self.subTest(name):
                a = {(i, d) for i, d, _ in H.decisions(self.D, p)}
                b = {(i, {"long": "short", "short": "long"}[d]) for i, d, _ in H.decisions(self.Dm, p)}
                self.assertTrue(a, f"{name}: no signals, mirror test vacuous")
                self.assertEqual(a, b)

    def test_mirror_barriers(self):
        for x in H.E7:
            a = {i: b for i, d, b in H.decisions(self.D, {"exit": x}) if b}
            m = {i: b for i, d, b in H.decisions(self.Dm, {"exit": x}) if b}
            self.assertEqual(set(a), set(m))
            for i in a:
                self.assertAlmostEqual(a[i]["r_dist"], m[i]["r_dist"], places=6)
                self.assertAlmostEqual(a[i]["tp"] - a[i]["entry"], m[i]["entry"] - m[i]["tp"], places=4)

    def test_runner_smoke(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = walk(5, n=(R._ms("2023-06-20") - T0) // M5)
            os.makedirs(os.path.join(tmp, "ohlcv"))
            for tf, data in frames(rows).items():
                with gzip.open(os.path.join(tmp, "ohlcv", f"BTC_{h.TF_FILE[tf]}.csv.gz"), "wt") as f:
                    f.write("ts_ms,open,high,low,close,vol\n")
                    f.writelines(",".join(map(str, r)) + "\n" for r in data)
            T = R.run(H.strategy, {}, ["BTC"], ("2023-06-01", "2023-06-15"), data_dir=tmp)
            self.assertGreater(len(T), 0)
            self.assertTrue(all(t["bars_limit_15m"] == 64 for t in T))
            self.assertTrue(all(t["signal_ts_ms"] % H.H1 == 0 for t in T))


if __name__ == "__main__":
    unittest.main()
