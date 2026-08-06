import unittest
from tick_size_pilot_program_impact_assessment import (
    TickRegime,
    AlgoStrategyType,
    TickSnapshot,
    TickMetrics,
    RegimeComparisonResult,
    TickSizeImpactEngine,
    MicrostructureError,
)


class TestTickSizeImpactEngine(unittest.TestCase):
    def setUp(self):
        self.engine = TickSizeImpactEngine()

    def test_spread_calculations(self):
        # Bid: 10.00, Ask: 10.05 -> Mid = 10.025
        q_spread = TickSizeImpactEngine.calculate_quoted_spread(10.00, 10.05)
        self.assertAlmostEqual(q_spread, 0.05)

        # Trade price = 10.05 (Buy) -> Effective spread = 2 * 1 * (10.05 - 10.025) = 0.05
        eff_spread = TickSizeImpactEngine.calculate_effective_spread(10.05, 10.025, is_buy=True)
        self.assertAlmostEqual(eff_spread, 0.05)

        # Future mid = 10.04 -> Realized spread = 2 * 1 * (10.05 - 10.04) = 0.02
        real_spread = TickSizeImpactEngine.calculate_realized_spread(10.05, 10.04, is_buy=True)
        self.assertAlmostEqual(real_spread, 0.02)

    def test_microstructure_metrics_evaluation(self):
        snapshots = [
            TickSnapshot(1000, "XYZ", 10.00, 10.05, 1000, 1000, 10.05, 100, True, 10.04),
            TickSnapshot(2000, "XYZ", 10.00, 10.05, 1200, 800, 10.00, 200, False, 10.01),
        ]
        metrics = self.engine.evaluate_microstructure_metrics(
            "XYZ", TickRegime.WIDENED_FIVE_CENT, snapshots, total_messages=100, total_fills=10
        )
        self.assertEqual(metrics.symbol, "XYZ")
        self.assertEqual(metrics.regime, TickRegime.WIDENED_FIVE_CENT)
        self.assertEqual(metrics.sample_count, 2)
        self.assertAlmostEqual(metrics.avg_quoted_spread, 0.05)
        self.assertEqual(metrics.avg_top_depth_shares, 2000.0)
        self.assertEqual(metrics.avg_order_to_trade_ratio, 10.0)
        self.assertEqual(metrics.realized_fill_rate_pct, 10.0)

    def test_regime_comparison_widened_spread(self):
        baseline = TickMetrics(
            symbol="ABC",
            regime=TickRegime.STANDARD_CENT,
            sample_count=1000,
            avg_quoted_spread=0.01,
            avg_effective_spread=0.01,
            avg_realized_spread_5m=0.005,
            avg_top_depth_shares=500.0,
            avg_order_to_trade_ratio=5.0,
            realized_fill_rate_pct=20.0,
            adverse_selection_bps=2.0,
        )
        test = TickMetrics(
            symbol="ABC",
            regime=TickRegime.WIDENED_FIVE_CENT,
            sample_count=1000,
            avg_quoted_spread=0.05,
            avg_effective_spread=0.05,
            avg_realized_spread_5m=0.025,
            avg_top_depth_shares=2500.0,
            avg_order_to_trade_ratio=15.0,
            realized_fill_rate_pct=10.0,
            adverse_selection_bps=5.0,
        )

        comp = self.engine.compare_regimes(baseline, test)
        self.assertAlmostEqual(comp.quoted_spread_change_pct, 400.0)  # 0.01 to 0.05 = +400%
        self.assertAlmostEqual(comp.top_depth_change_pct, 400.0)       # 500 to 2500 = +400%
        self.assertTrue(any("widened" in f.lower() for f in comp.key_findings))

    def test_strategy_tuning_recommendations(self):
        comp = RegimeComparisonResult(
            symbol="ABC",
            baseline_regime=TickRegime.STANDARD_CENT,
            test_regime=TickRegime.WIDENED_FIVE_CENT,
            quoted_spread_change_pct=400.0,
            effective_spread_change_pct=400.0,
            top_depth_change_pct=400.0,
            fill_rate_change_pct=-10.0,
            adverse_selection_change_bps=3.0,
        )
        recs_mm = self.engine.recommend_strategy_tuning(AlgoStrategyType.PASSIVE_MARKET_MAKING, comp)
        self.assertTrue(len(recs_mm) >= 2)
        self.assertTrue(any("queue priority" in r.lower() for r in recs_mm))

        recs_twap = self.engine.recommend_strategy_tuning(AlgoStrategyType.TWAP_VWAP_SLICING, comp)
        self.assertTrue(len(recs_twap) >= 1)
        self.assertTrue(any("passive limit orders" in r.lower() for r in recs_twap))


if __name__ == "__main__":
    unittest.main()

