import unittest
from strategy_capacity_estimation_before_scaling_capital import (
    StrategyCapacityEstimationBeforeScalingCapital,
    StrategyCapacityEstimationBeforeScalingCapitalConfig,
    StrategyCapacityEstimatorEngine, StrategyParameters,
    StrategyCapacityReport, AUMCapacityPoint
)

class TestStrategyCapacityLegacy(unittest.TestCase):
    def test_execute_true(self):
        config = StrategyCapacityEstimationBeforeScalingCapitalConfig(enabled=True)
        engine = StrategyCapacityEstimationBeforeScalingCapital(config)
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        config = StrategyCapacityEstimationBeforeScalingCapitalConfig(enabled=False)
        engine = StrategyCapacityEstimationBeforeScalingCapital(config)
        self.assertFalse(engine.execute())

class TestStrategyCapacityEngineAdvanced(unittest.TestCase):

    def setUp(self):
        self.engine = StrategyCapacityEstimatorEngine(impact_gamma=0.5)
        self.params = StrategyParameters(
            strategy_id="STAT_ARB_US_LARGE_CAP",
            annual_gross_return_pct=0.25,     # 25% gross
            annual_volatility_pct=0.15,       # 15% vol -> Frictionless Sharpe = 1.67
            daily_turnover_pct=0.10,          # 10% daily turnover
            avg_daily_volume_usd=50_000_000.0,# $50M ADV
            avg_daily_volatility_pct=0.015,   # 1.5% daily vol
            half_spread_bps=1.0,
            max_participation_rate_pct=5.0,   # Max 5% ADV ($2.5M daily turnover)
            min_acceptable_sharpe=1.0
        )

    def test_capacity_estimation_and_limiting_factor(self):
        # Simulate AUM scaling up to $100M in steps of $1M
        report = self.engine.estimate_capacity(
            params=self.params,
            aum_step_usd=1_000_000.0,
            max_search_aum_usd=100_000_000.0
        )
        self.assertEqual(report.frictionless_sharpe_ratio, 1.67)
        self.assertGreater(report.max_capacity_aum_usd, 0.0)
        self.assertIn(report.limiting_factor, ["MIN_SHARPE_BREACH", "ADV_PARTICIPATION_LIMIT"])

    def test_sharpe_ratio_decay_as_aum_scales(self):
        report = self.engine.estimate_capacity(
            params=self.params,
            aum_step_usd=1_000_000.0,
            max_search_aum_usd=5_000_000.0
        )
        curve = report.capacity_curve
        # As AUM scales from $1M to $5M, Net Sharpe ratio must decay due to market impact
        sharpe_1m = curve[0].net_sharpe_ratio
        sharpe_5m = curve[4].net_sharpe_ratio
        self.assertGreater(sharpe_1m, sharpe_5m)

if __name__ == '__main__':
    unittest.main()
