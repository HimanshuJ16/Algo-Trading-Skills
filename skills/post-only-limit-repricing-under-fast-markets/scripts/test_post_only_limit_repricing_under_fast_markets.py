import unittest
from post_only_limit_repricing_under_fast_markets import (
    Config, MainEngine, FastMarketPostOnlyRepricer,
    MarketState, OrderRequest, FastMarketRepriceReport
)

class TestPostOnlyLimitRepricingUnderFastMarkets(unittest.TestCase):

    def setUp(self):
        self.config = Config(param1=2.0, max_reprice_attempts=3)
        self.legacy_engine = MainEngine(self.config)
        self.repricer = FastMarketPostOnlyRepricer(self.config)

    def test_legacy_execute(self):
        self.assertTrue(self.legacy_engine.execute())

    def test_legacy_process_data(self):
        self.assertEqual(self.legacy_engine.process_data(10.0), 20.0)

    def test_passive_order_accepted_without_reprice(self):
        market = MarketState("BTCUSDT", best_bid=60000.0, best_ask=60010.0)
        order = OrderRequest("ORD_01", "BUY", 1.0, desired_price=60000.0)

        report = self.repricer.process_order(market, order)
        self.assertEqual(report.status, "POST_ONLY_ACCEPTED_PASSIVE")
        self.assertFalse(report.is_repriced)
        self.assertEqual(report.final_limit_price, 60000.0)

    def test_fast_market_spread_crossed_passive_reprice(self):
        # BUY order desired_price 60015 >= best_ask 60010 (crossed spread) in fast market (25 ticks/sec)
        market = MarketState("BTCUSDT", best_bid=60000.0, best_ask=60010.0, market_velocity_ticks_per_sec=25.0)
        order = OrderRequest("ORD_02", "BUY", 1.0, desired_price=60015.0)

        report = self.repricer.process_order(market, order)
        self.assertEqual(report.status, "POST_ONLY_PASSIVE_REPRICED")
        self.assertTrue(report.is_repriced)
        self.assertTrue(report.is_fast_market)
        self.assertEqual(report.final_limit_price, 60000.0) # Repriced to best_bid

    def test_reprice_attempts_exceeded(self):
        market = MarketState("BTCUSDT", best_bid=60000.0, best_ask=60010.0)
        order = OrderRequest("ORD_03", "BUY", 1.0, desired_price=60015.0, reprice_attempts=3)

        report = self.repricer.process_order(market, order)
        self.assertEqual(report.status, "REPRICE_ATTEMPTS_EXCEEDED")
        self.assertTrue(report.rejection_churn_prevented)

if __name__ == '__main__':
    unittest.main()
