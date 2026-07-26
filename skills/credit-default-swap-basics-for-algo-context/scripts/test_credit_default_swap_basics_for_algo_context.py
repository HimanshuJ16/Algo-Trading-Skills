import unittest
import math
from credit_default_swap_basics_for_algo_context import (
    CreditDefaultSwapEngine, CdsValuationResult
)

class TestCreditDefaultSwapEngine(unittest.TestCase):

    def setUp(self):
        # Recovery = 40% (0.40), Risk-free rate = 4% (0.04)
        self.engine = CreditDefaultSwapEngine(recovery_rate=0.40, risk_free_rate=0.04)

    def test_hazard_rate_calculation(self):
        # Par spread = 180 bps (0.0180) -> Hazard rate = 0.0180 / 0.60 = 0.030 (3.0%)
        hazard_rate = self.engine.calculate_hazard_rate(180.0)
        self.assertAlmostEqual(hazard_rate, 0.03, places=4)

    def test_default_probabilities(self):
        # Hazard rate = 0.0333 (3.33%), T = 5.0 years
        # Survival = exp(-0.0333 * 5) = exp(-0.1667) = 0.8465 (84.65%)
        # Default = 1 - 0.8465 = 15.35%
        survival, pd = self.engine.calculate_default_probabilities(0.033333, 5.0)
        self.assertAlmostEqual(survival, math.exp(-0.033333 * 5.0), places=4)
        self.assertAlmostEqual(pd, 1.0 - math.exp(-0.033333 * 5.0), places=4)

    def test_isda_upfront_payment_and_metrics(self):
        # Par spread = 200 bps, Standard Coupon = 100 bps (100 bps difference)
        # Notional = $10,000,000, T = 5.0 years
        res = self.engine.calculate_isda_upfront_payment(
            notional_usd=10_000_000.0, par_spread_bps=200.0,
            standard_coupon_bps=100.0, maturity_years=5.0
        )
        
        self.assertGreater(res.hazard_rate_pct, 0.0)
        self.assertGreater(res.isda_upfront_payment_usd, 0.0) # Protection buyer pays upfront
        self.assertEqual(res.credit_tier, "CROSSOVER_HIGH_YIELD")

if __name__ == '__main__':
    unittest.main()
