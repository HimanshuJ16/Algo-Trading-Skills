"""
Unit tests for risk-metric-recalculation-frequency-tuning.

Expected values are derived by hand from the tier intervals, never by calling
the implementation and asserting on whatever it returned.
"""
import logging
import math
import unittest

from risk_frequency_tuner import (
    RiskMetricFrequencyTuner,
    RiskMetricScheduleConfig,
    default_schedule,
)

# The engine logs a warning on every accelerated / stale evaluation. Silence it
# so test output stays readable; the tests assert on the report, not on logs.
logging.getLogger("risk_frequency_tuner").setLevel(logging.CRITICAL)


class TestScheduleConfigValidation(unittest.TestCase):
    def test_rejects_accelerated_interval_slower_than_base(self):
        with self.assertRaises(ValueError):
            RiskMetricScheduleConfig(
                "VAR", tier=3, base_interval_sec=5.0, accelerated_interval_sec=30.0)

    def test_rejects_non_zero_interval_on_per_evaluation_tier(self):
        # A tier-1 metric runs on every evaluation, so a 2s interval is a lie
        # the scheduler would silently ignore.
        with self.assertRaises(ValueError):
            RiskMetricScheduleConfig(
                "TICK", tier=1, base_interval_sec=2.0, accelerated_interval_sec=0.0)

    def test_rejects_non_finite_and_negative_intervals(self):
        for bad in (float("nan"), float("inf"), -1.0):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    RiskMetricScheduleConfig(
                        "M", tier=2, base_interval_sec=bad, accelerated_interval_sec=0.0)

    def test_rejects_empty_name_bad_tier_and_bad_cost(self):
        with self.assertRaises(ValueError):
            RiskMetricScheduleConfig("  ", tier=2, base_interval_sec=1.0,
                                     accelerated_interval_sec=1.0)
        with self.assertRaises(ValueError):
            RiskMetricScheduleConfig("M", tier=0, base_interval_sec=1.0,
                                     accelerated_interval_sec=1.0)
        with self.assertRaises(ValueError):
            RiskMetricScheduleConfig("M", tier=2, base_interval_sec=1.0,
                                     accelerated_interval_sec=1.0,
                                     relative_cost_units=0.0)


class TestTunerConstruction(unittest.TestCase):
    def test_rejects_invalid_constructor_arguments(self):
        bad_kwargs = [
            {"pnl_velocity_threshold_usd_per_sec": 0.0},
            {"pnl_velocity_threshold_usd_per_sec": float("nan")},
            {"min_velocity_sample_sec": 0.0},
            {"acceleration_exit_ratio": 0.0},
            {"acceleration_exit_ratio": 1.5},
            {"acceleration_min_dwell_sec": -1.0},
            {"staleness_multiple": 0.9},
        ]
        for kwargs in bad_kwargs:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    RiskMetricFrequencyTuner(**kwargs)

    def test_rejects_empty_and_duplicate_configs(self):
        with self.assertRaises(ValueError):
            RiskMetricFrequencyTuner(configs=[])
        dup = [
            RiskMetricScheduleConfig("M", tier=2, base_interval_sec=1.0,
                                     accelerated_interval_sec=1.0),
            RiskMetricScheduleConfig("M", tier=3, base_interval_sec=2.0,
                                     accelerated_interval_sec=1.0),
        ]
        with self.assertRaises(ValueError):
            RiskMetricFrequencyTuner(configs=dup)

    def test_caller_config_objects_are_not_mutated(self):
        cfgs = default_schedule()
        tuner = RiskMetricFrequencyTuner(configs=cfgs)
        tuner.evaluate_due_metrics(current_pnl_usd=0.0, current_timestamp_sec=10.0)
        self.assertTrue(
            all(c.last_calculated_timestamp is None for c in cfgs),
            "scheduling must not write through to the caller's config objects")


