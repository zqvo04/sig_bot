import os, sys, unittest, datetime, random
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import phase1_rescore as p

DT = datetime.datetime


class T(unittest.TestCase):
    def test_t1_mirror(self):  # 롱/숏 가격 거울: E 동일, SL/TP 방향 반전 → R Dist 동일 → cost_R 동일
        E, sl_l, sl_s = 100.0, 99.0, 101.0
        for x in ("TP", "SL", "TIME", "BE"):
            for sc in "ABC":
                a = p.cost_R(E, abs(E - sl_l), x, sc, 0.01)
                b = p.cost_R(E, abs(sl_s - E), x, sc, 0.01)
                self.assertEqual(a, b)

    def test_t2_zero_cost(self):
        for x in ("TP", "SL", "TIME", "BE"):
            for sc in "ABC":
                c = p.cost_R(100.0, 1.0, x, sc, 0.0, maker=0, taker=0, slip=0)
                fr, *_ = p.fund_R(1, 0.01, DT(2026, 9, 1, 1), DT(2026, 9, 1, 9), {p._ms(DT(2026, 9, 1, 8)): 0.0})
                self.assertEqual(1.7 - c - fr, 1.7)

    def test_t3_funding_sign_and_window(self):
        S, E = DT(2026, 9, 1, 1), DT(2026, 9, 1, 9)
        k = p._ms(DT(2026, 9, 1, 8))
        self.assertGreater(p.fund_R(1, .01, S, E, {k: .0001})[0], 0)   # 롱 +율 = 지불
        self.assertLess(p.fund_R(-1, .01, S, E, {k: .0001})[0], 0)     # 숏 +율 = 수취
        self.assertLess(p.fund_R(1, .01, S, E, {k: -.0001})[0], 0)
        self.assertGreater(p.fund_R(-1, .01, S, E, {k: -.0001})[0], 0)
        # S < t_f <= E: t_f==S 제외, t_f==E 포함
        self.assertEqual(p.settlements(DT(2026, 9, 1, 8), DT(2026, 9, 1, 12)), [])
        self.assertEqual(p.settlements(DT(2026, 9, 1, 4), DT(2026, 9, 1, 8)), [DT(2026, 9, 1, 8)])
        self.assertEqual(p.fund_R(1, .01, S, E, {})[2], 1)  # 결측 1건 보고

    def test_t4_scale_free(self):
        for k in (0.01, 3.0, 1000.0):
            for x in ("TP", "SL"):
                self.assertAlmostEqual(p.cost_R(50.0, 0.5, x, "A", 0.01), p.cost_R(50 * k, 0.5 * k, x, "A", 0.01 * k), places=12)

    def test_t5_monotone(self):
        for x in ("TP", "SL", "TIME", "BE"):
            a, b, c = (p.cost_R(100.0, 0.5, x, s, 0.1) for s in "ABC")
            self.assertLessEqual(a, b)
            self.assertLessEqual(b, c)

    def test_block_bootstrap_determinism(self):
        rnd = random.Random(1)
        v = [rnd.gauss(0, 1) for _ in range(200)]
        d = [i // 4 for i in range(200)]
        a, b = p.block_ci(v, d), p.block_ci(v, d)
        self.assertEqual(a, b)
        self.assertLess(a[0], a[1])


if __name__ == "__main__":
    unittest.main()
