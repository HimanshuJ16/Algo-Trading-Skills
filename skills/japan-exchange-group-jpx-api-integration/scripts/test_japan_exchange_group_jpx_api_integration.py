import unittest
from japan_exchange_group_jpx_api_integration import (
    JpxStockExchangeApiEngine, JpxOrderPayload, JpxOrderReport
)

class TestJpxStockExchangeApiEngine(unittest.TestCase):

    def setUp(self):
        self.engine = JpxStockExchangeApiEngine(max_daily_price_limit_pct=20.0)

    def test_tse_tick_size_tiers(self):
        # JPY 2,500 -> Tick JPY 1.0
        self.assertEqual(self.engine.get_tse_tick_size(2500.0), 1.0)
        # JPY 4,000 -> Tick JPY 5.0
        self.assertEqual(self.engine.get_tse_tick_size(4000.0), 5.0)
        # JPY 8,000 -> Tick JPY 10.0
        self.assertEqual(self.engine.get_tse_tick_size(8000.0), 10.0)
        # JPY 15,000 -> Tick JPY 50.0
        self.assertEqual(self.engine.get_tse_tick_size(15000.0), 50.0)

    def test_valid_jpx_order_routing(self):
        # Toyota Motor (7203) @ JPY 2,500 (Tick JPY 1.0), 500 shares (5 Units) -> VALID!
        payload = JpxOrderPayload("7203", "BUY", price_jpy=2500.0, quantity=500, reference_price_jpy=2500.0)
        report = self.engine.validate_and_route_order(payload)

        self.assertEqual(report.local_code, "7203")
        self.assertEqual(report.status, "JPX_ORDER_VALIDATED")
        self.assertEqual(report.board_lots_count, 5)
        self.assertTrue(report.is_price_tick_valid)
        self.assertTrue(report.is_board_lot_valid)

    def test_invalid_board_lot_rejection(self):
        # 150 shares (not a multiple of 100) -> INVALID_BOARD_LOT!
        payload = JpxOrderPayload("6758", "BUY", price_jpy=12000.0, quantity=150, reference_price_jpy=12000.0)
        report = self.engine.validate_and_route_order(payload)

        self.assertEqual(report.status, "INVALID_BOARD_LOT")
        self.assertFalse(report.is_board_lot_valid)

    def test_invalid_tick_size_rejection(self):
        # JPY 4,002 violates JPY 5.0 tick size for prices JPY 3,000-5,000 -> INVALID_TICK_SIZE!
        payload = JpxOrderPayload("7203", "BUY", price_jpy=4002.0, quantity=500, reference_price_jpy=4000.0)
        report = self.engine.validate_and_route_order(payload)

        self.assertEqual(report.status, "INVALID_TICK_SIZE")
        self.assertFalse(report.is_price_tick_valid)

if __name__ == '__main__':
    unittest.main()