class TestTieredCadence(unittest.TestCase):
    def setUp(self):
        self.tuner = RiskMetricFrequencyTuner(pnl_velocity_threshold_usd_per_sec=500.0)

    def test_all_metrics_due_on_first_evaluation(self):
        rep = self.tuner.evaluate_due_metrics(current_pnl_usd=1000.0,
                                              current_timestamp_sec=100.0)
        self.assertEqual(
            rep.metrics_due_for_calc,
            ["TICK_DRAWDOWN", "GREEKS_DELTA", "VAR_1DAY", "STRESS_TEST"])
        self.assertFalse(rep.is_accelerated_mode)
        self.assertEqual(rep.pnl_velocity_usd_per_sec, 0.0)

    def test_first_evaluation_is_independent_of_clock_epoch(self):
        # Regression: with a 0.0 "never calculated" sentinel, a monotonic clock
        # starting near zero left VAR_1DAY and STRESS_TEST not-due on the very
        # first call, while an epoch clock made them due. Same schedule, same
        # first call -- the answer must not depend on the caller's clock.
        epoch = RiskMetricFrequencyTuner().evaluate_due_metrics(0.0, 1_700_000_000.0)
        monotonic = RiskMetricFrequencyTuner().evaluate_due_metrics(0.0, 0.5)
        self.assertEqual(epoch.metrics_due_for_calc, monotonic.metrics_due_for_calc)
        self.assertIn("STRESS_TEST", monotonic.metrics_due_for_calc)

    def test_only_tier_one_due_between_intervals(self):
        self.tuner.evaluate_due_metrics(1000.0, 100.0)
        # +1.0s, $10 move -> $10/s, far below threshold. GREEKS needs 2.0s.
        rep = self.tuner.evaluate_due_metrics(1010.0, 101.0)
        self.assertEqual(rep.metrics_due_for_calc, ["TICK_DRAWDOWN"])
        self.assertFalse(rep.is_accelerated_mode)

    def test_metrics_become_due_exactly_at_their_interval(self):
        self.tuner.evaluate_due_metrics(1000.0, 100.0)
        # t=101.9: GREEKS elapsed 1.9 < 2.0 -> not due.
        self.assertNotIn("GREEKS_DELTA",
                         self.tuner.evaluate_due_metrics(1001.0, 101.9).metrics_due_for_calc)
        # t=102.0: elapsed exactly 2.0 -> due (the boundary is inclusive).
        rep = self.tuner.evaluate_due_metrics(1002.0, 102.0)
        self.assertIn("GREEKS_DELTA", rep.metrics_due_for_calc)
        self.assertNotIn("VAR_1DAY", rep.metrics_due_for_calc)

    def test_tier_three_and_four_fire_on_their_own_cadence(self):
        self.tuner.evaluate_due_metrics(1000.0, 0.0)
        # Creep P&L by $1 per 10s = $0.10/s so acceleration never engages.
        due_at = {}
        for step in range(1, 31):
            t = 10.0 * step
            rep = self.tuner.evaluate_due_metrics(1000.0 + step, t)
            self.assertFalse(rep.is_accelerated_mode)
            for name in rep.metrics_due_for_calc:
                due_at.setdefault(name, []).append(t)
        # VAR_1DAY (30s) first re-fires at t=30, STRESS_TEST (300s) at t=300.
        self.assertEqual(due_at["VAR_1DAY"][0], 30.0)
        self.assertEqual(due_at["STRESS_TEST"][0], 300.0)
        self.assertEqual(len(due_at["TICK_DRAWDOWN"]), 30)


