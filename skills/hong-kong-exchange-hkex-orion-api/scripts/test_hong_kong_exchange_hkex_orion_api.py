import unittest
from hong_kong_exchange_hkex_orion_api import (
    HkexOrionApiEngine, HkexOrderPayload, HkexOrionOrderReport
)

class TestHkexOrionApiEngine(unittest.TestCase):

    def setUp(self):
        self.engine = HkexOrionApiEngine()

    def test_stock_code_zero_padding(self):
        self.assertEqual(self.engine.format_hkex_stock_code("700"), "00700")
        self.assertEqual(self.engine.format_hkex_stock_code("5"), "00005")
        self.assertEqual(self.engine.format_hkex_stock_code("80700"), "80700")

    def test_hkex_spread_table_tick_size_tiers(self):
        # Price $0.40 -> $0.005
        self.assertEqual(self.engine.get_hkex_spread_table_tick_size(0.40), 0.005)
        # Price $15.00 -> $0.020
        self.assertEqual(self.engine.get_hkex_spread_table_tick_size(15.00), 0.020)
        # Price $300.20 -> $0.200
        self.assertEqual(self.engine.get_hkex_spread_table_tick_size(300.20), 0.200)

    def test_valid_order_preparation(self):
        # Tencent (00700) @ $300.20 (Tick 0.20, Qty 200, Board Lot 100) -> VALID!
        payload = HkexOrderPayload("700", "BUY", "LIMIT", price=300.20, quantity=200, board_lot_size=100)
        report = self.engine.validate_and_prepare_order(payload)

        self.assertEqual(report.formatted_stock_code, "00700")
        self.assertEqual(report.status, "ORDER_VALIDATED")
        self.assertTrue(report.is_price_tick_valid)
        self.assertTrue(report.is_board_lot_multiple)

    def test_invalid_tick_size_rejection(self):
        # Tencent @ $300.15 (Tick size for $300 is $0.20, so $300.15 is invalid tick!)
        payload = HkexOrderPayload("700", "BUY", "LIMIT", price=300.15, quantity=200, board_lot_size=100)
        report = self.engine.validate_and_prepare_order(payload)

        self.assertEqual(report.status, "INVALID_TICK_SIZE")
        self.assertFalse(report.is_price_tick_valid)

if __name__ == '__main__':
    unittest.main()
