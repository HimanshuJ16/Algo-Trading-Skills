import unittest
from korea_exchange_krx_api_integration import (
    KoreaExchangeKrxApiEngine, KrxOrderPayload, KrxOrderReport
)

class TestKoreaExchangeKrxApiEngine(unittest.TestCase):

    def setUp(self):
        self.engine = KoreaExchangeKrxApiEngine(max_daily_price_limit_pct=30.0)

    def test_krx_tick_size_tiers(self):
        # KRW 3,500 -> KRW 5 Tick
        self.assertEqual(self.engine.get_krx_tick_size_krw(3500.0), 5.0)
        # KRW 35,000 -> KRW 50 Tick
        self.assertEqual(self.engine.get_krx_tick_size_krw(35000.0), 50.0)
        # KRW 150,000 (Samsung Electronics tier) -> KRW 500 Tick
        self.assertEqual(self.engine.get_krx_tick_size_krw(150000.0), 500.0)
        # KRW 600,000 -> KRW 1,000 Tick
        self.assertEqual(self.engine.get_krx_tick_size_krw(600000.0), 1000.0)

    def test_valid_samsung_electronics_order(self):
        # Samsung Electronics (5930 -> 005930) @ KRW 150,000 (KRW 500 Tick), 10 shares -> VALID!
        payload = KrxOrderPayload("5930", "BUY", price_krw=150000.0, quantity=10, reference_price_krw=150000.0)
        report = self.engine.validate_and_route_order(payload)

        self.assertEqual(report.status, "KRX_ORDER_VALIDATED")
        self.assertEqual(report.local_code, "005930")
        self.assertEqual(report.applicable_tick_size_krw, 500.0)
        self.assertTrue(report.is_price_tick_valid)
        self.assertTrue(report.is_price_limit_valid)

    def test_invalid_tick_size_rejection(self):
        # KRW 150,200 violates KRW 500 tick size for Samsung tier -> INVALID_TICK_SIZE!
        payload = KrxOrderPayload("005930", "BUY", price_krw=150200.0, quantity=10, reference_price_krw=150000.0)
        report = self.engine.validate_and_route_order(payload)

        self.assertEqual(report.status, "INVALID_TICK_SIZE")
        self.assertFalse(report.is_price_tick_valid)

    def test_price_limit_exceeded_rejection(self):
        # KRW 210,000 vs Ref KRW 150,000 (+40% deviation > 30% limit) -> PRICE_LIMIT_EXCEEDED!
        payload = KrxOrderPayload("005930", "BUY", price_krw=210000.0, quantity=10, reference_price_krw=150000.0)
        report = self.engine.validate_and_route_order(payload)

        self.assertEqual(report.status, "PRICE_LIMIT_EXCEEDED")
        self.assertFalse(report.is_price_limit_valid)

if __name__ == '__main__':
    unittest.main()