class TestVelocityAndAcceleration(unittest.TestCase):
    def setUp(self):
        self.tuner = RiskMetricFrequencyTuner(pnl_velocity_threshold_usd_per_sec=500.0)

    def test_high_velocity_engages_acceleration(self):
        self.tuner.evaluate_due_metrics(1000.0, 100.0)
        # -$2,000 over 1.0s -> $2,000/s >= $500/s.
        rep = self.tuner.evaluate_due_metrics(-1000.0, 101.0)
        self.assertTrue(rep.is_accelerated_mode)
        self.assertEqual(rep.pnl_velocity_usd_per_sec, 2000.0)

    def test_entering_acceleration_forces_immediate_recalculation(self):
        # Regression: the spike tick used to announce acceleration while leaving
        # VAR_1DAY not-due (elapsed 1.0s < 5.0s accelerated interval), so the
        # heavy metrics did not actually recompute until t=105.
        self.tuner.evaluate_due_metrics(1000.0, 100.0)
        rep = self.tuner.evaluate_due_metrics(-1000.0, 101.0)
        self.assertTrue(rep.is_accelerated_mode)
        for name in ("TICK_DRAWDOWN", "GREEKS_DELTA", "VAR_1DAY", "STRESS_TEST"):
            self.assertIn(name, rep.metrics_due_for_calc)

    def test_threshold_boundary_is_inclusive(self):
        tuner = RiskMetricFrequencyTuner(pnl_velocity_threshold_usd_per_sec=500.0)
        tuner.evaluate_due_metrics(0.0, 0.0)
        # Exactly $500 over 1.0s.
        self.assertTrue(tuner.evaluate_due_metrics(500.0, 1.0).is_accelerated_mode)

        below = RiskMetricFrequencyTuner(pnl_velocity_threshold_usd_per_sec=500.0)
        below.evaluate_due_metrics(0.0, 0.0)
        self.assertFalse(below.evaluate_due_metrics(499.99, 1.0).is_accelerated_mode)

    def test_velocity_uses_absolute_change_so_rallies_also_accelerate(self):
        self.tuner.evaluate_due_metrics(0.0, 0.0)
        rep = self.tuner.evaluate_due_metrics(900.0, 1.0)
        self.assertEqual(rep.pnl_velocity_usd_per_sec, 900.0)
        self.assertTrue(rep.is_accelerated_mode)

    def test_sub_window_samples_do_not_fabricate_velocity(self):
        # Regression: the old max(0.001, dt) clamp turned a $1 wobble across a
        # 1ms tick gap into $1,000/s and accelerated the whole engine on noise.
        tuner = RiskMetricFrequencyTuner(pnl_velocity_threshold_usd_per_sec=500.0,
                                         min_velocity_sample_sec=0.25)
        tuner.evaluate_due_metrics(1000.0, 0.0)
        rep = tuner.evaluate_due_metrics(1001.0, 0.001)
        self.assertEqual(rep.pnl_velocity_usd_per_sec, 0.0)
        self.assertFalse(rep.is_accelerated_mode)

    def test_sub_window_change_is_accumulated_not_discarded(self):
        # The anchor is held, not reset: $600 accumulated over 0.1s + 0.4s is
        # measured across the full 0.5s span -> $1,200/s, not lost.
        tuner = RiskMetricFrequencyTuner(pnl_velocity_threshold_usd_per_sec=500.0,
                                         min_velocity_sample_sec=0.25)
        tuner.evaluate_due_metrics(0.0, 0.0)
        self.assertEqual(tuner.evaluate_due_metrics(300.0, 0.1).pnl_velocity_usd_per_sec, 0.0)
        rep = tuner.evaluate_due_metrics(600.0, 0.5)
        self.assertEqual(rep.pnl_velocity_usd_per_sec, 1200.0)
        self.assertTrue(rep.is_accelerated_mode)

    def test_repeated_timestamp_does_not_divide_by_zero(self):
        self.tuner.evaluate_due_metrics(1000.0, 50.0)
        rep = self.tuner.evaluate_due_metrics(9999.0, 50.0)
        self.assertTrue(math.isfinite(rep.pnl_velocity_usd_per_sec))
        self.assertEqual(rep.pnl_velocity_usd_per_sec, 0.0)


