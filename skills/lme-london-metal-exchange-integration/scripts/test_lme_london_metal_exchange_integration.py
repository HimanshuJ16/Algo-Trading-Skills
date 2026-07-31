import unittest
from lme_london_metal_exchange_integration import (
    LmeExchangeApiEngine, LmeOrderPayload, LmeOrderReport
)

class TestLmeExchangeApiEngine(unittest.TestCase):

    def setUp(self):
        self.engine = LmeExchangeApiEngine()

    def test_valid_copper_3m_order(self):
        # 10 lots 3M Copper (CA) @ $9,250.50/MT -> 10 * 25 MT = 250 MT. Notional = 250 * 9,250.50 = $2,312,625.00 -> VALIDATED!
        payload = LmeOrderPayload("CA", "3M", "BUY", price_usd_per_mt=9250.50, lots=10)
        report = self.engine.validate_and_route_order(payload)

        self.assertEqual(report.status, "LME_ORDER_VALIDATED")
        self.assertEqual(report.metal_code, "CA")
        self.assertEqual(report.metal_name, "Copper Grade A")
        self.assertEqual(report.prompt_date, "3M")
        self.assertEqual(report.total_tonnage_mt, 250.0)
        self.assertEqual(report.total_notional_usd, 2312625.0)
        self.assertTrue(report.is_price_tick_valid)

    def test_valid_nickel_tonnage_order(self):
        # 10 lots Nickel (NI) @ $16,500.00/MT -> 10 * 6 MT = 60 MT (Nickel is 6 MT/lot!). Notional = 60 * 16,500 = $990,000.00 -> VALIDATED!
        payload = LmeOrderPayload("NI", "CASH", "BUY", price_usd_per_mt=16500.00, lots=10)
        report = self.engine.validate_and_route_order(payload)

        self.assertEqual(report.status, "LME_ORDER_VALIDATED")
        self.assertEqual(report.total_tonnage_mt, 60.0)
        self.assertEqual(report.total_notional_usd, 990000.0)

    def test_invalid_price_tick_rejection(self):
        # $9,250.23/MT violates $0.50/MT tick size -> INVALID_TICK_SIZE!
        payload = LmeOrderPayload("CA", "3M", "BUY", price_usd_per_mt=9250.23, lots=10)
        report = self.engine.validate_and_route_order(payload)

        self.assertEqual(report.status, "INVALID_TICK_SIZE")
        self.assertFalse(report.is_price_tick_valid)

if __name__ == '__main__':
    unittest.main()
