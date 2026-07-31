import unittest
from hong_kong_sfc_algorithmic_trading_guidelines import (
    HkSfcAlgorithmicTradingEngine, HkSfcOrderRequest, HkSfcComplianceReport
)

class TestHkSfcAlgorithmicTradingEngine(unittest.TestCase):

    def setUp(self):
        self.engine = HkSfcAlgorithmicTradingEngine(
            max_order_value_hkd=10_000_000.0, max_price_deviation_pct=5.0
        )

    def test_sfc_compliant_order_approval(self):
        req = HkSfcOrderRequest(
            algo_id="HK_MOMENTUM_01", stock_code="00700", side="BUY",
            order_price=300.00, order_quantity=10000, market_last_price=300.00,
            developer_registration_active=True
        )
        report = self.engine.audit_sfc_compliance(req)

        self.assertEqual(report.status, "SFC_COMPLIANT_APPROVED")
        self.assertEqual(report.order_value_hkd, 3_000_000.0)
        self.assertFalse(report.is_kill_switch_active)

    def test_naked_short_selling_rejection(self):
        # Short Sell without locate borrow confirmation -> REJECTED_ILLEGAL_NAKED_SHORT!
        req = HkSfcOrderRequest(
            algo_id="HK_ARBITRAGE_02", stock_code="09988", side="SHORT_SELL",
            order_price=100.00, order_quantity=5000, market_last_price=100.00,
            is_short_sell=True, has_locate_borrow=False
        )
        report = self.engine.audit_sfc_compliance(req)

        self.assertEqual(report.status, "REJECTED_ILLEGAL_NAKED_SHORT")
        self.assertFalse(report.is_short_sell_legal)

    def test_price_deviation_limit_rejection(self):
        # Order Price 320.0 vs Market 300.0 -> Deviation 6.67% > 5.0% -> REJECTED!
        req = HkSfcOrderRequest(
            algo_id="HK_MOMENTUM_01", stock_code="00700", side="BUY",
            order_price=320.00, order_quantity=1000, market_last_price=300.00
        )
        report = self.engine.audit_sfc_compliance(req)

        self.assertEqual(report.status, "REJECTED_PRICE_DEVIATION_LIMIT")
        self.assertGreater(report.price_deviation_pct, 5.0)

    def test_kill_switch_blocks_orders(self):
        self.engine.trigger_sfc_kill_switch()
        req = HkSfcOrderRequest(
            algo_id="HK_MOMENTUM_01", stock_code="00700", side="BUY",
            order_price=300.00, order_quantity=1000, market_last_price=300.00
        )
        report = self.engine.audit_sfc_compliance(req)

        self.assertEqual(report.status, "REJECTED_KILL_SWITCH_ACTIVE")

if __name__ == '__main__':
    unittest.main()
