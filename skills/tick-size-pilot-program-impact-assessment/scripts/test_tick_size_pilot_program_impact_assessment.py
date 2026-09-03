"""Unit tests for the tick size regime impact engine.

Expected values are derived independently of the implementation:

* The effective-spread cases reproduce the worked example published in the Tick Size
  Pilot Assessment (NMS Plan Participants / Rosenblatt Securities, 2018-07-03,
  footnote 11): selling at 10.00 into a 10.00 x 10.05 quote gives an effective spread
  of 0.05, and selling at 10.01 into the same quote gives 0.03.
* Share-weighted averages are computed by hand from the sample sizes, matching the
  Rule 605 definition of a share-weighted average (17 CFR 242.600(b)(8), (13)).
* Fill-rate cases use the Pilot Assessment's Fig 28 convention -- executed shares over
  ordered shares (Test Group 3 moved 1.1% -> 2.2%).
"""
import logging
import unittest

from tick_size_pilot_program_impact_assessment import (
    AlgoStrategyType,
    InvalidSnapshotPolicy,
    MicrostructureError,
    RegimeComparisonResult,
    SpreadWeighting,
    TickMetrics,
    TickRegime,
    TickSizeImpactEngine,
    TickSnapshot,
)

logging.disable(logging.CRITICAL)


def make_metrics(**overrides) -> TickMetrics:
    """A complete, valid TickMetrics with named overrides."""
    defaults = dict(
        symbol="ABC",
        regime=TickRegime.STANDARD_CENT,
        sample_count=1000,
        avg_quoted_spread=0.01,
        avg_effective_spread=0.01,
        avg_realized_spread_5m=0.005,
        avg_top_depth_shares=500.0,
        avg_order_to_trade_ratio=5.0,
        share_fill_rate_pct=1.1,
        adverse_selection_bps=2.0,
        avg_midpoint=10.0,
        trade_sample_count=100,
        realized_sample_count=100,
        weighting=SpreadWeighting.SHARE_WEIGHTED,
    )
    defaults.update(overrides)
    return TickMetrics(**defaults)


class TestSpreadFormulas(unittest.TestCase):
    """Published worked examples, not a restatement of the implementation."""

    def test_quoted_spread_matches_nbbo_width(self):
        self.assertAlmostEqual(
            TickSizeImpactEngine.calculate_quoted_spread(10.00, 10.05), 0.05)

    def test_locked_market_has_zero_quoted_spread(self):
        # A locked NBBO is transient but real; its spread genuinely is zero.
        self.assertEqual(TickSizeImpactEngine.calculate_quoted_spread(10.00, 10.00), 0.0)

    def test_crossed_market_raises(self):
        with self.assertRaises(MicrostructureError):
            TickSizeImpactEngine.calculate_quoted_spread(10.05, 10.00)

    def test_non_finite_and_non_positive_prices_raise(self):
        for bid, ask in ((float("nan"), 10.05), (10.00, float("inf")), (0.0, 10.05),
                         (-1.0, 10.05)):
            with self.subTest(bid=bid, ask=ask):
                with self.assertRaises(MicrostructureError):
                    TickSizeImpactEngine.calculate_quoted_spread(bid, ask)

    def test_effective_spread_published_sell_examples(self):
        # Assessment fn.11: sell at 10.00 into 10.00 x 10.05 -> 0.05.
        self.assertAlmostEqual(
            TickSizeImpactEngine.calculate_effective_spread(10.00, 10.025, is_buy=False),
            0.05)
        # Same quote, sell at 10.01 -> 0.03.
        self.assertAlmostEqual(
            TickSizeImpactEngine.calculate_effective_spread(10.01, 10.025, is_buy=False),
            0.03)

    def test_midpoint_print_has_zero_effective_spread(self):
        # "An effective spread of zero indicates a midpoint transaction" (fn.11).
        self.assertAlmostEqual(
            TickSizeImpactEngine.calculate_effective_spread(10.025, 10.025, is_buy=True),
            0.0)

    def test_effective_spread_can_be_negative_inside_the_midpoint(self):
        # Buying below the midpoint is price improvement past the mid, not an error.
        self.assertAlmostEqual(
            TickSizeImpactEngine.calculate_effective_spread(10.015, 10.025, is_buy=True),
            -0.02)

    def test_realized_spread_signs_by_side(self):
        # Buy at 10.05, midpoint 5 minutes later 10.04 -> 2 * (10.05 - 10.04) = 0.02.
        self.assertAlmostEqual(
            TickSizeImpactEngine.calculate_realized_spread(10.05, 10.04, is_buy=True),
            0.02)
        # Sell at 10.00, midpoint 5 minutes later 10.01 -> 2 * (10.01 - 10.00) = 0.02.
        self.assertAlmostEqual(
            TickSizeImpactEngine.calculate_realized_spread(10.00, 10.01, is_buy=False),
            0.02)
        # A maker run over: buy at 10.035, midpoint moves up to 10.045 -> -0.02.
        self.assertAlmostEqual(
            TickSizeImpactEngine.calculate_realized_spread(10.035, 10.045, is_buy=True),
            -0.02)


