import unittest
from cross_account_risk_aggregator import (
    CrossAccountRiskAggregator, SubAccountState
)

class TestCrossAccountRiskAggregator(unittest.TestCase):

    def setUp(self):
        self.aggregator = CrossAccountRiskAggregator(max_firm_gmv_limit_usd=1_000_000.0, max_margin_utilization_pct=80.0)
        
        # Sub-Account 1 (IBKR): Long 1,000 AAPL, Long 500 NVDA
        self.acc1 = SubAccountState(
            account_id="ACC_IBKR_01", broker_name="InteractiveBrokers",
            cash_usd=200_000.0, margin_used_usd=50_000.0, margin_limit_usd=200_000.0,
            positions={"AAPL": 1000.0, "NVDA": 500.0}
        )
        # Sub-Account 2 (Coinbase/Crypto/Futures): Short 400 AAPL (Internal Offsetting!)
        self.acc2 = SubAccountState(
            account_id="ACC_FUTURES_02", broker_name="CME_FCM",
            cash_usd=150_000.0, margin_used_usd=30_000.0, margin_limit_usd=200_000.0,
            positions={"AAPL": -400.0}
        )
        
        self.aggregator.register_account(self.acc1)
        self.aggregator.register_account(self.acc2)
        
        # Market prices: AAPL = $150, NVDA = $200
        self.market_prices = {"AAPL": 150.0, "NVDA": 200.0}

    def test_consolidation_and_internal_offsetting(self):
        report = self.aggregator.aggregate_firm_risk(self.market_prices)
        
        # AAPL Net = 1000 - 400 = 600 shares ($90,000 value)
        # NVDA Net = 500 shares ($100,000 value)
        # Gross Market Value = $90k + $100k = $190,000
        self.assertEqual(report.net_positions["AAPL"], 600.0)
        self.assertEqual(report.net_positions["NVDA"], 500.0)
        self.assertEqual(report.total_gmv_usd, 190_000.0)
        self.assertTrue(report.is_compliant)
        
        # AAPL held long in Acc 1 and short in Acc 2 -> Internal Offsetting
        self.assertIn("AAPL", report.internal_offsetting_friction_symbols)

    def test_pre_trade_order_approval(self):
        # Order: Buy 1,000 AAPL @ $150 ($150,000 value) -> Total GMV = $190k + $150k = $340k <= $1M
        is_approved, msg = self.aggregator.evaluate_pre_trade_order(
            account_id="ACC_IBKR_01", symbol="AAPL", proposed_qty=1000.0, price=150.0, market_prices=self.market_prices
        )
        self.assertTrue(is_approved)

    def test_pre_trade_order_rejection_on_gmv_limit(self):
        # Order: Buy 10,000 NVDA @ $200 ($2,000,000 value -> Breaches $1M firm GMV cap)
        is_approved, msg = self.aggregator.evaluate_pre_trade_order(
            account_id="ACC_IBKR_01", symbol="NVDA", proposed_qty=10000.0, price=200.0, market_prices=self.market_prices
        )
        self.assertFalse(is_approved)
        self.assertIn("Firm GMV limit breached", msg)

if __name__ == '__main__':
    unittest.main()
