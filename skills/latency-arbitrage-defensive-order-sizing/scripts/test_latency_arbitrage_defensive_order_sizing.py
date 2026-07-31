import unittest
from latency_arbitrage_defensive_order_sizing import (
    LatencyArbitrageDefensiveSizingEngine, MarketStateSpec, DefensiveSizingReport
)

class TestLatencyArbitrageDefensiveSizingEngine(unittest.TestCase):

    def setUp(self):
        self.engine = LatencyArbitrageDefensiveSizingEngine(
            max_sniping_prob_threshold=0.50, lambda_scaling=0.50
        )

    def test_normal_market_defensive_quote_sizing(self):
        # 1.0 ms latency gap, 20% vol -> P_snipe = 1 - exp(-0.5 * 0.20 * 1.0) = 1 - exp(-0.1) ~ 0.0952 (9.52% risk)
        spec = MarketStateSpec(
            symbol="AAPL", base_quote_qty=1000, latency_gap_ms=1.0,
            volatility_annualized=0.20, spread_bps=2.0, min_lot_size=100
        )
        report = self.engine.calculate_defensive_sizing(spec)

        self.assertEqual(report.status, "QUOTE_DEFENSIVELY_SIZED")
        self.assertFalse(report.is_quote_canceled)
        self.assertAlmostEqual(report.sniping_probability, 0.0952, places=3)
        self.assertGreater(report.defensive_quote_qty, 850)
        self.assertLess(report.defensive_quote_qty, 1000)

    def test_high_sniping_risk_quote_cancellation(self):
        # 25 ms latency gap, 80% vol -> P_snipe > 0.99 (> 50% limit) -> HIGH_SNIPING_RISK_CANCEL!
        spec = MarketStateSpec(
            symbol="TSLA", base_quote_qty=1000, latency_gap_ms=25.0,
            volatility_annualized=0.80, spread_bps=5.0, min_lot_size=100
        )
        report = self.engine.calculate_defensive_sizing(spec)

        self.assertEqual(report.status, "HIGH_SNIPING_RISK_CANCEL")
        self.assertTrue(report.is_quote_canceled)
        self.assertEqual(report.defensive_quote_qty, 0)
        self.assertGreater(report.sniping_probability, 0.50)

if __name__ == '__main__':
    unittest.main()