class TestMetricAggregation(unittest.TestCase):
    def setUp(self):
        self.engine = TickSizeImpactEngine()

    def test_effective_spread_is_share_weighted(self):
        # Quote 10.00 x 10.05 (mid 10.025) on both snapshots.
        #   100 shares at 10.05  -> effective 0.05
        #   900 shares at 10.035 -> effective 0.02
        # Share-weighted: (0.05*100 + 0.02*900) / 1000 = 0.023
        # Equal-weighted would be 0.035 -- the value the pre-fix engine returned.
        snapshots = [
            TickSnapshot(1_000, "XYZ", 10.00, 10.05, 1000, 1000, 10.050, 100, True),
            TickSnapshot(2_000, "XYZ", 10.00, 10.05, 1000, 1000, 10.035, 900, True),
        ]
        metrics = self.engine.evaluate_microstructure_metrics(
            "XYZ", TickRegime.WIDENED_FIVE_CENT, snapshots)
        self.assertIs(metrics.weighting, SpreadWeighting.SHARE_WEIGHTED)
        self.assertAlmostEqual(metrics.avg_effective_spread, 0.023, places=9)
        self.assertNotAlmostEqual(metrics.avg_effective_spread, 0.035, places=4)

    def test_missing_trade_size_falls_back_to_equal_weighting(self):
        snapshots = [
            TickSnapshot(1_000, "XYZ", 10.00, 10.05, 1000, 1000, 10.050, 100, True),
            TickSnapshot(2_000, "XYZ", 10.00, 10.05, 1000, 1000, 10.035, None, True),
        ]
        metrics = self.engine.evaluate_microstructure_metrics(
            "XYZ", TickRegime.WIDENED_FIVE_CENT, snapshots)
        self.assertIs(metrics.weighting, SpreadWeighting.EQUAL_WEIGHTED)
        self.assertAlmostEqual(metrics.avg_effective_spread, (0.05 + 0.02) / 2.0, places=9)

    def test_adverse_selection_from_hand_computed_weighted_averages(self):
        #   100 shares at 10.050, future mid 10.040 -> effective  0.05, realized  0.02
        #   300 shares at 10.035, future mid 10.045 -> effective  0.02, realized -0.02
        # Weighted effective = (0.05*100 + 0.02*300)/400 =  0.0275
        # Weighted realized  = (0.02*100 - 0.02*300)/400 = -0.01
        # Average midpoint   = 10.025
        # Adverse selection  = (0.0275 + 0.01) / 10.025 * 10000 = 37.406484 bps
        snapshots = [
            TickSnapshot(1_000, "XYZ", 10.00, 10.05, 500, 500, 10.050, 100, True, 10.040),
            TickSnapshot(2_000, "XYZ", 10.00, 10.05, 500, 500, 10.035, 300, True, 10.045),
        ]
        metrics = self.engine.evaluate_microstructure_metrics(
            "XYZ", TickRegime.WIDENED_FIVE_CENT, snapshots)
        self.assertAlmostEqual(metrics.avg_effective_spread, 0.0275, places=9)
        self.assertAlmostEqual(metrics.avg_realized_spread_5m, -0.01, places=9)
        self.assertAlmostEqual(metrics.avg_midpoint, 10.025, places=9)
        self.assertAlmostEqual(metrics.adverse_selection_bps, 37.4064838, places=5)

    def test_quote_only_sample_reports_none_not_the_quoted_spread(self):
        # Regression: the pre-fix engine substituted the quoted spread for a missing
        # effective spread, and half the effective spread for a missing realized
        # spread, fabricating both metrics from data that was never observed.
        snapshots = [TickSnapshot(1_000, "XYZ", 10.00, 10.05, 400, 600)]
        metrics = self.engine.evaluate_microstructure_metrics(
            "XYZ", TickRegime.STANDARD_CENT, snapshots)
        self.assertAlmostEqual(metrics.avg_quoted_spread, 0.05)
        self.assertIsNone(metrics.avg_effective_spread)
        self.assertIsNone(metrics.avg_realized_spread_5m)
        self.assertIsNone(metrics.adverse_selection_bps)
        self.assertEqual(metrics.trade_sample_count, 0)

    def test_trades_without_future_midpoints_leave_realized_unmeasured(self):
        snapshots = [
            TickSnapshot(1_000, "XYZ", 10.00, 10.05, 400, 600, 10.05, 100, True, None),
        ]
        metrics = self.engine.evaluate_microstructure_metrics(
            "XYZ", TickRegime.STANDARD_CENT, snapshots)
        self.assertAlmostEqual(metrics.avg_effective_spread, 0.05, places=9)
        self.assertIsNone(metrics.avg_realized_spread_5m)
        self.assertIsNone(metrics.adverse_selection_bps)
        self.assertEqual(metrics.realized_sample_count, 0)

    def test_depth_and_midpoint_averages(self):
        snapshots = [
            TickSnapshot(1_000, "XYZ", 10.00, 10.05, 1000, 1000),
            TickSnapshot(2_000, "XYZ", 10.00, 10.05, 1200, 800),
        ]
        metrics = self.engine.evaluate_microstructure_metrics(
            "XYZ", TickRegime.WIDENED_FIVE_CENT, snapshots)
        self.assertEqual(metrics.sample_count, 2)
        self.assertAlmostEqual(metrics.avg_top_depth_shares, 2000.0)
        self.assertAlmostEqual(metrics.avg_midpoint, 10.025, places=9)

    def test_order_to_trade_ratio_and_share_fill_rate(self):
        snapshots = [TickSnapshot(1_000, "XYZ", 10.00, 10.05, 1000, 1000)]
        metrics = self.engine.evaluate_microstructure_metrics(
            "XYZ", TickRegime.WIDENED_FIVE_CENT, snapshots,
            total_messages=100, total_fills=10,
            total_shares_ordered=1_000_000, total_shares_executed=22_000)
        self.assertAlmostEqual(metrics.avg_order_to_trade_ratio, 10.0)
        # Fill rate is executed shares over ordered shares (Assessment Fig 28), not
        # fills over messages -- which was only the reciprocal of the OTR.
        self.assertAlmostEqual(metrics.share_fill_rate_pct, 2.2, places=9)

    def test_missing_counters_report_none_not_zero(self):
        snapshots = [TickSnapshot(1_000, "XYZ", 10.00, 10.05, 1000, 1000)]
        metrics = self.engine.evaluate_microstructure_metrics(
            "XYZ", TickRegime.WIDENED_FIVE_CENT, snapshots)
        self.assertIsNone(metrics.avg_order_to_trade_ratio)
        self.assertIsNone(metrics.share_fill_rate_pct)

    def test_crossed_quote_is_skipped_not_fatal(self):
        # Regression: one crossed print used to abort the whole batch.
        snapshots = [
            TickSnapshot(1_000, "XYZ", 10.00, 10.05, 1000, 1000),
            TickSnapshot(2_000, "XYZ", 10.06, 10.01, 1000, 1000),  # crossed
            TickSnapshot(3_000, "XYZ", 10.00, 10.05, 1000, 1000),
        ]
        metrics = self.engine.evaluate_microstructure_metrics(
            "XYZ", TickRegime.STANDARD_CENT, snapshots)
        self.assertEqual(metrics.sample_count, 2)
        self.assertEqual(metrics.excluded_snapshot_count, 1)

    def test_raise_policy_propagates_the_bad_snapshot(self):
        snapshots = [
            TickSnapshot(1_000, "XYZ", 10.00, 10.05, 1000, 1000),
            TickSnapshot(2_000, "XYZ", 10.06, 10.01, 1000, 1000),
        ]
        with self.assertRaises(MicrostructureError):
            self.engine.evaluate_microstructure_metrics(
                "XYZ", TickRegime.STANDARD_CENT, snapshots,
                invalid_snapshot_policy=InvalidSnapshotPolicy.RAISE)

    def test_empty_and_fully_excluded_samples_raise(self):
        with self.assertRaises(MicrostructureError):
            self.engine.evaluate_microstructure_metrics(
                "XYZ", TickRegime.STANDARD_CENT, [])
        with self.assertRaises(MicrostructureError):
            self.engine.evaluate_microstructure_metrics(
                "XYZ", TickRegime.STANDARD_CENT,
                [TickSnapshot(1_000, "XYZ", 10.06, 10.01, 1000, 1000)])

    def test_negative_counters_rejected(self):
        snapshots = [TickSnapshot(1_000, "XYZ", 10.00, 10.05, 1000, 1000)]
        with self.assertRaises(MicrostructureError):
            self.engine.evaluate_microstructure_metrics(
                "XYZ", TickRegime.STANDARD_CENT, snapshots, total_messages=-1)
        with self.assertRaises(MicrostructureError):
            self.engine.evaluate_microstructure_metrics(
                "XYZ", TickRegime.STANDARD_CENT, snapshots, total_shares_ordered=-5)

    def test_nan_size_does_not_reach_the_average(self):
        snapshots = [
            TickSnapshot(1_000, "XYZ", 10.00, 10.05, float("nan"), 1000),
            TickSnapshot(2_000, "XYZ", 10.00, 10.05, 1000, 1000),
        ]
        metrics = self.engine.evaluate_microstructure_metrics(
            "XYZ", TickRegime.STANDARD_CENT, snapshots)
        self.assertEqual(metrics.excluded_snapshot_count, 1)
        self.assertAlmostEqual(metrics.avg_top_depth_shares, 2000.0)


