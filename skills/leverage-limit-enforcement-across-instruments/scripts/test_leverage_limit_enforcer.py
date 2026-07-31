import unittest
from leverage_limit_enforcer import (
    LeverageLimitEnforcerEngine, PositionSpec, ProposedOrderSpec, LeverageEnforcementReport
)

class TestLeverageLimitEnforcerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = LeverageLimitEnforcerEngine(
            max_gross_leverage=5.0,
            max_net_leverage=3.0,
            asset_class_limits={"EQUITY": 2.0, "CRYPTO": 3.0, "FX": 10.0}
        )

    def test_order_leverage_approved(self):
        # Equity = $100k
        # Active: Long $100k AAPL, Short $50k TSLA -> Gross = $150k (1.5x), Net = $50k (0.5x)
        # Proposed: Buy $50k MSFT (Equity) -> Proj Gross = $200k (2.0x <= 5.0x), Proj Net = $100k (1.0x <= 3.0x), Equity = $150k (1.5x <= 2.0x) -> APPROVED!
        positions = [
            PositionSpec("AAPL", "EQUITY", "BUY", 100_000.0),
            PositionSpec("TSLA", "EQUITY", "SELL", 50_000.0)
        ]
        order = ProposedOrderSpec("MSFT", "EQUITY", "BUY", 50_000.0)
        report = self.engine.audit_proposed_order(100_000.0, positions, order)

        self.assertEqual(report.status, "ORDER_LEVERAGE_APPROVED")
        self.assertEqual(report.projected_gross_leverage, 2.0)
        self.assertEqual(report.projected_net_leverage, 1.0)
        self.assertTrue(report.is_gross_limit_passed)
        self.assertTrue(report.is_net_limit_passed)

    def test_gross_leverage_breach_veto(self):
        # Active Gross = 2.0x
        # Proposed: Buy $350k BTC-PERP (Crypto) -> Proj Gross = $550k (5.5x > 5.0x limit) -> REJECTED_GROSS_LEVERAGE_BREACH!
        positions = [
            PositionSpec("AAPL", "EQUITY", "BUY", 150_000.0),
            PositionSpec("TSLA", "EQUITY", "SELL", 50_000.0)
        ]
        order = ProposedOrderSpec("BTC-PERP", "CRYPTO", "BUY", 350_000.0)
        report = self.engine.audit_proposed_order(100_000.0, positions, order)

        self.assertEqual(report.status, "REJECTED_GROSS_LEVERAGE_BREACH")
        self.assertFalse(report.is_gross_limit_passed)
        self.assertEqual(report.projected_gross_leverage, 5.5)

    def test_asset_class_leverage_breach_veto(self):
        # Active Equity = $150k Long, $150k Short (Gross 3.0x, Net 0.0x)
        # Proposed: Buy $100k AAPL (Equity) -> Proj Equity = $400k (2.5x > 2.0x Equity limit) -> REJECTED_ASSET_CLASS_LEVERAGE_BREACH!
        positions = [
            PositionSpec("AAPL", "EQUITY", "BUY", 150_000.0),
            PositionSpec("TSLA", "EQUITY", "SELL", 150_000.0)
        ]
        order = ProposedOrderSpec("MSFT", "EQUITY", "BUY", 100_000.0)
        report = self.engine.audit_proposed_order(160_000.0, positions, order)

        self.assertEqual(report.status, "REJECTED_ASSET_CLASS_LEVERAGE_BREACH")
        self.assertFalse(report.is_asset_class_limit_passed)
        self.assertEqual(report.projected_asset_class_leverage, 2.5)

if __name__ == '__main__':
    unittest.main()
