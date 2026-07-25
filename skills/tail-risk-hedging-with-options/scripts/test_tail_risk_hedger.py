import unittest
from tail_risk_hedger import TailRiskHedger, HedgingResult

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
