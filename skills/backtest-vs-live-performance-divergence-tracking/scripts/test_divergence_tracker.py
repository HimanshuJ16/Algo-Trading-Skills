"""
Unit tests for backtest-vs-live-performance-divergence-tracking skill.
"""
import math
import unittest

from divergence_tracker import (
    BacktestLiveDivergenceTracker,
    DivergenceSeverity,
    DivergenceTrackerError,
    PerformanceSnapshot,
)


def snap(sharpe=2.0, dd=10.0, wr=55.0, fill=98.0, slip=3.0, periods=None):
    """Healthy baseline; override only the dimension under test."""
    return PerformanceSnapshot(
        sharpe_ratio=sharpe, max_drawdown_pct=dd, win_rate_pct=wr,
        fill_rate_pct=fill, avg_slippage_bps=slip, observation_periods=periods,
    )


class TestBacktestLiveDivergenceTracker(unittest.TestCase):

    def setUp(self):
        self.tracker = BacktestLiveDivergenceTracker()

    def _metric(self, report, name):
        return next(m for m in report.metrics if m.name == name)

    # ----------------------------------------------------------------- baseline

    def test_acceptable_divergence(self):
        bt = PerformanceSnapshot(sharpe_ratio=2.0, max_drawdown_pct=10.0, win_rate_pct=55.0, fill_rate_pct=98.0, avg_slippage_bps=3.0)
        live = PerformanceSnapshot(sharpe_ratio=1.85, max_drawdown_pct=11.0, win_rate_pct=53.0, fill_rate_pct=96.0, avg_slippage_bps=4.0)

        report = self.tracker.evaluate_divergence("momentum_v1", bt, live)

        self.assertEqual(report.overall_severity, DivergenceSeverity.ACCEPTABLE)
        self.assertFalse(report.is_suspension_recommended)
        self.assertEqual(report.driving_metrics, [])

    def test_critical_sharpe_decay_triggers_suspension(self):
        bt = PerformanceSnapshot(sharpe_ratio=2.5, max_drawdown_pct=8.0, win_rate_pct=60.0, fill_rate_pct=99.0, avg_slippage_bps=2.0)
        live = PerformanceSnapshot(sharpe_ratio=0.8, max_drawdown_pct=20.0, win_rate_pct=45.0, fill_rate_pct=80.0, avg_slippage_bps=10.0)

        report = self.tracker.evaluate_divergence("broken_strat", bt, live)

        self.assertEqual(report.overall_severity, DivergenceSeverity.CRITICAL)
        self.assertTrue(report.is_suspension_recommended)
        self.assertIn("CRITICAL DIVERGENCE", report.message)
        self.assertIn("Sharpe Decay", report.driving_metrics)

    def test_warning_on_moderate_divergence(self):
        bt = PerformanceSnapshot(sharpe_ratio=2.0, max_drawdown_pct=10.0, win_rate_pct=55.0, fill_rate_pct=98.0, avg_slippage_bps=3.0)
        live = PerformanceSnapshot(sharpe_ratio=1.5, max_drawdown_pct=12.0, win_rate_pct=52.0, fill_rate_pct=95.0, avg_slippage_bps=5.0)

        report = self.tracker.evaluate_divergence("moderate_drift", bt, live)

        self.assertEqual(report.overall_severity, DivergenceSeverity.WARNING)
        self.assertFalse(report.is_suspension_recommended)

    def test_critical_win_rate_decay(self):
        # Even if Sharpe is barely acceptable, a crash in Win Rate from 80% to 50% (>25% decay) triggers CRITICAL Concept Drift
        bt = PerformanceSnapshot(sharpe_ratio=2.0, max_drawdown_pct=10.0, win_rate_pct=80.0, fill_rate_pct=98.0, avg_slippage_bps=3.0)
        live = PerformanceSnapshot(sharpe_ratio=1.6, max_drawdown_pct=11.0, win_rate_pct=50.0, fill_rate_pct=98.0, avg_slippage_bps=3.0)

        report = self.tracker.evaluate_divergence("concept_drift_strat", bt, live)

        self.assertEqual(report.overall_severity, DivergenceSeverity.CRITICAL)
        self.assertTrue(report.is_suspension_recommended)
        self.assertTrue(any(m.name == "Win Rate Decay" and m.severity == DivergenceSeverity.CRITICAL for m in report.metrics))

    # ------------------------------ silent-ACCEPTABLE paths (regression, critical)

    def test_negative_drawdown_convention_still_detects_blow_up(self):
        """Regression: a -50% live drawdown against -10% backtest reported ACCEPTABLE.

        The guard was `backtest.max_drawdown_pct > 0`, so the negative convention emitted
        by this repo's own tearsheet skill skipped the comparison entirely and defaulted
        the ratio to 1.0. Magnitudes are now compared.
        """
        report = self.tracker.evaluate_divergence("neg_convention", snap(dd=-10.0), snap(sharpe=1.9, dd=-50.0))

        self.assertEqual(report.overall_severity, DivergenceSeverity.CRITICAL)
        self.assertTrue(report.is_suspension_recommended)
        self.assertAlmostEqual(self._metric(report, "Drawdown Blow-Up").comparison_value, 5.0)

    def test_both_drawdown_conventions_give_identical_verdicts(self):
        positive = self.tracker.evaluate_divergence("p", snap(dd=10.0), snap(sharpe=1.9, dd=50.0))
        negative = self.tracker.evaluate_divergence("n", snap(dd=-10.0), snap(sharpe=1.9, dd=-50.0))
        self.assertEqual(positive.overall_severity, negative.overall_severity)
        self.assertEqual(
            self._metric(positive, "Drawdown Blow-Up").comparison_value,
            self._metric(negative, "Drawdown Blow-Up").comparison_value,
        )

    def test_mixed_drawdown_sign_conventions_are_rejected(self):
        with self.assertRaises(DivergenceTrackerError):
            self.tracker.evaluate_divergence("mixed", snap(dd=-10.0), snap(dd=50.0))

    def test_zero_slippage_baseline_is_never_acceptable(self):
        """Regression: a backtest assuming zero slippage vs 50 bps live reported ACCEPTABLE.

        No amplification ratio exists against a zero baseline, and the old code defaulted
        it to 1.0 -- silently blessing the single most common backtest omission.
        """
        report = self.tracker.evaluate_divergence("no_slippage_model", snap(slip=0.0), snap(slip=50.0))

        metric = self._metric(report, "Slippage Amplification")
        self.assertIs(metric.severity, DivergenceSeverity.WARNING)
        self.assertIn("unmodelled", metric.notes)
        self.assertNotEqual(report.overall_severity, DivergenceSeverity.ACCEPTABLE)

    def test_zero_slippage_on_both_sides_is_acceptable(self):
        report = self.tracker.evaluate_divergence("flat", snap(slip=0.0), snap(slip=0.0))
        self.assertIs(self._metric(report, "Slippage Amplification").severity, DivergenceSeverity.ACCEPTABLE)

    def test_zero_backtest_drawdown_is_never_acceptable(self):
        report = self.tracker.evaluate_divergence("no_dd_model", snap(dd=0.0), snap(dd=15.0))
        metric = self._metric(report, "Drawdown Blow-Up")
        self.assertIs(metric.severity, DivergenceSeverity.WARNING)
        self.assertNotEqual(metric.notes, "")

    def test_non_positive_backtest_sharpe_is_never_acceptable(self):
        """Regression: bt Sharpe 0.0 vs live -3.0 reported ACCEPTABLE."""
        report = self.tracker.evaluate_divergence("bad_baseline", snap(sharpe=0.0), snap(sharpe=-3.0))
        metric = self._metric(report, "Sharpe Decay")
        self.assertIs(metric.severity, DivergenceSeverity.WARNING)
        self.assertNotEqual(report.overall_severity, DivergenceSeverity.ACCEPTABLE)

    def test_nan_metrics_are_rejected_not_classified(self):
        """Regression: NaN made every metric ACCEPTABLE.

        `max(0.0, nan)` returns 0.0 and `nan >= threshold` is False, so an all-NaN live
        snapshot previously reported no divergence and no suspension.
        """
        nan = float("nan")
        for bad in (snap(sharpe=nan), snap(dd=nan), snap(wr=nan), snap(fill=nan), snap(slip=nan)):
            with self.assertRaises(DivergenceTrackerError):
                self.tracker.evaluate_divergence("nan_live", snap(), bad)
        with self.assertRaises(DivergenceTrackerError):
            self.tracker.evaluate_divergence("inf_live", snap(), snap(sharpe=float("inf")))

    # ---------------------------------------------------- threshold boundary behaviour

    def test_threshold_boundary_is_inclusive_and_self_consistent(self):
        """Sharpe 2.0 -> 1.6 is exactly 20% decay, the warning threshold.

        In binary floating point this computes as 19.999999999999996, so the old code
        classified it ACCEPTABLE while the report displayed `divergence_pct` 20.0 beside
        `threshold_warning` 20.0 -- a self-contradictory audit record.
        """
        report = self.tracker.evaluate_divergence("boundary", snap(sharpe=2.0), snap(sharpe=1.6))
        metric = self._metric(report, "Sharpe Decay")

        self.assertEqual(metric.divergence_pct, 20.0)
        self.assertEqual(metric.comparison_value, 20.0)
        self.assertIs(metric.severity, DivergenceSeverity.WARNING)

    def test_just_below_threshold_stays_acceptable(self):
        # 2.0 -> 1.61 is a 19.5% decay.
        report = self.tracker.evaluate_divergence("under", snap(sharpe=2.0), snap(sharpe=1.61))
        self.assertIs(self._metric(report, "Sharpe Decay").severity, DivergenceSeverity.ACCEPTABLE)

    def test_live_outperformance_is_not_penalised(self):
        report = self.tracker.evaluate_divergence("better", snap(sharpe=1.0, dd=20.0, wr=50.0, fill=90.0, slip=10.0),
                                                  snap(sharpe=3.0, dd=5.0, wr=70.0, fill=99.0, slip=1.0))
        self.assertEqual(report.overall_severity, DivergenceSeverity.ACCEPTABLE)
        self.assertEqual(self._metric(report, "Sharpe Decay").divergence_pct, 0.0)

    # ------------------------------------------------------- units and self-description

    def test_comparison_value_shares_the_threshold_scale(self):
        """Regression: `divergence_pct` was a percent while the thresholds were multipliers.

        A dashboard comparing divergence_pct 80.0 against threshold_warning 1.5 is
        meaningless; comparison_value is the quantity actually classified.
        """
        report = self.tracker.evaluate_divergence("units", snap(dd=10.0), snap(sharpe=1.9, dd=18.0))
        metric = self._metric(report, "Drawdown Blow-Up")

        self.assertEqual(metric.divergence_pct, 80.0)
        self.assertAlmostEqual(metric.comparison_value, 1.8)
        self.assertEqual(metric.comparison_basis, "ratio to backtest")
        self.assertIs(
            metric.severity,
            DivergenceSeverity.WARNING if metric.comparison_value >= metric.threshold_warning else DivergenceSeverity.ACCEPTABLE,
        )

    def test_every_metric_declares_its_comparison_basis(self):
        report = self.tracker.evaluate_divergence("bases", snap(), snap(sharpe=1.9))
        bases = {m.name: m.comparison_basis for m in report.metrics}
        self.assertEqual(bases["Sharpe Decay"], "relative decay %")
        self.assertEqual(bases["Win Rate Decay"], "relative decay %")
        self.assertEqual(bases["Fill Rate Gap"], "percentage-point gap")
        self.assertEqual(bases["Drawdown Blow-Up"], "ratio to backtest")
        self.assertEqual(bases["Slippage Amplification"], "ratio to backtest")

    def test_severity_serialises_as_a_string(self):
        report = self.tracker.evaluate_divergence("ser", snap(), snap())
        self.assertEqual(report.overall_severity.value, "ACCEPTABLE")
        self.assertEqual(f"{report.overall_severity}", str(DivergenceSeverity.ACCEPTABLE))

    def test_fill_rate_gap_is_hand_computed(self):
        report = self.tracker.evaluate_divergence("fill", snap(fill=99.0), snap(sharpe=1.9, fill=83.0))
        metric = self._metric(report, "Fill Rate Gap")
        self.assertAlmostEqual(metric.comparison_value, 16.0)
        self.assertIs(metric.severity, DivergenceSeverity.CRITICAL)

    # ----------------------------------------------------------- observation adequacy

    def test_short_live_sample_is_flagged_without_altering_severity(self):
        tracker = BacktestLiveDivergenceTracker(min_live_observations=60)
        bt = snap(sharpe=2.5, periods=756)
        live = snap(sharpe=0.8, periods=10)

        report = tracker.evaluate_divergence("fresh", bt, live)

        self.assertFalse(report.is_sample_adequate)
        self.assertIn("SHORT LIVE SAMPLE", report.message)
        # A short sample must not silently downgrade a critical verdict.
        self.assertEqual(report.overall_severity, DivergenceSeverity.CRITICAL)
        self.assertTrue(report.is_suspension_recommended)

    def test_adequate_sample_is_not_flagged(self):
        tracker = BacktestLiveDivergenceTracker(min_live_observations=60)
        report = tracker.evaluate_divergence("mature", snap(periods=756), snap(sharpe=1.9, periods=120))
        self.assertTrue(report.is_sample_adequate)
        self.assertNotIn("SHORT LIVE SAMPLE", report.message)

    def test_sample_check_is_disabled_by_default(self):
        report = self.tracker.evaluate_divergence("nogate", snap(periods=756), snap(periods=2))
        self.assertTrue(report.is_sample_adequate)

    # --------------------------------------------------------------- input validation

    def test_inverted_thresholds_are_rejected(self):
        """Regression: warn=50 / crit=20 silently made a 30% decay CRITICAL."""
        with self.assertRaises(DivergenceTrackerError):
            BacktestLiveDivergenceTracker(sharpe_warning_pct=50.0, sharpe_critical_pct=20.0)
        with self.assertRaises(DivergenceTrackerError):
            BacktestLiveDivergenceTracker(drawdown_warning_multiplier=3.0, drawdown_critical_multiplier=2.0)

    def test_invalid_threshold_values_are_rejected(self):
        with self.assertRaises(DivergenceTrackerError):
            BacktestLiveDivergenceTracker(sharpe_warning_pct=-1.0)
        with self.assertRaises(DivergenceTrackerError):
            BacktestLiveDivergenceTracker(fill_rate_critical_gap_pct=float("nan"))
        with self.assertRaises(DivergenceTrackerError):
            BacktestLiveDivergenceTracker(min_live_observations=-5)

    def test_percentages_outside_zero_to_hundred_are_rejected(self):
        """A win rate passed as the fraction 0.55 would read as 0.55%, not 55%."""
        with self.assertRaises(DivergenceTrackerError):
            self.tracker.evaluate_divergence("pct", snap(), snap(wr=150.0))
        with self.assertRaises(DivergenceTrackerError):
            self.tracker.evaluate_divergence("pct", snap(), snap(fill=-1.0))

    def test_blank_strategy_name_is_rejected(self):
        with self.assertRaises(DivergenceTrackerError):
            self.tracker.evaluate_divergence("   ", snap(), snap())

    def test_non_positive_observation_periods_are_rejected(self):
        with self.assertRaises(DivergenceTrackerError):
            self.tracker.evaluate_divergence("obs", snap(), snap(periods=0))

    def test_report_is_self_consistent(self):
        report = self.tracker.evaluate_divergence("consistency", snap(sharpe=2.5), snap(sharpe=0.8, dd=25.0, slip=15.0))
        self.assertEqual(
            report.is_suspension_recommended, report.overall_severity is DivergenceSeverity.CRITICAL
        )
        # Every driving metric must actually sit at the overall severity.
        for name in report.driving_metrics:
            self.assertIs(self._metric(report, name).severity, report.overall_severity)
        # No metric may exceed the overall severity.
        order = {DivergenceSeverity.ACCEPTABLE: 0, DivergenceSeverity.WARNING: 1, DivergenceSeverity.CRITICAL: 2}
        self.assertEqual(
            max(order[m.severity] for m in report.metrics), order[report.overall_severity]
        )
        self.assertTrue(all(not math.isnan(m.comparison_value) for m in report.metrics))


if __name__ == "__main__":
    unittest.main()
