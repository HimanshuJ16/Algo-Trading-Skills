import unittest
from multi_order_netting_before_routing import (
    MultiOrderNettingEngine, InternalOrder, MarketQuote, NettingReport
)

class TestMultiOrderNettingEngine(unittest.TestCase):

    def setUp(self):
        self.engine = MultiOrderNettingEngine()

    def test_multi_order_internal_netting_and_residual_routing(self):
        # Market Quote: Bid $150.00 / Ask $150.10 (Mid $150.05, Spread $0.10)
        # Orders:
        # Strategy A: Buy 500 AAPL
        # Strategy B: Sell 300 AAPL
        # Strategy C: Buy 200 AAPL
        # Total Buy = 700, Total Sell = 300 -> Matched = 300 @ $150.05, Net Residual = Buy 400 AAPL!
        quote = MarketQuote("AAPL", bid_price=150.00, ask_price=150.10, fee_per_share_usd=0.003)

        orders = [
            InternalOrder("ORD_1", "STRAT_A", "AAPL", "BUY", 500),
            InternalOrder("ORD_2", "STRAT_B", "AAPL", "SELL", 300),
            InternalOrder("ORD_3", "STRAT_C", "AAPL", "BUY", 200),
        ]

        report = self.engine.net_and_route_orders(quote, orders)

        self.assertEqual(report.status, "NETTING_SUCCESS")
        self.assertEqual(report.internal_matched_quantity, 300)
        self.assertEqual(report.internal_match_price, 150.05)
        self.assertEqual(report.net_external_quantity, 400)
        self.assertIsNotNone(report.external_order)
        self.assertEqual(report.external_order.side, "BUY")
        self.assertEqual(report.external_order.quantity, 400)

        # Fee savings = 2 * 300 * 0.003 = $1.80 | Spread savings = 300 * 0.10 = $30.00 | Total = $31.80
        self.assertEqual(report.exchange_fee_savings_usd, 1.80)
        self.assertEqual(report.spread_savings_usd, 30.00)
        self.assertEqual(report.total_cost_savings_usd, 31.80)

    def test_perfect_internal_match_zero_residual(self):
        # Buy 500 AAPL vs Sell 500 AAPL -> Matched = 500, External Residual = 0 (None)
        quote = MarketQuote("AAPL", bid_price=150.00, ask_price=150.10, fee_per_share_usd=0.003)
        orders = [
            InternalOrder("ORD_1", "STRAT_A", "AAPL", "BUY", 500),
            InternalOrder("ORD_2", "STRAT_B", "AAPL", "SELL", 500),
        ]

        report = self.engine.net_and_route_orders(quote, orders)

        self.assertEqual(report.internal_matched_quantity, 500)
        self.assertEqual(report.net_external_quantity, 0)
        self.assertIsNone(report.external_order)

if __name__ == '__main__':
    unittest.main()
