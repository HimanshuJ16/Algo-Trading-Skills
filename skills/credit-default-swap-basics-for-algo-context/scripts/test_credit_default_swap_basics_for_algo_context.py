import math
import unittest
from credit_default_swap_basics_for_algo_context import CreditDefaultSwapEngine

class TestCreditDefaultSwapEngine(unittest.TestCase):

    def setUp(self):
        # Recovery = 40% (0.40), Risk-free rate = 4% (0.04)
        self.engine = CreditDefaultSwapEngine(recovery_rate=0.40, risk_free_rate=0.04)

    def test_hazard_rate_calculation(self):
        # Credit triangle: Par spread = 180 bps -> 0.0180 / 0.60 = 0.030 (3.0%)
        self.assertAlmostEqual(self.engine.calculate_hazard_rate(180.0), 0.03, places=6)
        self.assertEqual(self.engine.calculate_hazard_rate(0.0), 0.0)

    def test_default_probabilities_known_constant(self):
        # lambda = 0.03, T = 5: S = e^-0.15 = 0.8607079764250578 (known constant)
        survival, pd = self.engine.calculate_default_probabilities(0.03, 5.0)
        self.assertAlmostEqual(survival, 0.8607079764250578, places=10)
        self.assertAlmostEqual(pd, 0.1392920235749422, places=10)
        self.assertAlmostEqual(survival + pd, 1.0, places=12)

    def test_rpv01_matches_numerical_integration(self):
        # Independent derivation: RPV01 = integral_0^T e^-(r+lambda)t dt,
        # approximated by the midpoint rule (100,000 steps).
        hazard, maturity, rate = 1.0 / 30.0, 5.0, 0.04
        steps = 100_000
        h = maturity / steps
        integral = sum(
            math.exp(-(rate + hazard) * (i + 0.5) * h) * h for i in range(steps)
        )
        self.assertAlmostEqual(self.engine.calculate_rpv01(hazard, maturity), integral, places=6)

    def test_rpv01_zero_rate_limit_is_maturity(self):
        # r + lambda -> 0: (1 - e^-xT)/x -> T
        zero_rate_engine = CreditDefaultSwapEngine(risk_free_rate=0.0)
        self.assertEqual(zero_rate_engine.calculate_rpv01(0.0, 5.0), 5.0)

    def test_upfront_payment_independently_derived(self):
        # Par 200, coupon 100, R 0.40, r 4%, T 5, N $10M:
        # lambda = 0.02/0.6 = 1/30; r + lambda = 11/150
        # RPV01 = integral_0^5 e^-(11/150)t dt (midpoint rule, independent path)
        # Upfront = 10M * RPV01 * 0.01 ~= $418,580 (buyer pays; par > coupon)
        res = self.engine.calculate_isda_upfront_payment(
            notional_usd=10_000_000.0, par_spread_bps=200.0,
            standard_coupon_bps=100.0, maturity_years=5.0
        )
        self.assertAlmostEqual(res.hazard_rate_pct, 100.0 / 30.0, places=4)
        steps, maturity, rate_lambda = 100_000, 5.0, 11.0 / 150.0
        h = maturity / steps
        integral = sum(math.exp(-rate_lambda * (i + 0.5) * h) * h for i in range(steps))
        self.assertAlmostEqual(res.rpv01, integral, delta=0.001)
        expected_upfront = 10_000_000.0 * integral * 0.01
        self.assertAlmostEqual(res.isda_upfront_payment_usd, expected_upfront, delta=5.0)
        self.assertEqual(res.credit_tier, "CROSSOVER_HIGH_YIELD")

    def test_upfront_sign_convention(self):
        # Par < coupon: protection seller pays (negative upfront)
        res = self.engine.calculate_isda_upfront_payment(
            notional_usd=10_000_000.0, par_spread_bps=50.0,
            standard_coupon_bps=100.0, maturity_years=5.0
        )
        self.assertLess(res.isda_upfront_payment_usd, 0.0)
        # Par == coupon: no upfront
        res_at_par = self.engine.calculate_isda_upfront_payment(
            notional_usd=10_000_000.0, par_spread_bps=100.0,
            standard_coupon_bps=100.0, maturity_years=5.0
        )
        self.assertEqual(res_at_par.isda_upfront_payment_usd, 0.0)

    def test_credit_tier_boundaries(self):
        self.assertEqual(self.engine.classify_credit_tier(149.99), "INVESTMENT_GRADE")
        self.assertEqual(self.engine.classify_credit_tier(150.0), "CROSSOVER_HIGH_YIELD")
        # Regression: 500 bps is the standard HY coupon, not distressed
        self.assertEqual(self.engine.classify_credit_tier(500.0), "CROSSOVER_HIGH_YIELD")
        self.assertEqual(self.engine.classify_credit_tier(999.99), "CROSSOVER_HIGH_YIELD")
        self.assertEqual(self.engine.classify_credit_tier(1000.0), "DISTRESSED")

    def test_cross_asset_signal_spike_and_compression(self):
        # [100 x 9, 200]: mean=110, pop std=30, z=(200-110)/30=3.0 > 2
        spike = CreditDefaultSwapEngine.generate_cross_asset_signal([100.0] * 9 + [200.0])
        self.assertEqual(spike.signal, "SHORT_EQUITY_LONG_CDS")
        self.assertAlmostEqual(spike.z_score, 3.0, places=6)
        # [200 x 9, 100]: mean=190, std=30, z=-3.0 < -2
        compress = CreditDefaultSwapEngine.generate_cross_asset_signal([200.0] * 9 + [100.0])
        self.assertEqual(compress.signal, "LONG_EQUITY_SHORT_CDS")
        self.assertAlmostEqual(compress.z_score, -3.0, places=6)

    def test_cross_asset_signal_boundary_and_flat_history(self):
        # [100 x 4, 300]: mean=140, var=(4*1600+25600)/5=6400, std=80, z=2.0
        # Threshold is strict: z == 2.0 stays NEUTRAL
        at_boundary = CreditDefaultSwapEngine.generate_cross_asset_signal(
            [100.0, 100.0, 100.0, 100.0, 300.0]
        )
        self.assertEqual(at_boundary.signal, "NEUTRAL")
        # Flat history: zero std -> z = 0 -> NEUTRAL
        flat = CreditDefaultSwapEngine.generate_cross_asset_signal([100.0, 100.0])
        self.assertEqual(flat.signal, "NEUTRAL")
        self.assertEqual(flat.z_score, 0.0)

    def test_engine_input_validation(self):
        for bad_recovery in (1.0, -0.1, 1.5, float("nan")):
            with self.assertRaises(ValueError):
                CreditDefaultSwapEngine(recovery_rate=bad_recovery)
        with self.assertRaises(ValueError):
            CreditDefaultSwapEngine(risk_free_rate=float("inf"))
        with self.assertRaises(ValueError):
            CreditDefaultSwapEngine(ig_crossover_threshold_bps=1500.0,
                                    crossover_distressed_threshold_bps=1000.0)

    def test_method_input_validation(self):
        with self.assertRaises(ValueError):
            self.engine.calculate_hazard_rate(-5.0)
        with self.assertRaises(ValueError):
            self.engine.calculate_hazard_rate(float("nan"))
        with self.assertRaises(ValueError):
            self.engine.calculate_default_probabilities(-0.01, 5.0)
        with self.assertRaises(ValueError):
            self.engine.calculate_default_probabilities(0.03, 0.0)
        with self.assertRaises(ValueError):
            self.engine.calculate_rpv01(-0.01, 5.0)
        with self.assertRaises(ValueError):
            self.engine.calculate_isda_upfront_payment(0.0, 200.0, 100.0, 5.0)
        with self.assertRaises(ValueError):
            self.engine.calculate_isda_upfront_payment(10_000_000.0, 200.0, 100.0, 0.0)
        with self.assertRaises(ValueError):
            self.engine.calculate_isda_upfront_payment(10_000_000.0, 200.0, -1.0, 5.0)

    def test_cross_asset_signal_validation(self):
        with self.assertRaises(ValueError):
            CreditDefaultSwapEngine.generate_cross_asset_signal([150.0])
        with self.assertRaises(ValueError):
            CreditDefaultSwapEngine.generate_cross_asset_signal([100.0, float("nan")])
        with self.assertRaises(ValueError):
            CreditDefaultSwapEngine.generate_cross_asset_signal([100.0, -50.0])
        with self.assertRaises(ValueError):
            CreditDefaultSwapEngine.generate_cross_asset_signal([100.0, 200.0], z_threshold=0.0)

if __name__ == '__main__':
    unittest.main()