class TestHysteresis(unittest.TestCase):
    def setUp(self):
        self.tuner = RiskMetricFrequencyTuner(
            pnl_velocity_threshold_usd_per_sec=500.0,
            acceleration_exit_ratio=0.5,
            acceleration_min_dwell_sec=30.0)

    def test_single_quiet_sample_does_not_end_acceleration(self):
        # Regression: mode was a stateless per-sample comparison, so one calm
        # tick mid-crash dropped stress testing back to a 300s cadence.
        self.tuner.evaluate_due_metrics(0.0, 0.0)
        self.assertTrue(self.tuner.evaluate_due_metrics(-2000.0, 1.0).is_accelerated_mode)
        # $10/s: well below the $250/s exit level, but only 1s of dwell elapsed.
        self.assertTrue(self.tuner.evaluate_due_metrics(-2010.0, 2.0).is_accelerated_mode)

    def test_exit_requires_both_low_velocity_and_dwell_time(self):
        self.tuner.evaluate_due_metrics(0.0, 0.0)
        self.tuner.evaluate_due_metrics(-2000.0, 1.0)
        # t=40: dwell satisfied (39s >= 30s) but velocity is $300/s, above the
        # $250/s exit level -> stay accelerated.
        self.assertTrue(
            self.tuner.evaluate_due_metrics(-2000.0 - 11700.0, 40.0).is_accelerated_mode)
        # t=80: quiet ($0/s) and dwell long satisfied -> exit.
        self.assertFalse(
            self.tuner.evaluate_due_metrics(-13700.0, 80.0).is_accelerated_mode)

    def test_dwell_alone_does_not_hold_acceleration_forever(self):
        self.tuner.evaluate_due_metrics(0.0, 0.0)
        self.tuner.evaluate_due_metrics(-2000.0, 1.0)
        self.assertFalse(self.tuner.evaluate_due_metrics(-2000.0, 40.0).is_accelerated_mode)

    def test_dwell_is_measured_from_a_zero_valued_entry_timestamp(self):
        # Regression: `self._accelerated_since or timestamp` treats an entry at
        # t=0.0 as "unset" and re-zeroes the dwell on every call, pinning the
        # engine in accelerated mode forever on a monotonic clock that starts
        # at zero. Seed the anchor at a negative timestamp so acceleration is
        # entered exactly at t=0.0.
        tuner = RiskMetricFrequencyTuner(pnl_velocity_threshold_usd_per_sec=500.0,
                                         acceleration_min_dwell_sec=30.0)
        tuner.evaluate_due_metrics(0.0, -1.0)
        self.assertTrue(tuner.evaluate_due_metrics(-2000.0, 0.0).is_accelerated_mode)
        # t=40: quiet and 40s of dwell elapsed -> must exit.
        self.assertFalse(tuner.evaluate_due_metrics(-2000.0, 40.0).is_accelerated_mode)

    def test_exit_ratio_of_one_disables_the_hysteresis_band(self):
        tuner = RiskMetricFrequencyTuner(pnl_velocity_threshold_usd_per_sec=500.0,
                                         acceleration_exit_ratio=1.0,
                                         acceleration_min_dwell_sec=0.0)
        tuner.evaluate_due_metrics(0.0, 0.0)
        self.assertTrue(tuner.evaluate_due_metrics(-2000.0, 1.0).is_accelerated_mode)
        # $499/s: below the entry threshold and <= the exit level -> exits at once.
        self.assertFalse(tuner.evaluate_due_metrics(-2499.0, 2.0).is_accelerated_mode)


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.tuner = RiskMetricFrequencyTuner()

    def test_non_finite_pnl_raises_instead_of_disabling_acceleration(self):
        # Regression: NaN >= threshold is False, so a corrupted P&L feed used to
        # silently pin the engine in normal cadence exactly when risk numbers
        # were least trustworthy.
        self.tuner.evaluate_due_metrics(1000.0, 1.0)
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.tuner.evaluate_due_metrics(bad, 2.0)

    def test_non_finite_timestamp_raises(self):
        with self.assertRaises(ValueError):
            self.tuner.evaluate_due_metrics(1000.0, float("nan"))

    def test_backwards_timestamp_raises(self):
        # Regression: max(0.001, dt) on a negative dt produced |dPnL| * 1000 and
        # a spurious acceleration from an out-of-order replay event.
        self.tuner.evaluate_due_metrics(1000.0, 100.0)
        with self.assertRaises(ValueError):
            self.tuner.evaluate_due_metrics(1050.0, 99.0)

    def test_state_is_unchanged_after_a_rejected_evaluation(self):
        self.tuner.evaluate_due_metrics(1000.0, 100.0)
        with self.assertRaises(ValueError):
            self.tuner.evaluate_due_metrics(float("nan"), 101.0)
        rep = self.tuner.evaluate_due_metrics(1001.0, 101.0)
        self.assertEqual(rep.evaluations_observed, 2)
        self.assertEqual(rep.metrics_due_for_calc, ["TICK_DRAWDOWN"])


