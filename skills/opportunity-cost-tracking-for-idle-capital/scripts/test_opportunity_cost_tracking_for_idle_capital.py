import unittest
from opportunity_cost_tracking_for_idle_capital import (
    OpportunityCostTrackerEngine, PortfolioCapitalState, SweepConfig, OpportunityCostReport
)

class TestOpportunityCostTrackerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = OpportunityCostTrackerEngine()

    def test_opportunity_cost_drag_and_sweep_recommendation(self):
        # $10M total capital, $2M unallocated cash (20% idle ratio > 5% max threshold)
        # SOFR = 5.25%, holding period = 30 days
        # Gross drag = $2,000,000 * 0.0525 * (30/365) = $8,630.14
        state = PortfolioCapitalState(
            total_capital=10000000.0,
            allocated_capital=8000000.0,
            unallocated_cash=2000000.0,
            benchmark_rate_pct=5.25,
            holding_period_days=30.0
        )

        report = self.engine.analyze_opportunity_cost(state)

        self.assertEqual(report.status, "IDLE_CAPITAL_RATIO_EXCEEDED")
        self.assertEqual(report.idle_capital_ratio_pct, 20.0)
        self.assertAlmostEqual(report.gross_opportunity_cost_usd, 8630.14, delta=1.0)
        self.assertEqual(report.recommendation, "SWEEP_TO_YIELD_BENCHMARK")
        self.assertGreater(report.net_yield_gain_usd, 8500.0)

    def test_small_cash_maintain_idle_recommendation(self):
        # $10k cash below $100k min sweep threshold -> MAINTAIN_IDLE_CASH
        state = PortfolioCapitalState(
            total_capital=1000000.0,
            allocated_capital=990000.0,
            unallocated_cash=10000.0,
            benchmark_rate_pct=5.25,
            holding_period_days=30.0
        )

        report = self.engine.analyze_opportunity_cost(state)

        self.assertEqual(report.status, "IDLE_RATIO_HEALTHY")
        self.assertEqual(report.idle_capital_ratio_pct, 1.0)
        self.assertEqual(report.recommendation, "MAINTAIN_IDLE_CASH")

if __name__ == '__main__':
    unittest.main()
