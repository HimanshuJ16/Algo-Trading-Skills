"""
Unit tests for tail-risk-hedging-with-options skill.

Tests:
1. Legacy hedge success and insufficient budget tests.
2. Legacy Config & Engine wrapper.
3. Black-Scholes OTM put pricing and Greeks calculation.
4. Systematic OTM put hedge planning with 20% and 30% crash payout simulation.
"""
import unittest
from tail_risk_hedger import Config, Engine, TailRiskHedger, HedgingResult


class TestTailRiskHedger(unittest.TestCase):

    def test_hedge_success(self):
        hedger = TailRiskHedger(budget_pct=0.05)
        res = hedger.hedge(100000, 100)
        self.assertTrue(res.hedged)
        self.assertEqual(res.options_bought, 50)
        self.assertEqual(res.cost, 5000)

    def test_hedge_insufficient_budget(self):
        hedger = TailRiskHedger(budget_pct=0.01)
        res = hedger.hedge(1000, 50)
        self.assertFalse(res.hedged)
        self.assertEqual(res.options_bought, 0)
        self.assertEqual(res.cost, 0.0)

    def test_legacy_engine(self):
        eng = Engine(Config())
        self.assertTrue(eng.config.enabled)
        self.assertTrue(eng.run())

    def test_black_scholes_put_pricing(self):
        hedger = TailRiskHedger()
        bs = hedger.black_scholes_put(S=100.0, K=85.0, T=0.25, r=0.04, sigma=0.25)
        self.assertGreater(bs["price"], 0.0)
        self.assertLess(bs["delta"], 0.0)
        self.assertGreater(bs["gamma"], 0.0)

    def test_systematic_otm_put_hedge_planning(self):
        config = Config(budget_pct=0.02, otm_pct=0.15, dte_target=90)
        hedger = TailRiskHedger(config=config)
        portfolio_val = 1_000_000.0
        spot = 400.0

        res = hedger.plan_systematic_otm_put_hedge(
            portfolio_value=portfolio_val,
            spot_price=spot,
            volatility=0.25,
            risk_free_rate=0.04,
            contract_multiplier=100,
        )

        self.assertTrue(res.hedged)
        self.assertGreater(res.options_bought, 0)
        self.assertLessEqual(res.cost, portfolio_val * 0.02)
        self.assertEqual(res.strike_price, spot * 0.85)
        # On a 30% drop (spot becomes 280), strike is 340, intrinsic value per share is 60
        self.assertGreater(res.crash_payout_30pct_drop, 0.0)


if __name__ == "__main__":
    unittest.main()
