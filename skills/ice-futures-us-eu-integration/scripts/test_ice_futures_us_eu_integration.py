import unittest
from ice_futures_us_eu_integration import (
    IceFuturesIntegrationEngine, IceOrderPayload, IceFuturesOrderReport
)

class TestIceFuturesIntegrationEngine(unittest.TestCase):

    def setUp(self):
        self.engine = IceFuturesIntegrationEngine()

    def test_brent_crude_order_routing(self):
        # Brent Crude Dec 2026 (BZ26), IFEU, Multiplier = 1000, Price = $75.50, Qty = 10 -> Notional $755,000!
        payload = IceOrderPayload("B", "Z", 2026, "BUY", price=75.50, quantity=10, bbo_price=75.40)
        report = self.engine.process_and_route_order(payload)

        self.assertEqual(report.formatted_ice_symbol, "BZ26")
        self.assertEqual(report.fix_tag_207_mic, "IFEU")
        self.assertEqual(report.fix_tag_200_maturity, "202612")
        self.assertEqual(report.notional_value_usd, 755_000.0)
        self.assertEqual(report.status, "ORDER_ROUTED_APPROVED")
        self.assertTrue(report.is_price_tick_valid)

    def test_sugar_no_11_us_routing(self):
        # Sugar No. 11 (SB), IFUS, Multiplier = 112,000, Price = 0.2250, Qty = 5
        payload = IceOrderPayload("SB", "V", 2026, "SELL", price=0.2250, quantity=5, bbo_price=0.2250)
        report = self.engine.process_and_route_order(payload)

        self.assertEqual(report.formatted_ice_symbol, "SBV26")
        self.assertEqual(report.fix_tag_207_mic, "IFUS")
        self.assertEqual(report.status, "ORDER_ROUTED_APPROVED")

    def test_ncr_limit_exceeded_rejection(self):
        # Price $80.00 vs BBO $75.00 -> 500 ticks deviation > max 100 ticks -> NCR_LIMIT_EXCEEDED!
        payload = IceOrderPayload("B", "Z", 2026, "BUY", price=80.00, quantity=10, bbo_price=75.00)
        report = self.engine.process_and_route_order(payload)

        self.assertEqual(report.status, "NCR_LIMIT_EXCEEDED")
        self.assertFalse(report.is_ncr_reasonability_valid)

if __name__ == '__main__':
    unittest.main()
