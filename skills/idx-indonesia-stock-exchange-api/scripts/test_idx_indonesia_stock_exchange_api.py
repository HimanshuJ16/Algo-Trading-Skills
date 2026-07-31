import unittest
from idx_indonesia_stock_exchange_api import (
    IdxStockExchangeApiEngine, IdxOrderPayload, IdxOrderReport
)

class TestIdxStockExchangeApiEngine(unittest.TestCase):

    def setUp(self):
        self.engine = IdxStockExchangeApiEngine(max_auto_rejection_pct=25.0)

    def test_fraksi_harga_tick_sizes(self):
        # Price Rp 150 -> Rp 1
        self.assertEqual(self.engine.get_idx_fraksi_harga(150.0), 1.0)
        # Price Rp 350 -> Rp 2
        self.assertEqual(self.engine.get_idx_fraksi_harga(350.0), 2.0)
        # Price Rp 1,500 -> Rp 5
        self.assertEqual(self.engine.get_idx_fraksi_harga(1500.0), 5.0)
        # Price Rp 3,500 -> Rp 10
        self.assertEqual(self.engine.get_idx_fraksi_harga(3500.0), 10.0)
        # Price Rp 10,000 -> Rp 25
        self.assertEqual(self.engine.get_idx_fraksi_harga(10000.0), 25.0)

    def test_valid_idx_order_routing(self):
        # BBCA @ Rp 10,000 (Tick Rp 25), 500 shares (5 Lots), Ref Price Rp 10,000 -> VALID!
        payload = IdxOrderPayload("BBCA", "RG", "BUY", price=10000.0, quantity=500, reference_price=10000.0)
        report = self.engine.validate_and_route_order(payload)

        self.assertEqual(report.ticker, "BBCA")
        self.assertEqual(report.status, "IDX_ORDER_VALIDATED")
        self.assertEqual(report.lots_count, 5)
        self.assertTrue(report.is_price_tick_valid)
        self.assertTrue(report.is_board_lot_valid)

    def test_invalid_board_lot_rejection(self):
        # 150 shares (not a multiple of 100) -> INVALID_BOARD_LOT!
        payload = IdxOrderPayload("TLKM", "RG", "BUY", price=4000.0, quantity=150, reference_price=4000.0)
        report = self.engine.validate_and_route_order(payload)

        self.assertEqual(report.status, "INVALID_BOARD_LOT")
        self.assertFalse(report.is_board_lot_valid)

    def test_invalid_fraksi_harga_rejection(self):
        # Rp 10,005 violates Rp 25 tick size for prices >= Rp 5,000 -> INVALID_TICK_SIZE!
        payload = IdxOrderPayload("BBCA", "RG", "BUY", price=10005.0, quantity=500, reference_price=10000.0)
        report = self.engine.validate_and_route_order(payload)

        self.assertEqual(report.status, "INVALID_TICK_SIZE")
        self.assertFalse(report.is_price_tick_valid)

if __name__ == '__main__':
    unittest.main()
