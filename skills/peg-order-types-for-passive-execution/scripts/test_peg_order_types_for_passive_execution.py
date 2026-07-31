import unittest
from peg_order_types_for_passive_execution import (
    PegOrderTypesForPassiveExecutionConfig,
    PegOrderTypesForPassiveExecutionEngine,
    PegOrder,
    NBBOQuote,
    PegOrderReport
)

class TestPegOrderTypesForPassiveExecution(unittest.TestCase):

    def setUp(self):
        self.config = PegOrderTypesForPassiveExecutionConfig(enabled=True, threshold=100.0, size=50)
        self.engine = PegOrderTypesForPassiveExecutionEngine(self.config)

    def test_evaluate_triggers_order_legacy(self):
        market_data = {"symbol": "AAPL", "price": 105.0}
        orders = self.engine.evaluate(market_data)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["qty"], 50)

    def test_evaluate_no_trigger_legacy(self):
        market_data = {"symbol": "AAPL", "price": 95.0}
        orders = self.engine.evaluate(market_data)
        self.assertEqual(len(orders), 0)

    def test_disabled_engine_legacy(self):
        self.engine.config.enabled = False
        market_data = {"symbol": "AAPL", "price": 105.0}
        orders = self.engine.evaluate(market_data)
        self.assertEqual(len(orders), 0)

    def test_pegged_order_pricing_and_caps(self):
        nbbo = NBBOQuote("AAPL", best_bid=100.0, best_ask=100.10)

        # Primary Peg BUY with +$0.01 offset -> $100.01
        order1 = PegOrder("ORD1", "AAPL", "BUY", "PRIMARY", offset=0.01)
        report1 = self.engine.calculate_pegged_price(order1, nbbo)
        self.assertEqual(report1.effective_limit_price, 100.01)
        self.assertFalse(report1.is_cap_active)

        # Midpoint Peg -> $100.05
        order2 = PegOrder("ORD2", "AAPL", "BUY", "MIDPOINT")
        report2 = self.engine.calculate_pegged_price(order2, nbbo)
        self.assertEqual(report2.effective_limit_price, 100.05)

        # Market Peg BUY with +$0.05 offset capped at $100.12 -> Raw $100.15 clamped to $100.12
        order3 = PegOrder("ORD3", "AAPL", "BUY", "MARKET", offset=0.05, limit_cap=100.12)
        report3 = self.engine.calculate_pegged_price(order3, nbbo)
        self.assertEqual(report3.effective_limit_price, 100.12)
        self.assertTrue(report3.is_cap_active)

if __name__ == '__main__':
    unittest.main()