class TestRegimeComparison(unittest.TestCase):
    def setUp(self):
        self.engine = TickSizeImpactEngine()

    def test_percentage_changes(self):
        baseline = make_metrics(
            avg_quoted_spread=0.01, avg_top_depth_shares=500.0, share_fill_rate_pct=1.1)
        test = make_metrics(
            regime=TickRegime.WIDENED_FIVE_CENT, avg_quoted_spread=0.05,
            avg_effective_spread=0.05, avg_realized_spread_5m=0.025,
            avg_top_depth_shares=2500.0, share_fill_rate_pct=2.2,
            adverse_selection_bps=5.0)
        comparison = self.engine.compare_regimes(baseline, test)
        self.assertAlmostEqual(comparison.quoted_spread_change_pct, 400.0)
        self.assertAlmostEqual(comparison.top_depth_change_pct, 400.0)
        self.assertAlmostEqual(comparison.fill_rate_change_pp, 1.1, places=9)
        self.assertAlmostEqual(comparison.adverse_selection_change_bps, 3.0)
        self.assertEqual(comparison.undefined_metrics, [])
        self.assertTrue(any("widened" in f.lower() for f in comparison.key_findings))

    def test_zero_baseline_effective_spread_is_undefined_not_a_crash(self):
        # Regression: an all-midpoint baseline (effective spread 0.0) raised
        # ZeroDivisionError and took the whole comparison down with it.
        baseline = make_metrics(avg_effective_spread=0.0)
        test = make_metrics(regime=TickRegime.WIDENED_FIVE_CENT, avg_effective_spread=0.05)
        comparison = self.engine.compare_regimes(baseline, test)
        self.assertIsNone(comparison.effective_spread_change_pct)
        self.assertIn("effective_spread_change_pct", comparison.undefined_metrics)

    def test_negative_baseline_effective_spread_does_not_invert_the_sign(self):
        # Regression: baseline -0.01 -> test 0.05 used to report -600%, i.e. a
        # compression, while the cost of crossing had actually risen.
        baseline = make_metrics(avg_effective_spread=-0.01)
        test = make_metrics(regime=TickRegime.WIDENED_FIVE_CENT, avg_effective_spread=0.05)
        comparison = self.engine.compare_regimes(baseline, test)
        self.assertIsNone(comparison.effective_spread_change_pct)

    def test_unmeasured_metrics_are_named_not_defaulted(self):
        baseline = make_metrics(adverse_selection_bps=None, share_fill_rate_pct=None)
        test = make_metrics(regime=TickRegime.WIDENED_FIVE_CENT,
                            adverse_selection_bps=None, share_fill_rate_pct=None)
        comparison = self.engine.compare_regimes(baseline, test)
        self.assertIsNone(comparison.adverse_selection_change_bps)
        self.assertIsNone(comparison.fill_rate_change_pp)
        self.assertIn("adverse_selection_change_bps", comparison.undefined_metrics)
        self.assertTrue(any("Not measured" in f for f in comparison.key_findings))

    def test_symbol_mismatch_rejected(self):
        baseline = make_metrics(symbol="ABC")
        test = make_metrics(symbol="XYZ", regime=TickRegime.WIDENED_FIVE_CENT)
        with self.assertRaises(MicrostructureError):
            self.engine.compare_regimes(baseline, test)

    def test_quoted_and_effective_findings_are_reported_separately(self):
        # The Pilot widened quoted spreads far more than the cost actually paid;
        # the two must not be collapsed into one narrative.
        baseline = make_metrics(avg_quoted_spread=0.01, avg_effective_spread=0.0296)
        test = make_metrics(regime=TickRegime.WIDENED_FIVE_CENT,
                            avg_quoted_spread=0.05, avg_effective_spread=0.0300)
        comparison = self.engine.compare_regimes(baseline, test)
        self.assertAlmostEqual(comparison.quoted_spread_change_pct, 400.0)
        self.assertLess(comparison.effective_spread_change_pct, 2.0)
        self.assertFalse(any("Effective spread widened" in f for f in comparison.key_findings))


