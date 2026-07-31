import unittest
from lse_millennium_exchange_api import (
    LseMillenniumExchangeApiEngine, LseOrderPayload, LseOrderReport
)

class TestLseMillenniumExchangeApiEngine(unittest.TestCase):

    def setUp(self):
        self.engine = LseMillenniumExchangeApiEngine()

    def test_lse_tick_size_tiers(self):
        # 5.0 GBX -> 0.01 GBX Tick
        self.assertEqual(self.engine.get_lse_tick_size_gbx(5.0), 0.01)
        # 25.0 GBX -> 0.05 GBX Tick
        self.assertEqual(self.engine.get_lse_tick_size_gbx(25.0), 0.05)
        # 250.0 GBX -> 0.20 GBX Tick
        self.assertEqual(self.engine.get_lse_tick_size_gbx(250.0), 0.20)
        # 2650.0 GBX (Shell tier) -> 1.00 GBX Tick
        self.assertEqual(self.engine.get_lse_tick_size_gbx(2650.0), 1.00)

    def test_valid_shell_order(self):
        # Shell (SHEL) @ 2,650.00 GBX (£26.50), 1,000 shares -> Notional = £26,500.00 -> VALIDATED!
        payload = LseOrderPayload("SHEL", "BUY", price_gbx=2650.0, quantity=1000, currency="GBX")
        report = self.engine.validate_and_route_order(payload)

        self.assertEqual(report.status, "LSE_ORDER_VALIDATED")
        self.assertEqual(report.tidm, "SHEL")
        self.assertEqual(report.applicable_tick_size_gbx, 1.00)
        self.assertEqual(report.total_notional_gbp, 26500.0)
        self.assertTrue(report.is_price_tick_valid)

    def test_invalid_currency_rejection(self):
        # Submitting in GBP instead of GBX -> INVALID_CURRENCY!
        payload = LseOrderPayload("SHEL", "BUY", price_gbx=26.50, quantity=1000, currency="GBP")
        report = self.engine.validate_and_route_order(payload)

        self.assertEqual(report.status, "INVALID_CURRENCY")
        self.assertFalse(report.is_price_tick_valid)

    def test_invalid_tick_size_rejection(self):
        # 2650.35 GBX violates 1.00 GBX tick size -> INVALID_TICK_SIZE!
        payload = LseOrderPayload("SHEL", "BUY", price_gbx=2650.35, quantity=1000, currency="GBX")
        report = self.engine.validate_and_route_order(payload)

        self.assertEqual(report.status, "INVALID_TICK_SIZE")
        self.assertFalse(report.is_price_tick_valid)

if __name__ == '__main__':
    unittest.main()
