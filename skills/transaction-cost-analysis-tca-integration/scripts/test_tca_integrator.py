"""
Unit tests for transaction-cost-analysis-tca-integration skill.
"""
import unittest
from tca_integrator import TCABacktestIntegrator


class TestTCABacktestIntegrator(unittest.TestCase):

    def setUp(self):
        self.tca = TCABacktestIntegrator(
            market_impact_gamma=15.0,
            fixed_commission_bps=2.5,
            max_acceptable_shortfall_bps=50.0,
        )

    def test_trade_cost_decomposition(self):
        # 10,000 shares buy order on 100,000 ADV stock (10% volume) -> sqrt(0.10) * 15 = 4.74 bps impact
        trade = self.tca.analyze_trade(
            symbol="AAPL",
            action="BUY",
            order_size=10000.0,
            adv=100000.0,
            p_decision=150.00,
            p_arrival=150.02,  # +1.33 bps delay
            p_fill=150.10,
            spread=0.04,        # 1.33 bps spread cross
        )

        self.assertAlmostEqual(trade.delay_cost_bps, 1.33, delta=0.5)
        self.assertAlmostEqual(trade.market_impact_bps, 4.74, delta=0.5)
        self.assertGreater(trade.total_shortfall_bps, 0.0)

    def test_portfolio_viability_under_tca(self):
        trades = [
            self.tca.analyze_trade("AAPL", "BUY", 1000, 100000, 150.0, 150.0, 150.05, 0.02)
            for _ in range(5)
        ]

        summary = self.tca.evaluate_portfolio_tca(trades, gross_return_pct=15.0)

        self.assertEqual(summary.total_trades_analyzed, 5)
        self.assertTrue(summary.is_strategy_viable)
        self.assertLess(summary.net_tca_return_pct, 15.0)


if __name__ == "__main__":
    unittest.main()
