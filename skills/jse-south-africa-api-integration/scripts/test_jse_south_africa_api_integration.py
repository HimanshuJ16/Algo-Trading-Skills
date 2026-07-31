import unittest
from jse_south_africa_api_integration import (
    JseSouthAfricaApiEngine, JseOrderPayload, JseOrderReport
)

class TestJseSouthAfricaApiEngine(unittest.TestCase):

    def setUp(self):
        self.engine = JseSouthAfricaApiEngine(max_daily_price_limit_pct=15.0)

    def test_jse_tick_size_tiers_in_zac(self):
        # 5,000 ZAC (ZAR 50) -> 1.0 ZAC Tick
        self.assertEqual(self.engine.get_jse_tick_size_zac(5000.0), 1.0)
        # 15,000 ZAC (ZAR 150) -> 5.0 ZAC Tick
        self.assertEqual(self.engine.get_jse_tick_size_zac(15000.0), 5.0)

    def test_valid_jse_naspers_order(self):
        # Naspers (NPN) @ 85,500 ZAC (ZAR 855.00), 100 shares -> Notional ZAR 85,500.00 -> VALID!
        payload = JseOrderPayload("NPN", "BUY", price_zac=85500.0, quantity=100, reference_price_zac=85500.0)
        report = self.engine.validate_and_route_order(payload)

        self.assertEqual(report.status, "JSE_ORDER_VALIDATED")
        self.assertEqual(report.alpha_code, "NPN")
        self.assertEqual(report.equivalent_price_zar, 855.00)
        self.assertEqual(report.notional_value_zar, 85500.00)
        self.assertEqual(report.applicable_tick_size_zac, 5.0)
        self.assertTrue(report.is_price_tick_valid)

    def test_invalid_tick_size_rejection_zac(self):
        # 85,502 ZAC violates 5.0 ZAC tick size for prices >= 10,000 ZAC -> INVALID_TICK_SIZE!
        payload = JseOrderPayload("NPN", "BUY", price_zac=85502.0, quantity=100, reference_price_zac=85500.0)
        report = self.engine.validate_and_route_order(payload)

        self.assertEqual(report.status, "INVALID_TICK_SIZE")
        self.assertFalse(report.is_price_tick_valid)

if __name__ == '__main__':
    unittest.main()