class TestStalenessReporting(unittest.TestCase):
    def test_missed_cadence_is_reported_on_the_next_evaluation(self):
        tuner = RiskMetricFrequencyTuner(staleness_multiple=2.0)
        tuner.evaluate_due_metrics(0.0, 0.0)
        # 70s feed gap. VAR_1DAY (30s base) is 70s late -> > 2 x 30s.
        # GREEKS_DELTA (2s) likewise. STRESS_TEST (300s) is not yet overdue.
        rep = tuner.evaluate_due_metrics(10.0, 70.0)
        self.assertIn("VAR_1DAY", rep.overdue_metrics)
        self.assertIn("GREEKS_DELTA", rep.overdue_metrics)
        self.assertNotIn("STRESS_TEST", rep.overdue_metrics)
        self.assertNotIn("TICK_DRAWDOWN", rep.overdue_metrics)
        self.assertIn("RISK METRICS STALE", rep.status_message)

    def test_mode_change_is_not_misreported_as_a_missed_cadence(self):
        # Entering accelerated mode shortens VAR_1DAY from 30s to 5s. A metric
        # calculated 12s ago is late against the new 5s interval (12 > 2 x 5)
        # but was on cadence under the 30s interval actually in force
        # (12 < 2 x 30), so it must not be reported overdue.
        tuner = RiskMetricFrequencyTuner(
            pnl_velocity_threshold_usd_per_sec=500.0,
            staleness_multiple=2.0,
            configs=[
                RiskMetricScheduleConfig("TICK_DRAWDOWN", tier=1, base_interval_sec=0.0,
                                         accelerated_interval_sec=0.0),
                RiskMetricScheduleConfig("VAR_1DAY", tier=3, base_interval_sec=30.0,
                                         accelerated_interval_sec=5.0),
            ])
        tuner.evaluate_due_metrics(0.0, 0.0)
        rep = tuner.evaluate_due_metrics(-6000.0, 12.0)  # $500/s -> accelerates
        self.assertTrue(rep.is_accelerated_mode)
        self.assertEqual(rep.overdue_metrics, [])

    def test_no_staleness_reported_on_a_healthy_cadence(self):
        tuner = RiskMetricFrequencyTuner()
        tuner.evaluate_due_metrics(0.0, 0.0)
        for step in range(1, 20):
            rep = tuner.evaluate_due_metrics(float(step), step * 1.0)
            self.assertEqual(rep.overdue_metrics, [])


