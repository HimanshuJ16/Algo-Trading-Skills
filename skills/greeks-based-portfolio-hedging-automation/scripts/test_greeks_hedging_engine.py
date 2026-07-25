"""Unit tests for greeks-based-portfolio-hedging-automation."""
import unittest
from greeks_hedging_engine import GreeksPortfolioHedgingEngine, OptionPosition


class TestGreeksPortfolioHedgingEngine(unittest.TestCase):
    def setUp(self):
        self.engine = GreeksPortfolioHedgingEngine(
            max_allowed_delta_usd=50000.0,
            max_allowed_vega_usd=10000.0,
            min_rebalance_delta_usd=10000.0,
        )

    def test_delta_breach_generates_correct_hedge_order(self):
        # 1,000 Call contracts (+100,000 shares) @ $0.60 Delta on $100 stock -> Net Delta USD = +$6,000,000
        positions = [
            OptionPosition("SPY_240621_C500", "SPY", 1000.0, 0.60, 0.20, 500.0)  # 1000 * 0.60 * 500 = +$300,000
        ]

        report = self.engine.evaluate_and_hedge(positions, hedge_instrument_symbol="SPY", hedge_instrument_price=500.0)

        self.assertTrue(report.is_hedging_required)
        self.assertTrue(report.net_greeks.is_delta_breached)
        self.assertEqual(len(report.recommended_hedge_orders), 1)

        hedge = report.recommended_hedge_orders[0]
        self.assertEqual(hedge.action, "SELL")
        self.assertEqual(hedge.quantity, 600.0)  # - $300,000 / $500 = -600 shares

    def test_delta_neutral_portfolio_no_hedge(self):
        positions = [
            OptionPosition("SPY_C", "SPY", 100.0, 0.50, 0.10, 500.0),    # +$25,000
            OptionPosition("SPY_P", "SPY", 100.0, -0.50, 0.10, 500.0),   # -$25,000
        ]

        report = self.engine.evaluate_and_hedge(positions, hedge_instrument_symbol="SPY", hedge_instrument_price=500.0)

        self.assertFalse(report.is_hedging_required)
        self.assertEqual(report.net_greeks.net_delta_usd, 0.0)
        self.assertEqual(len(report.recommended_hedge_orders), 0)


if __name__ == "__main__":
    unittest.main()
