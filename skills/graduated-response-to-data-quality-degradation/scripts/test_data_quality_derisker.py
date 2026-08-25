"""Behavioural tests for the graduated data-quality de-risking engine.

Expected scores are derived by hand from the documented penalty table rather than
by re-running the implementation's own arithmetic:

    stale_data        = (stale_seconds - 1.0) * 10.0   for stale_seconds > 1.0
    missing_sequences = missing_count * 2.0
    price_spike       = 25.0
    crossed_book      = 50.0
    wide_spread       = (multiplier - 2.0) * 15.0      for multiplier > 2.0
    Q                 = clamp(100 - sum(penalties), 0, 100)

Time-dependent recovery-hold behaviour is driven by an injected fake clock rather
than ``time.sleep``, so the hold boundary is asserted exactly.
"""

import logging
import math
import threading
import unittest

from data_quality_derisker import (
    DataQualityDeRiskerEngine,
    DataQualityMetrics,
)

logging.disable(logging.CRITICAL)


class FakeClock:
    """Deterministic monotonic clock."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


class TestQualityScoring(unittest.TestCase):
    def setUp(self):
        self.engine = DataQualityDeRiskerEngine()

    def test_clean_feed_scores_100_and_allows_full_trading(self):
        report = self.engine.audit_and_de_risk(
            DataQualityMetrics("AAPL", stale_time_seconds=0.1)
        )
        self.assertEqual(report.data_quality_score_pct, 100.0)
        self.assertEqual(report.de_risking_tier, 0)
        self.assertEqual(report.tier_name, "TIER_0_NORMAL")
        self.assertEqual(report.action_mandate, "ALLOW_FULL_TRADING")
        self.assertEqual(report.position_sizing_factor, 1.0)
        self.assertTrue(report.allow_new_entries)
        self.assertFalse(report.cancel_resting_orders)
        self.assertFalse(report.flatten_positions)
        self.assertEqual(report.penalty_breakdown, {})
        self.assertEqual(report.triggered_conditions, [])

    def test_staleness_penalty_is_per_second_beyond_one_second_grace(self):
        # 2.5s -> (2.5 - 1.0) * 10 = 15.0 penalty -> Q = 85.0 -> Tier 1.
        report = self.engine.audit_and_de_risk(
            DataQualityMetrics("AAPL", stale_time_seconds=2.5)
        )
        self.assertAlmostEqual(report.penalty_breakdown["stale_data"], 15.0, places=9)
        self.assertEqual(report.data_quality_score_pct, 85.0)
        self.assertEqual(report.de_risking_tier, 1)
        self.assertEqual(report.action_mandate, "REDUCE_SIZE_50_PCT")
        self.assertEqual(report.position_sizing_factor, 0.50)

    def test_staleness_within_grace_is_not_penalised(self):
        report = self.engine.audit_and_de_risk(
            DataQualityMetrics("AAPL", stale_time_seconds=1.0)
        )
        self.assertEqual(report.data_quality_score_pct, 100.0)
        self.assertEqual(report.de_risking_tier, 0)

    def test_missing_sequence_penalty_is_two_points_each(self):
        # 6 missing -> 12.0 penalty -> Q = 88.0 -> Tier 1.
        report = self.engine.audit_and_de_risk(
            DataQualityMetrics("AAPL", stale_time_seconds=0.0, missing_sequence_count=6)
        )
        self.assertAlmostEqual(report.penalty_breakdown["missing_sequences"], 12.0, places=9)
        self.assertEqual(report.data_quality_score_pct, 88.0)
        self.assertEqual(report.de_risking_tier, 1)
        self.assertIn("SEQUENCE_GAP:6", report.triggered_conditions)

    def test_price_spike_alone_costs_25_points(self):
        # Q = 75.0 -> Tier 1.
        report = self.engine.audit_and_de_risk(
            DataQualityMetrics(
                "AAPL", stale_time_seconds=0.0, price_spike_anomaly_detected=True
            )
        )
        self.assertEqual(report.data_quality_score_pct, 75.0)
        self.assertEqual(report.de_risking_tier, 1)
        self.assertIn("PRICE_SPIKE_ANOMALY", report.triggered_conditions)

    def test_spread_blowout_penalty_is_documented_and_applied(self):
        # 4.0x normal spread -> (4.0 - 2.0) * 15 = 30.0 penalty -> Q = 70.0 -> Tier 1
        # boundary. This penalty exists in the engine and must stay documented.
        report = self.engine.audit_and_de_risk(
            DataQualityMetrics(
                "AAPL", stale_time_seconds=0.0, bid_ask_spread_multiplier=4.0
            )
        )
        self.assertAlmostEqual(report.penalty_breakdown["wide_spread"], 30.0, places=9)
        self.assertEqual(report.data_quality_score_pct, 70.0)
        self.assertEqual(report.de_risking_tier, 1)

    def test_spread_within_grace_multiple_is_not_penalised(self):
        report = self.engine.audit_and_de_risk(
            DataQualityMetrics(
                "AAPL", stale_time_seconds=0.0, bid_ask_spread_multiplier=2.0
            )
        )
        self.assertEqual(report.data_quality_score_pct, 100.0)

    def test_score_is_clamped_at_zero_when_penalties_exceed_100(self):
        # 90 (stale 10s) + 25 (spike) + 50 (crossed) = 165 -> clamped to 0.
        report = self.engine.audit_and_de_risk(
            DataQualityMetrics(
                "AAPL",
                stale_time_seconds=10.0,
                price_spike_anomaly_detected=True,
                crossed_book_detected=True,
            )
        )
        self.assertEqual(report.data_quality_score_pct, 0.0)
        self.assertEqual(report.de_risking_tier, 3)


class TestTierBoundaries(unittest.TestCase):
    def setUp(self):
        self.engine = DataQualityDeRiskerEngine()

    def test_exact_90_is_tier_0(self):
        # stale 2.0s -> 10.0 penalty -> Q = 90.0 exactly. The lower bound is inclusive.
        report = self.engine.audit_and_de_risk(
            DataQualityMetrics("AAPL", stale_time_seconds=2.0)
        )
        self.assertEqual(report.data_quality_score_pct, 90.0)
        self.assertEqual(report.de_risking_tier, 0)

    def test_exact_70_is_tier_1(self):
        # stale 4.0s -> 30.0 penalty -> Q = 70.0 exactly.
        report = self.engine.audit_and_de_risk(
            DataQualityMetrics("AAPL", stale_time_seconds=4.0)
        )
        self.assertEqual(report.data_quality_score_pct, 70.0)
        self.assertEqual(report.de_risking_tier, 1)

    def test_exact_40_is_tier_2(self):
        # stale 7.0s -> 60.0 penalty -> Q = 40.0 exactly.
        report = self.engine.audit_and_de_risk(
            DataQualityMetrics("AAPL", stale_time_seconds=7.0)
        )
        self.assertEqual(report.data_quality_score_pct, 40.0)
        self.assertEqual(report.de_risking_tier, 2)

    def test_score_just_below_a_boundary_does_not_round_up_into_full_trading(self):
        # Regression: stale 2.0004s -> 10.004 penalty -> Q = 89.996. Rounding the score
        # to 2dp *before* classifying yields 90.00 and grants ALLOW_FULL_TRADING.
        # Classification must use the exact score, and the reported score must floor.
        report = self.engine.audit_and_de_risk(
            DataQualityMetrics("AAPL", stale_time_seconds=2.0004)
        )
        self.assertEqual(report.de_risking_tier, 1)
        self.assertEqual(report.action_mandate, "REDUCE_SIZE_50_PCT")
        self.assertEqual(report.data_quality_score_pct, 89.99)

    def test_reported_score_never_overstates_the_assigned_tier(self):
        for stale in (1.0001, 2.0004, 4.00001, 7.000001, 9.9999):
            with self.subTest(stale=stale):
                report = self.engine.audit_and_de_risk(
                    DataQualityMetrics("AAPL", stale_time_seconds=stale)
                )
                bounds = {0: 90.0, 1: 70.0, 2: 40.0, 3: 0.0}
                self.assertGreaterEqual(
                    report.data_quality_score_pct, bounds[report.de_risking_tier] - 0.01
                )


class TestActionMandates(unittest.TestCase):
    """Tier 2 and Tier 3 both carry position_sizing_factor 0.0, so the booleans -- not
    the float -- are what a caller must gate exits on."""

    def setUp(self):
        self.engine = DataQualityDeRiskerEngine()

    def test_tier_2_blocks_entries_but_still_permits_exits(self):
        # Crossed book alone -> 50.0 penalty -> Q = 50.0 -> Tier 2.
        report = self.engine.audit_and_de_risk(
            DataQualityMetrics("AAPL", stale_time_seconds=0.0, crossed_book_detected=True)
        )
        self.assertEqual(report.data_quality_score_pct, 50.0)
        self.assertEqual(report.de_risking_tier, 2)
        self.assertEqual(report.action_mandate, "BLOCK_NEW_ENTRIES")
        self.assertEqual(report.position_sizing_factor, 0.0)
        self.assertFalse(report.allow_new_entries)
        self.assertTrue(report.allow_risk_reducing_exits)
        self.assertTrue(report.cancel_resting_orders)
        self.assertFalse(report.flatten_positions)

    def test_tier_3_cancels_and_flattens(self):
        # stale 3.0s (20.0) + crossed book (50.0) = 70.0 penalty -> Q = 30.0 -> Tier 3.
        report = self.engine.audit_and_de_risk(
            DataQualityMetrics(
                "AAPL", stale_time_seconds=3.0, crossed_book_detected=True
            )
        )
        self.assertEqual(report.data_quality_score_pct, 30.0)
        self.assertEqual(report.de_risking_tier, 3)
        self.assertEqual(report.action_mandate, "EMERGENCY_HALT_AND_FLATTEN")
        self.assertEqual(report.position_sizing_factor, 0.0)
        self.assertFalse(report.allow_new_entries)
        self.assertTrue(report.allow_risk_reducing_exits)
        self.assertTrue(report.cancel_resting_orders)
        self.assertTrue(report.flatten_positions)

    def test_exits_are_permitted_at_every_tier(self):
        cases = [
            DataQualityMetrics("AAPL", stale_time_seconds=0.0),
            DataQualityMetrics("AAPL", stale_time_seconds=2.5),
            DataQualityMetrics("AAPL", stale_time_seconds=0.0, crossed_book_detected=True),
            DataQualityMetrics(
                "AAPL", stale_time_seconds=3.0, crossed_book_detected=True
            ),
        ]
        for metrics in cases:
            with self.subTest(stale=metrics.stale_time_seconds):
                report = self.engine.audit_and_de_risk(metrics)
                self.assertTrue(report.allow_risk_reducing_exits)


class TestCorruptTelemetryFailsClosed(unittest.TestCase):
    """A metric that cannot be evaluated must not be scored as clean."""

    def setUp(self):
        self.engine = DataQualityDeRiskerEngine()

    def _assert_failed_closed(self, metrics, field_name):
        report = self.engine.audit_and_de_risk(metrics)
        self.assertFalse(report.metrics_valid)
        self.assertEqual(report.data_quality_score_pct, 0.0)
        self.assertEqual(report.de_risking_tier, 3)
        self.assertEqual(report.action_mandate, "EMERGENCY_HALT_AND_FLATTEN")
        self.assertIn(f"INVALID_METRIC:{field_name}", report.triggered_conditions)

    def test_nan_staleness_does_not_score_as_a_perfect_feed(self):
        # Regression: `NaN > 1.0` is False, so a naive implementation applies no
        # penalty and returns Q = 100 / ALLOW_FULL_TRADING on unusable telemetry.
        self._assert_failed_closed(
            DataQualityMetrics("AAPL", stale_time_seconds=float("nan")),
            "stale_time_seconds",
        )

    def test_infinite_staleness_fails_closed(self):
        self._assert_failed_closed(
            DataQualityMetrics("AAPL", stale_time_seconds=float("inf")),
            "stale_time_seconds",
        )

    def test_negative_staleness_from_clock_skew_fails_closed(self):
        # A tick timestamped in the future yields negative age: the clocks are wrong,
        # which is a data-quality failure, not a clean feed.
        self._assert_failed_closed(
            DataQualityMetrics("AAPL", stale_time_seconds=-0.5), "stale_time_seconds"
        )

    def test_nan_spread_multiplier_fails_closed(self):
        self._assert_failed_closed(
            DataQualityMetrics(
                "AAPL", stale_time_seconds=0.0, bid_ask_spread_multiplier=float("nan")
            ),
            "bid_ask_spread_multiplier",
        )

    def test_non_positive_spread_multiplier_fails_closed(self):
        self._assert_failed_closed(
            DataQualityMetrics(
                "AAPL", stale_time_seconds=0.0, bid_ask_spread_multiplier=0.0
            ),
            "bid_ask_spread_multiplier",
        )

    def test_negative_missing_sequence_count_fails_closed(self):
        self._assert_failed_closed(
            DataQualityMetrics(
                "AAPL", stale_time_seconds=0.0, missing_sequence_count=-3
            ),
            "missing_sequence_count",
        )

    def test_non_boolean_flag_fails_closed(self):
        self._assert_failed_closed(
            DataQualityMetrics(
                "AAPL", stale_time_seconds=0.0, crossed_book_detected="yes"
            ),
            "crossed_book_detected",
        )

    def test_valid_metrics_are_marked_valid(self):
        report = self.engine.audit_and_de_risk(
            DataQualityMetrics("AAPL", stale_time_seconds=0.5)
        )
        self.assertTrue(report.metrics_valid)


class TestInputContract(unittest.TestCase):
    def setUp(self):
        self.engine = DataQualityDeRiskerEngine()

    def test_wrong_metrics_type_raises(self):
        with self.assertRaises(TypeError):
            self.engine.audit_and_de_risk({"symbol": "AAPL", "stale_time_seconds": 0.0})

    def test_blank_symbol_raises(self):
        with self.assertRaises(ValueError):
            self.engine.audit_and_de_risk(
                DataQualityMetrics("   ", stale_time_seconds=0.0)
            )

    def test_staleness_has_no_default_so_liveness_must_be_measured(self):
        with self.assertRaises(TypeError):
            DataQualityMetrics("AAPL")  # type: ignore[call-arg]


class TestConfigurationValidation(unittest.TestCase):
    def test_non_descending_tier_bounds_raise(self):
        with self.assertRaises(ValueError):
            DataQualityDeRiskerEngine(tier_0_min_score=60.0, tier_1_min_score=70.0)

    def test_out_of_range_tier_bound_raises(self):
        with self.assertRaises(ValueError):
            DataQualityDeRiskerEngine(tier_0_min_score=140.0)

    def test_nan_tier_bound_raises(self):
        with self.assertRaises(ValueError):
            DataQualityDeRiskerEngine(tier_1_min_score=float("nan"))

    def test_negative_penalty_raises(self):
        with self.assertRaises(ValueError):
            DataQualityDeRiskerEngine(crossed_book_penalty=-10.0)

    def test_size_factor_outside_zero_to_one_raises(self):
        with self.assertRaises(ValueError):
            DataQualityDeRiskerEngine(tier_1_size_factor=0.0)
        with self.assertRaises(ValueError):
            DataQualityDeRiskerEngine(tier_1_size_factor=1.5)

    def test_negative_recovery_hold_raises(self):
        with self.assertRaises(ValueError):
            DataQualityDeRiskerEngine(recovery_hold_seconds=-1.0)

    def test_custom_thresholds_are_honoured(self):
        engine = DataQualityDeRiskerEngine(
            tier_0_min_score=99.0, tier_1_min_score=95.0, tier_2_min_score=90.0
        )
        # stale 1.5s -> 5.0 penalty -> Q = 95.0 -> Tier 1 under these bounds.
        report = engine.audit_and_de_risk(
            DataQualityMetrics("AAPL", stale_time_seconds=1.5)
        )
        self.assertEqual(report.data_quality_score_pct, 95.0)
        self.assertEqual(report.de_risking_tier, 1)


class TestRecoveryHold(unittest.TestCase):
    """Escalate on the tick that degrades; de-escalate only after sustained recovery."""

    def setUp(self):
        self.clock = FakeClock()
        self.engine = DataQualityDeRiskerEngine(
            recovery_hold_seconds=30.0, clock=self.clock
        )
        self.clean = DataQualityMetrics("AAPL", stale_time_seconds=0.0)
        self.severe = DataQualityMetrics(
            "AAPL", stale_time_seconds=3.0, crossed_book_detected=True
        )

    def test_escalation_is_immediate(self):
        self.assertEqual(self.engine.audit_and_de_risk(self.clean).de_risking_tier, 0)
        report = self.engine.audit_and_de_risk(self.severe)
        self.assertEqual(report.de_risking_tier, 3)
        self.assertFalse(report.tier_held_by_recovery)

    def test_de_escalation_waits_for_the_full_hold(self):
        self.engine.audit_and_de_risk(self.severe)

        self.clock.advance(1.0)
        held = self.engine.audit_and_de_risk(self.clean)
        self.assertEqual(held.de_risking_tier, 3)
        self.assertEqual(held.instantaneous_tier, 0)
        self.assertTrue(held.tier_held_by_recovery)
        self.assertTrue(held.flatten_positions)

        self.clock.advance(29.9)
        still_held = self.engine.audit_and_de_risk(self.clean)
        self.assertEqual(still_held.de_risking_tier, 3)
        self.assertTrue(still_held.tier_held_by_recovery)

        self.clock.advance(0.1)  # exactly 30.0s of sustained improvement
        released = self.engine.audit_and_de_risk(self.clean)
        self.assertEqual(released.de_risking_tier, 0)
        self.assertFalse(released.tier_held_by_recovery)

    def test_a_relapse_restarts_the_hold(self):
        self.engine.audit_and_de_risk(self.severe)
        self.clock.advance(1.0)
        self.engine.audit_and_de_risk(self.clean)  # recovery timer starts

        self.clock.advance(20.0)
        self.engine.audit_and_de_risk(self.severe)  # relapse cancels the timer

        self.clock.advance(20.0)
        self.assertEqual(
            self.engine.audit_and_de_risk(self.clean).de_risking_tier, 3
        )

        self.clock.advance(30.0)
        self.assertEqual(
            self.engine.audit_and_de_risk(self.clean).de_risking_tier, 0
        )

    def test_hold_does_not_delay_a_further_escalation(self):
        moderate = DataQualityMetrics(
            "AAPL", stale_time_seconds=0.0, crossed_book_detected=True
        )
        self.engine.audit_and_de_risk(moderate)
        self.assertEqual(self.engine.held_tier("AAPL"), 2)
        report = self.engine.audit_and_de_risk(self.severe)
        self.assertEqual(report.de_risking_tier, 3)

    def test_state_is_isolated_per_symbol(self):
        self.engine.audit_and_de_risk(self.severe)
        other = self.engine.audit_and_de_risk(
            DataQualityMetrics("MSFT", stale_time_seconds=0.0)
        )
        self.assertEqual(other.de_risking_tier, 0)
        self.assertEqual(self.engine.held_tier("AAPL"), 3)
        self.assertEqual(self.engine.held_tier("MSFT"), 0)

    def test_reset_clears_the_hold(self):
        self.engine.audit_and_de_risk(self.severe)
        self.engine.reset("AAPL")
        self.assertIsNone(self.engine.held_tier("AAPL"))
        self.assertEqual(
            self.engine.audit_and_de_risk(self.clean).de_risking_tier, 0
        )

    def test_reset_all_clears_every_symbol(self):
        self.engine.audit_and_de_risk(self.severe)
        self.engine.audit_and_de_risk(
            DataQualityMetrics("MSFT", stale_time_seconds=3.0, crossed_book_detected=True)
        )
        self.engine.reset()
        self.assertIsNone(self.engine.held_tier("AAPL"))
        self.assertIsNone(self.engine.held_tier("MSFT"))

    def test_zero_hold_is_memoryless(self):
        engine = DataQualityDeRiskerEngine(recovery_hold_seconds=0.0)
        self.assertEqual(engine.audit_and_de_risk(self.severe).de_risking_tier, 3)
        report = engine.audit_and_de_risk(self.clean)
        self.assertEqual(report.de_risking_tier, 0)
        self.assertFalse(report.tier_held_by_recovery)
        self.assertIsNone(engine.held_tier("AAPL"))


class TestConcurrency(unittest.TestCase):
    def test_concurrent_audits_on_one_symbol_never_report_better_than_instantaneous(self):
        clock = FakeClock()
        engine = DataQualityDeRiskerEngine(recovery_hold_seconds=60.0, clock=clock)
        engine.audit_and_de_risk(
            DataQualityMetrics("AAPL", stale_time_seconds=3.0, crossed_book_detected=True)
        )
        clean = DataQualityMetrics("AAPL", stale_time_seconds=0.0)
        results = []

        def worker():
            for _ in range(200):
                results.append(engine.audit_and_de_risk(clean).de_risking_tier)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), 800)
        # The hold never elapses on a frozen clock, so every observation stays at 3.
        self.assertEqual(set(results), {3})
        self.assertEqual(engine.held_tier("AAPL"), 3)


class TestReportIntegrity(unittest.TestCase):
    def test_score_is_always_finite_and_bounded(self):
        engine = DataQualityDeRiskerEngine()
        cases = [
            DataQualityMetrics("AAPL", stale_time_seconds=0.0),
            DataQualityMetrics("AAPL", stale_time_seconds=1e6),
            DataQualityMetrics("AAPL", stale_time_seconds=0.0, missing_sequence_count=10**6),
            DataQualityMetrics(
                "AAPL", stale_time_seconds=0.0, bid_ask_spread_multiplier=1e9
            ),
            DataQualityMetrics("AAPL", stale_time_seconds=float("nan")),
        ]
        for metrics in cases:
            with self.subTest(metrics=metrics):
                report = engine.audit_and_de_risk(metrics)
                self.assertTrue(math.isfinite(report.data_quality_score_pct))
                self.assertGreaterEqual(report.data_quality_score_pct, 0.0)
                self.assertLessEqual(report.data_quality_score_pct, 100.0)
                self.assertIn(report.de_risking_tier, (0, 1, 2, 3))

    def test_audit_notes_name_the_triggering_conditions(self):
        engine = DataQualityDeRiskerEngine()
        report = engine.audit_and_de_risk(
            DataQualityMetrics(
                "AAPL", stale_time_seconds=3.0, crossed_book_detected=True
            )
        )
        self.assertIn("AAPL", report.audit_notes)
        self.assertIn("CROSSED_BOOK", report.audit_notes)
        self.assertIn("STALE_FEED", report.audit_notes)


if __name__ == "__main__":
    unittest.main()