class TestLoadReduction(unittest.TestCase):
    def test_reduction_is_measured_from_actual_scheduling(self):
        # Hand-derived: 4 metrics. Evaluation 1 schedules all 4 (first-run),
        # evaluations 2..10 at 1s spacing schedule TICK_DRAWDOWN only, except
        # t=2,4,6,8 where GREEKS_DELTA (2s) also fires.
        # executed = 4 + 9 + 4 = 17; naive = 10 x 4 = 40; reduction = 57.5%.
        tuner = RiskMetricFrequencyTuner()
        tuner.evaluate_due_metrics(0.0, 0.0)
        for step in range(1, 10):
            rep = tuner.evaluate_due_metrics(float(step), float(step))
        self.assertEqual(rep.evaluations_observed, 10)
        self.assertEqual(rep.cost_units_naive, 40.0)
        self.assertEqual(rep.cost_units_executed, 17.0)
        self.assertAlmostEqual(rep.calculation_load_reduction_pct, 57.5, places=6)

    def test_reduction_is_zero_when_every_metric_runs_every_evaluation(self):
        # A single tier-1 metric can never be skipped, so no load is avoided.
        tuner = RiskMetricFrequencyTuner(configs=[
            RiskMetricScheduleConfig("TICK", tier=1, base_interval_sec=0.0,
                                     accelerated_interval_sec=0.0)])
        tuner.evaluate_due_metrics(0.0, 0.0)
        rep = tuner.evaluate_due_metrics(1.0, 1.0)
        self.assertEqual(rep.calculation_load_reduction_pct, 0.0)

    def test_relative_costs_weight_the_reduction(self):
        # STRESS costs 100 units, TICK 1. Evaluation 1 runs both (101 units),
        # evaluation 2 at t=1 runs TICK only (1 unit).
        # executed = 102; naive = 2 x 101 = 202; reduction = 100 x (1 - 102/202).
        tuner = RiskMetricFrequencyTuner(configs=[
            RiskMetricScheduleConfig("TICK", tier=1, base_interval_sec=0.0,
                                     accelerated_interval_sec=0.0,
                                     relative_cost_units=1.0),
            RiskMetricScheduleConfig("STRESS", tier=4, base_interval_sec=300.0,
                                     accelerated_interval_sec=30.0,
                                     relative_cost_units=100.0)])
        tuner.evaluate_due_metrics(0.0, 0.0)
        rep = tuner.evaluate_due_metrics(1.0, 1.0)
        self.assertEqual(rep.cost_units_naive, 202.0)
        self.assertEqual(rep.cost_units_executed, 102.0)
        self.assertAlmostEqual(rep.calculation_load_reduction_pct,
                               round(100.0 * (1.0 - 102.0 / 202.0), 2), places=6)

    def test_no_hardcoded_75_percent_claim(self):
        # Regression: the reduction figure was a hardcoded 75.0 / 40.0 constant
        # that ignored the schedule entirely. With one tier-1 metric the honest
        # answer is 0%, not 75%.
        tuner = RiskMetricFrequencyTuner(configs=[
            RiskMetricScheduleConfig("TICK", tier=1, base_interval_sec=0.0,
                                     accelerated_interval_sec=0.0)])
        rep = tuner.evaluate_due_metrics(0.0, 0.0)
        self.assertNotEqual(rep.calculation_load_reduction_pct, 75.0)
        self.assertEqual(rep.calculation_load_reduction_pct, 0.0)


class TestIntrospection(unittest.TestCase):
    def test_snapshot_is_a_copy(self):
        tuner = RiskMetricFrequencyTuner()
        tuner.evaluate_due_metrics(0.0, 5.0)
        snap = tuner.schedule_snapshot()
        self.assertEqual({c.metric_name for c in snap},
                         {"TICK_DRAWDOWN", "GREEKS_DELTA", "VAR_1DAY", "STRESS_TEST"})
        for cfg in snap:
            cfg.last_calculated_timestamp = -999.0
        self.assertTrue(
            all(c.last_calculated_timestamp == 5.0 for c in tuner.schedule_snapshot()))

    def test_is_accelerated_mode_property_tracks_state(self):
        tuner = RiskMetricFrequencyTuner(pnl_velocity_threshold_usd_per_sec=500.0)
        tuner.evaluate_due_metrics(0.0, 0.0)
        self.assertFalse(tuner.is_accelerated_mode)
        tuner.evaluate_due_metrics(-2000.0, 1.0)
        self.assertTrue(tuner.is_accelerated_mode)


if __name__ == "__main__":
    unittest.main()
