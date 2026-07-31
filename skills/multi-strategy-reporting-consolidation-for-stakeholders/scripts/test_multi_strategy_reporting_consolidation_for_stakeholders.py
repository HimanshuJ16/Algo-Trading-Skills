import unittest
from multi_strategy_reporting_consolidation_for_stakeholders import (
    MultiStrategyReportingConsolidatorEngine, StrategyTelemetry, ReportingConfig, ConsolidatedStakeholderReport
)

class TestMultiStrategyReportingConsolidatorEngine(unittest.TestCase):

    def setUp(self):
        self.engine = MultiStrategyReportingConsolidatorEngine()

    def test_multi_strategy_consolidation_and_diversification(self):
        # 2 un-correlated strategies ($500k each = $1.0M portfolio)
        # Strategy A returns: +1%, -0.5%, +1%, -0.5%, +1%
        # Strategy B returns: -0.5%, +1%, -0.5%, +1%, -0.5% (opposite movement!)
        rets_a = [0.01, -0.005, 0.01, -0.005, 0.01] * 10
        rets_b = [-0.005, 0.01, -0.005, 0.01, -0.005] * 10

        strat_a = StrategyTelemetry(
            strategy_id="STAT_ARB_01",
            strategy_name="Statistical Arbitrage",
            allocated_capital_usd=500000.0,
            realized_pnl_usd=25000.0,
            unrealized_pnl_usd=5000.0,
            daily_returns=rets_a,
            max_drawdown_pct=2.5
        )

        strat_b = StrategyTelemetry(
            strategy_id="TREND_01",
            strategy_name="CTA Trend Following",
            allocated_capital_usd=500000.0,
            realized_pnl_usd=15000.0,
            unrealized_pnl_usd=5000.0,
            daily_returns=rets_b,
            max_drawdown_pct=3.0
        )

        report = self.engine.consolidate_reports([strat_a, strat_b])

        self.assertEqual(report.status, "REPORT_CONSOLIDATED_SUCCESS")
        self.assertEqual(report.total_allocated_capital_usd, 1000000.0)
        self.assertEqual(report.total_net_pnl_usd, 50000.0)
        self.assertEqual(report.portfolio_return_pct, 5.0)

        # Portfolio volatility should be significantly lower than weighted sum volatility (Diversification Benefit!)
        self.assertGreater(report.diversification_ratio, 1.1)
        self.assertLess(report.portfolio_annualized_volatility_pct, report.weighted_sum_volatility_pct)

    def test_single_strategy_consolidation(self):
        # 1 strategy -> Diversification ratio = 1.0
        rets = [0.002, 0.001, -0.001, 0.003] * 5
        strat = StrategyTelemetry("SINGLE_01", "Single Strat", 100000.0, 2000.0, 0.0, rets, 1.0)

        report = self.engine.consolidate_reports([strat])

        self.assertEqual(report.total_allocated_capital_usd, 100000.0)
        self.assertEqual(report.diversification_ratio, 1.0)

if __name__ == '__main__':
    unittest.main()