class TestStrategyTuning(unittest.TestCase):
    def setUp(self):
        self.engine = TickSizeImpactEngine()

    @staticmethod
    def comparison(**overrides) -> RegimeComparisonResult:
        defaults = dict(
            symbol="ABC",
            baseline_regime=TickRegime.STANDARD_CENT,
            test_regime=TickRegime.WIDENED_FIVE_CENT,
            quoted_spread_change_pct=400.0,
            effective_spread_change_pct=54.0,
            top_depth_change_pct=333.0,
            fill_rate_change_pp=1.1,
            adverse_selection_change_bps=3.0,
        )
        defaults.update(overrides)
        return RegimeComparisonResult(**defaults)

    def test_market_making_flags_queue_and_adverse_selection(self):
        recs = self.engine.recommend_strategy_tuning(
            AlgoStrategyType.PASSIVE_MARKET_MAKING, self.comparison())
        self.assertGreaterEqual(len(recs), 3)
        self.assertTrue(any("time priority" in r.lower() for r in recs))
        self.assertTrue(any("adverse selection" in r.lower() for r in recs))

    def test_slicing_gates_on_effective_not_quoted_spread(self):
        # Regression: a +400% quoted spread with a nearly flat effective spread used to
        # trigger a full passive re-weighting on the strength of the quote alone.
        recs = self.engine.recommend_strategy_tuning(
            AlgoStrategyType.TWAP_VWAP_SLICING,
            self.comparison(quoted_spread_change_pct=400.0,
                            effective_spread_change_pct=1.4))
        self.assertEqual(recs, [])

    def test_slicing_reacts_when_the_effective_cost_actually_rises(self):
        recs = self.engine.recommend_strategy_tuning(
            AlgoStrategyType.TWAP_VWAP_SLICING, self.comparison())
        self.assertTrue(any("passive" in r.lower() for r in recs))
        self.assertTrue(any("price cap" in r.lower() for r in recs))

    def test_stat_arb_is_handled_rather_than_silently_empty(self):
        recs = self.engine.recommend_strategy_tuning(
            AlgoStrategyType.STAT_ARB, self.comparison())
        self.assertTrue(recs)
        self.assertTrue(any("leg" in r.lower() for r in recs))

    def test_undefined_metrics_produce_an_explicit_cannot_assess(self):
        recs = self.engine.recommend_strategy_tuning(
            AlgoStrategyType.PASSIVE_MARKET_MAKING,
            self.comparison(quoted_spread_change_pct=None,
                            top_depth_change_pct=None,
                            adverse_selection_change_bps=None))
        self.assertEqual(len(recs), 2)
        self.assertTrue(all("cannot assess" in r.lower() for r in recs))

    def test_momentum_taker_quiet_when_costs_fall(self):
        recs = self.engine.recommend_strategy_tuning(
            AlgoStrategyType.MOMENTUM_TAKER,
            self.comparison(effective_spread_change_pct=-30.0))
        self.assertEqual(recs, [])

    def test_invalid_algo_type_rejected(self):
        with self.assertRaises(MicrostructureError):
            self.engine.recommend_strategy_tuning("PASSIVE_MARKET_MAKING", self.comparison())


if __name__ == "__main__":
    unittest.main()
