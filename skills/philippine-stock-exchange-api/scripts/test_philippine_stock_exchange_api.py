import unittest
from philippine_stock_exchange_api import (
    Config, IntegrationEngine, PhilippineStockExchangeEngine, PSEOrderRequest, PSEReport
)

class TestPhilippineStockExchangeEngine(unittest.TestCase):

    def setUp(self):
        self.config = Config(api_key="test_key")
        self.legacy_engine = IntegrationEngine(self.config)
        self.pse_engine = PhilippineStockExchangeEngine(self.config)

    def test_legacy_connect_and_process(self):
        self.assertTrue(self.legacy_engine.connect())
        self.assertEqual(self.legacy_engine.process(), "success")

    def test_valid_pse_order_high_tier(self):
        # SM Investments @ PHP 900.00 -> Tier: Price PHP 500-999.5 => Tick 0.50, Lot 10
        order = PSEOrderRequest("SM", "BUY", 900.00, 100, prev_close_price=890.00)
        report = self.pse_engine.validate_pse_order(order)

        self.assertEqual(report.status, "ORDER_VALID_COMPLIANT")
        self.assertEqual(report.required_board_lot, 10)
        self.assertEqual(report.required_tick_size, 0.50)
        self.assertTrue(report.is_valid_board_lot)
        self.assertTrue(report.is_valid_tick_size)

    def test_invalid_board_lot_rejection(self):
        # Ayala Corp @ PHP 700.00 -> Req Lot 10. Order Qty = 15 (Not divisible by 10)
        order = PSEOrderRequest("AC", "BUY", 700.00, 15, prev_close_price=700.00)
        report = self.pse_engine.validate_pse_order(order)

        self.assertEqual(report.status, "INVALID_BOARD_LOT")
        self.assertFalse(report.is_valid_board_lot)

    def test_price_ceiling_breach(self):
        # Prev close PHP 100.00 -> Ceiling = PHP 150.00. Order price = PHP 160.00
        order = PSEOrderRequest("BDO", "BUY", 160.00, 100, prev_close_price=100.00)
        report = self.pse_engine.validate_pse_order(order)

        self.assertEqual(report.status, "PRICE_BAND_BREACH")
        self.assertFalse(report.is_within_price_band)

if __name__ == '__main__':
    unittest.main()
