import unittest
from taiwan_stock_exchange_twse_api import Config, IntegrationEngine, TWSEOrder, TaiwanStockExchangeTWSEEngine


class TestTaiwanStockExchangeTWSEEngine(unittest.TestCase):
    def setUp(self):
        self.config = Config(api_key="twse_test_key", fini_id="FINI-TW-10023")
        self.engine = TaiwanStockExchangeTWSEEngine(self.config)

    def test_connect_and_process(self):
        self.assertTrue(self.engine.connect())
        self.assertEqual(self.engine.process(), "success")

    def test_tick_size_and_price_alignment(self):
        # Under NT$50: tick = NT$0.01
        self.assertEqual(self.engine.get_tick_size(45.50), 0.01)
        self.assertTrue(self.engine.validate_price_tick(45.52))

        # NT$50 and above: tick = NT$0.05
        self.assertEqual(self.engine.get_tick_size(550.0), 0.05)
        self.assertTrue(self.engine.validate_price_tick(550.05))
        self.assertFalse(self.engine.validate_price_tick(550.03))

    def test_daily_price_limit(self):
        prev_close = 100.0
        # 10% limit range: [90.0, 110.0]
        self.assertTrue(self.engine.validate_price_limit(105.0, prev_close))
        self.assertFalse(self.engine.validate_price_limit(115.0, prev_close))
        self.assertFalse(self.engine.validate_price_limit(85.0, prev_close))

    def test_board_lot_and_short_sale_rules(self):
        prev_close = 100.0

        # Invalid board lot (500 shares, odd_lot=False)
        order_invalid_lot = TWSEOrder(
            order_id="ORD-001",
            symbol="2330",  # TSMC
            side="BUY",
            quantity=500,
            price=100.0,
            odd_lot=False,
        )
        res1 = self.engine.submit_order(order_invalid_lot, prev_close)
        self.assertEqual(res1["status"], "REJECTED")
        self.assertIn("1,000 shares", res1["reason"])

        # Naked short sell without locate
        order_naked_short = TWSEOrder(
            order_id="ORD-002",
            symbol="2330",
            side="SHORT_SELL",
            quantity=1000,
            price=100.0,
        )
        res2 = self.engine.submit_order(order_naked_short, prev_close, locate_available=False)
        self.assertEqual(res2["status"], "REJECTED")
        self.assertIn("Naked short sale prohibited", res2["reason"])

        # Valid order with locate
        order_valid = TWSEOrder(
            order_id="ORD-003",
            symbol="2330",
            side="SHORT_SELL",
            quantity=2000,
            price=100.0,
        )
        res3 = self.engine.submit_order(order_valid, prev_close, locate_available=True)
        self.assertEqual(res3["status"], "SUBMITTED")


if __name__ == "__main__":
    unittest.main()
