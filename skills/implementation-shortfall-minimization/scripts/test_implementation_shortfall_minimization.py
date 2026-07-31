import unittest
from implementation_shortfall_minimization import (
    ImplementationShortfallEngine, ExecutedTradeFill, ImplementationShortfallReport
)

class TestImplementationShortfallEngine(unittest.TestCase):

    def setUp(self):
        self.engine = ImplementationShortfallEngine()

    def test_almgren_chriss_trajectory_generation(self):
        # Linear TWAP schedule when lambda = 0
        twap_trajectory = self.engine.calculate_almgren_chriss_trajectory(10_000, n_intervals=5, risk_aversion_lambda=0.0)
        self.assertEqual(sum(twap_trajectory), 10_000)
        self.assertEqual(twap_trajectory, [2000, 2000, 2000, 2000, 2000])

        # Aggressive front-loaded schedule when lambda > 0
        is_trajectory = self.engine.calculate_almgren_chriss_trajectory(10_000, n_intervals=5, risk_aversion_lambda=0.01)
        self.assertEqual(sum(is_trajectory), 10_000)
        self.assertGreater(is_trajectory[0], is_trajectory[-1]) # Front-loaded!

    def test_perold_is_cost_decomposition(self):
        # Buy 10,000 shares @ Decision P0 = $100.00
        # Fills: 8,000 shares @ avg $100.25 (Impact = 8000 * 0.25 = $2,000)
        # Unfilled: 2,000 shares @ Final P = $101.00 (Opp Cost = 2000 * 1.00 = $2,000)
        # Fees = $20.00
        # Total IS = $4,020.00 (40.20 bps on $1M benchmark)
        fills = [
            ExecutedTradeFill("F1", 4000, 100.20, 10.0, 1000),
            ExecutedTradeFill("F2", 4000, 100.30, 10.0, 1001)
        ]
        report = self.engine.evaluate_implementation_shortfall(
            symbol="AAPL", side="BUY", total_order_qty=10_000, decision_price_p0=100.00,
            executed_fills=fills, final_market_price=101.00
        )

        self.assertEqual(report.status, "IS_EVALUATION_SUCCESS")
        self.assertEqual(report.market_impact_cost_usd, 2000.0)
        self.assertEqual(report.opportunity_cost_usd, 2000.0)
        self.assertEqual(report.explicit_fees_usd, 20.0)
        self.assertEqual(report.total_implementation_shortfall_usd, 4020.0)
        self.assertEqual(report.total_implementation_shortfall_bps, 40.20)

if __name__ == '__main__':
    unittest.main()
