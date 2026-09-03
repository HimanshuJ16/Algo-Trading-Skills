"""
Unit tests for margin-utilization-circuit-breaker.

Expected values are derived independently of the implementation: utilization
figures are stated as plain arithmetic (e.g. 70,000 / 100,000 = 0.70) rather than
recomputed with the module's own expression, so a sign or operand error in the
module cannot also flip the expectation.

Every fail-open test here fails against a naive implementation: NaN inputs,
percent-scaled thresholds, latching, staleness and the risk-reducing carve-out
were all either absent or silently approved.
"""
import logging
import math
import threading
import unittest
from datetime import datetime, timedelta, timezone

from margin_breaker import (
    MarginBasis,
    MarginDataError,
    MarginStatus,
    MarginUtilizationBreaker,
)

NOW = datetime(2026, 8, 25, 13, 30, 0, tzinfo=timezone.utc)


def _breaker(**kwargs) -> MarginUtilizationBreaker:
    kwargs.setdefault("warning_threshold", 0.60)
    kwargs.setdefault("hard_stop_threshold", 0.80)
    kwargs.setdefault("max_data_age_seconds", 30.0)
    return MarginUtilizationBreaker(**kwargs)


class TestUtilizationGrading(unittest.TestCase):
    """Threshold grading, including the exact boundaries."""

    def setUp(self):
        self.breaker = _breaker()

    def test_normal_utilization_is_approved(self):
        # 50,000 / 100,000 = 0.50, below the 0.60 warning.
        result = self.breaker.check_order(50_000, 100_000, as_of=NOW, now=NOW)
        self.assertTrue(result.approved)
        self.assertEqual(result.margin_state.status, MarginStatus.NORMAL)
        self.assertAlmostEqual(result.margin_state.utilization_pct, 0.50)

    def test_warning_utilization_still_approves(self):
        # 70,000 / 100,000 = 0.70: warns, but is not a halt.
        state = self.breaker.evaluate_margin(70_000, 100_000, as_of=NOW, now=NOW)
        self.assertEqual(state.status, MarginStatus.WARNING)
        self.assertIn("MARGIN WARNING", state.message)
        self.assertFalse(self.breaker.is_latched)

    def test_exact_warning_boundary_warns(self):
        # 60,000 / 100,000 = 0.60 exactly. The comparison is >=, so it warns.
        state = self.breaker.evaluate_margin(60_000, 100_000, as_of=NOW, now=NOW)
        self.assertEqual(state.status, MarginStatus.WARNING)

    def test_just_below_warning_boundary_is_normal(self):
        # 59,999 / 100,000 = 0.59999.
        state = self.breaker.evaluate_margin(59_999, 100_000, as_of=NOW, now=NOW)
        self.assertEqual(state.status, MarginStatus.NORMAL)

    def test_exact_hard_stop_boundary_halts(self):
        # 80,000 / 100,000 = 0.80 exactly.
        state = self.breaker.evaluate_margin(80_000, 100_000, as_of=NOW, now=NOW)
        self.assertEqual(state.status, MarginStatus.HARD_STOP)
        self.assertTrue(self.breaker.is_latched)

    def test_hard_stop_blocks_new_orders(self):
        # 90,000 / 100,000 = 0.90.
        result = self.breaker.check_order(90_000, 100_000, as_of=NOW, now=NOW)
        self.assertFalse(result.approved)
        self.assertEqual(result.margin_state.status, MarginStatus.HARD_STOP)
        self.assertIn("MARGIN HARD STOP", result.rejection_reason)

    def test_projected_margin_breach_is_blocked(self):
        # Current 70,000 (0.70) plus 15,000 projects to 85,000 / 100,000 = 0.85.
        result = self.breaker.check_order(
            70_000, 100_000, additional_margin_required=15_000, as_of=NOW, now=NOW
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.margin_state.status, MarginStatus.HARD_STOP)
        self.assertAlmostEqual(result.margin_state.utilization_pct, 0.85)

    def test_projected_state_does_not_latch_a_healthy_account(self):
        """A rejected hypothetical must not halt an account that is fine."""
        result = self.breaker.check_order(
            70_000, 100_000, additional_margin_required=15_000, as_of=NOW, now=NOW
        )
        self.assertFalse(result.approved)
        self.assertFalse(self.breaker.is_latched)
        # The account itself is still only at 0.70, so a zero-impact order passes.
        follow_up = self.breaker.check_order(70_000, 100_000, as_of=NOW, now=NOW)
        self.assertTrue(follow_up.approved)

    def test_basis_is_reported_in_state_and_message(self):
        breaker = _breaker(basis=MarginBasis.INITIAL)
        state = breaker.evaluate_margin(50_000, 100_000, as_of=NOW, now=NOW)
        self.assertEqual(state.basis, MarginBasis.INITIAL)
        self.assertIn("INITIAL", state.message)


class TestNonFiniteAndInvalidInput(unittest.TestCase):
    """
    Fail-closed behaviour. Under a naive implementation a NaN equity produced
    status NORMAL and an approved order.
    """

    def setUp(self):
        self.breaker = _breaker()

    def test_nan_equity_vetoes_the_order(self):
        result = self.breaker.check_order(50_000, float("nan"), as_of=NOW, now=NOW)
        self.assertFalse(result.approved)
        self.assertTrue(result.is_data_error)
        self.assertIsNone(result.margin_state)

    def test_nan_used_margin_vetoes_the_order(self):
        result = self.breaker.check_order(float("nan"), 100_000, as_of=NOW, now=NOW)
        self.assertFalse(result.approved)
        self.assertTrue(result.is_data_error)

    def test_nan_margin_impact_vetoes_the_order(self):
        result = self.breaker.check_order(
            50_000, 100_000, additional_margin_required=float("nan"), as_of=NOW, now=NOW
        )
        self.assertFalse(result.approved)
        self.assertTrue(result.is_data_error)

    def test_infinite_equity_vetoes_the_order(self):
        result = self.breaker.check_order(50_000, float("inf"), as_of=NOW, now=NOW)
        self.assertFalse(result.approved)
        self.assertTrue(result.is_data_error)

    def test_negative_used_margin_is_rejected(self):
        # -50,000 / 100,000 = -0.50 graded as NORMAL under the old implementation.
        result = self.breaker.check_order(-50_000, 100_000, as_of=NOW, now=NOW)
        self.assertFalse(result.approved)
        self.assertTrue(result.is_data_error)

    def test_impossible_release_is_rejected(self):
        """Releasing more margin than is used is not a de-risking order."""
        result = self.breaker.check_order(
            10_000, 100_000, additional_margin_required=-25_000, as_of=NOW, now=NOW
        )
        self.assertFalse(result.approved)
        self.assertTrue(result.is_data_error)

    def test_evaluate_margin_raises_on_nan(self):
        """The monitoring path fails loudly rather than returning a grade."""
        with self.assertRaises(MarginDataError):
            self.breaker.evaluate_margin(50_000, float("nan"), as_of=NOW, now=NOW)

    def test_bool_is_not_accepted_as_a_number(self):
        with self.assertRaises(MarginDataError):
            self.breaker.evaluate_margin(True, 100_000, as_of=NOW, now=NOW)


class TestNonPositiveEquity(unittest.TestCase):
    def setUp(self):
        self.breaker = _breaker()

    def test_zero_equity_halts_with_undefined_utilization(self):
        state = self.breaker.evaluate_margin(50_000, 0.0, as_of=NOW, now=NOW)
        self.assertEqual(state.status, MarginStatus.HARD_STOP)
        self.assertTrue(math.isinf(state.utilization_pct))

    def test_negative_equity_reports_full_cover_shortfall(self):
        """
        Used 150,000 against equity of -50,000 needs 200,000 to cover, not
        150,000. The old implementation reported utilization as exactly 1.0,
        which reads as 'fully used' rather than 'in debit'.
        """
        state = self.breaker.evaluate_margin(150_000, -50_000, as_of=NOW, now=NOW)
        self.assertTrue(math.isinf(state.utilization_pct))
        self.assertAlmostEqual(state.margin_deficit, 200_000.0)
        self.assertEqual(state.available_margin, 0.0)


class TestDeficitReporting(unittest.TestCase):
    def test_deficit_is_exposed_when_used_exceeds_equity(self):
        breaker = _breaker(hard_stop_threshold=0.95)
        # Used 150,000 against equity 100,000: 50,000 short, utilization 1.50.
        state = breaker.evaluate_margin(150_000, 100_000, as_of=NOW, now=NOW)
        self.assertAlmostEqual(state.utilization_pct, 1.50)
        self.assertAlmostEqual(state.margin_deficit, 50_000.0)
        self.assertEqual(state.available_margin, 0.0)

    def test_no_deficit_below_full_utilization(self):
        breaker = _breaker()
        state = breaker.evaluate_margin(40_000, 100_000, as_of=NOW, now=NOW)
        self.assertEqual(state.margin_deficit, 0.0)
        self.assertAlmostEqual(state.available_margin, 60_000.0)


class TestThresholdValidation(unittest.TestCase):
    """
    Configuration errors that silently disable the breaker must not construct.
    an earlier constructor accepted every case below.
    """

    def test_percent_scaled_thresholds_are_rejected(self):
        with self.assertRaises(MarginDataError):
            MarginUtilizationBreaker(warning_threshold=60, hard_stop_threshold=80)

    def test_nan_thresholds_are_rejected(self):
        with self.assertRaises(MarginDataError):
            MarginUtilizationBreaker(
                warning_threshold=float("nan"), hard_stop_threshold=0.80
            )

    def test_zero_and_negative_thresholds_are_rejected(self):
        with self.assertRaises(MarginDataError):
            MarginUtilizationBreaker(warning_threshold=0.0, hard_stop_threshold=0.80)
        with self.assertRaises(MarginDataError):
            MarginUtilizationBreaker(warning_threshold=-0.1, hard_stop_threshold=0.80)

    def test_inverted_thresholds_are_rejected(self):
        with self.assertRaises(MarginDataError):
            MarginUtilizationBreaker(warning_threshold=0.90, hard_stop_threshold=0.80)

    def test_maintenance_basis_rejects_a_threshold_at_or_above_one(self):
        """At 1.0 on the maintenance basis the broker's cushion is already gone."""
        with self.assertRaises(MarginDataError):
            MarginUtilizationBreaker(
                warning_threshold=0.90,
                hard_stop_threshold=1.0,
                basis=MarginBasis.MAINTENANCE,
            )

    def test_initial_basis_permits_a_threshold_of_one(self):
        breaker = MarginUtilizationBreaker(
            warning_threshold=0.90,
            hard_stop_threshold=1.0,
            basis=MarginBasis.INITIAL,
            max_data_age_seconds=30.0,
        )
        self.assertEqual(breaker.hard_stop_threshold, 1.0)

    def test_re_arm_threshold_above_hard_stop_is_rejected(self):
        with self.assertRaises(MarginDataError):
            MarginUtilizationBreaker(
                warning_threshold=0.60,
                hard_stop_threshold=0.80,
                re_arm_threshold=0.90,
            )

    def test_re_arm_threshold_equal_to_hard_stop_is_rejected(self):
        """
        Equality permits re-arming at exactly the trip level: the re-arm is
        granted and the very next evaluation halts again.
        """
        with self.assertRaises(MarginDataError):
            MarginUtilizationBreaker(
                warning_threshold=0.60,
                hard_stop_threshold=0.80,
                re_arm_threshold=0.80,
            )

    def test_non_enum_basis_is_rejected(self):
        with self.assertRaises(MarginDataError):
            MarginUtilizationBreaker(basis="MAINTENANCE")

    def test_non_positive_max_data_age_is_rejected(self):
        with self.assertRaises(MarginDataError):
            MarginUtilizationBreaker(max_data_age_seconds=0)


class TestStaleness(unittest.TestCase):
    def setUp(self):
        self.breaker = _breaker(max_data_age_seconds=30.0)

    def test_fresh_snapshot_is_graded(self):
        result = self.breaker.check_order(
            50_000, 100_000, as_of=NOW - timedelta(seconds=5), now=NOW
        )
        self.assertTrue(result.approved)
        self.assertAlmostEqual(result.margin_state.data_age_seconds, 5.0)

    def test_stale_snapshot_vetoes_the_order(self):
        result = self.breaker.check_order(
            50_000, 100_000, as_of=NOW - timedelta(seconds=31), now=NOW
        )
        self.assertFalse(result.approved)
        self.assertTrue(result.is_data_error)

    def test_missing_timestamp_vetoes_when_freshness_is_enforced(self):
        result = self.breaker.check_order(50_000, 100_000, now=NOW)
        self.assertFalse(result.approved)
        self.assertTrue(result.is_data_error)

    def test_naive_timestamp_is_rejected(self):
        result = self.breaker.check_order(
            50_000, 100_000, as_of=datetime(2026, 8, 25, 13, 30, 0), now=NOW
        )
        self.assertFalse(result.approved)
        self.assertTrue(result.is_data_error)

    def test_future_timestamp_is_rejected(self):
        result = self.breaker.check_order(
            50_000, 100_000, as_of=NOW + timedelta(seconds=5), now=NOW
        )
        self.assertFalse(result.approved)
        self.assertTrue(result.is_data_error)

    def test_non_utc_timezone_is_converted_not_rejected(self):
        eastern = timezone(timedelta(hours=-4))
        as_of = NOW.astimezone(eastern) - timedelta(seconds=5)
        result = self.breaker.check_order(50_000, 100_000, as_of=as_of, now=NOW)
        self.assertTrue(result.approved)
        self.assertAlmostEqual(result.margin_state.data_age_seconds, 5.0)

    def test_freshness_check_is_optional(self):
        breaker = MarginUtilizationBreaker(max_data_age_seconds=None)
        state = breaker.evaluate_margin(50_000, 100_000)
        self.assertEqual(state.status, MarginStatus.NORMAL)
        self.assertIsNone(state.data_age_seconds)


class TestLatching(unittest.TestCase):
    """The behaviour that distinguishes this skill from a stateless ratio check."""

    def setUp(self):
        self.breaker = _breaker()

    def test_breaker_stays_halted_after_utilization_recovers(self):
        self.breaker.evaluate_margin(90_000, 100_000, as_of=NOW, now=NOW)
        self.assertTrue(self.breaker.is_latched)
        # Utilization now 0.10, comfortably normal - but the latch holds.
        recovered = self.breaker.check_order(10_000, 100_000, as_of=NOW, now=NOW)
        self.assertFalse(recovered.approved)
        self.assertEqual(recovered.margin_state.status, MarginStatus.HARD_STOP)
        self.assertTrue(recovered.margin_state.latched)

    def test_non_latching_mode_recovers_on_its_own(self):
        breaker = _breaker(latching=False)
        breaker.evaluate_margin(90_000, 100_000, as_of=NOW, now=NOW)
        self.assertFalse(breaker.is_latched)
        result = breaker.check_order(10_000, 100_000, as_of=NOW, now=NOW)
        self.assertTrue(result.approved)

    def test_concurrent_evaluation_latches_exactly_once(self):
        breaker = _breaker()
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            breaker.evaluate_margin(90_000, 100_000, as_of=NOW, now=NOW)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertTrue(breaker.is_latched)
        self.assertFalse(
            breaker.check_order(10_000, 100_000, as_of=NOW, now=NOW).approved
        )


class TestRiskReducingCarveOut(unittest.TestCase):
    """
    A breaker that blocks every order while halted also blocks the de-risking it
    is demanding. a naive implementation rejected a margin-releasing order
    whenever the projection remained above the hard stop.
    """

    def setUp(self):
        self.breaker = _breaker()
        self.breaker.evaluate_margin(90_000, 100_000, as_of=NOW, now=NOW)

    def test_margin_releasing_order_is_approved_while_halted(self):
        # 90,000 - 30,000 = 60,000 -> 0.60, still latched but strictly reducing.
        result = self.breaker.check_order(
            90_000, 100_000, additional_margin_required=-30_000, as_of=NOW, now=NOW
        )
        self.assertTrue(result.approved)
        self.assertTrue(result.risk_reducing)

    def test_partial_reduction_that_stays_over_the_limit_is_still_approved(self):
        # 90,000 - 5,000 = 85,000 -> 0.85, still above the 0.80 hard stop, but
        # it moves the account in the right direction and must not be vetoed.
        result = self.breaker.check_order(
            90_000, 100_000, additional_margin_required=-5_000, as_of=NOW, now=NOW
        )
        self.assertTrue(result.approved)
        self.assertTrue(result.risk_reducing)

    def test_margin_neutral_order_stays_blocked(self):
        """Equality is not reduction - a neutral swap must not slip through."""
        result = self.breaker.check_order(
            90_000, 100_000, additional_margin_required=0.0, as_of=NOW, now=NOW
        )
        self.assertFalse(result.approved)
        self.assertFalse(result.risk_reducing)

    def test_margin_increasing_order_stays_blocked(self):
        result = self.breaker.check_order(
            90_000, 100_000, additional_margin_required=1_000, as_of=NOW, now=NOW
        )
        self.assertFalse(result.approved)
        self.assertFalse(result.risk_reducing)

    def test_carve_out_is_not_available_below_the_hard_stop(self):
        """A healthy account's reducing order is a plain approval, not a carve-out."""
        breaker = _breaker()
        result = breaker.check_order(
            50_000, 100_000, additional_margin_required=-10_000, as_of=NOW, now=NOW
        )
        self.assertTrue(result.approved)
        self.assertFalse(result.risk_reducing)


class TestReArm(unittest.TestCase):
    def setUp(self):
        self.breaker = _breaker()
        self.breaker.evaluate_margin(90_000, 100_000, as_of=NOW, now=NOW)

    def test_re_arm_refused_while_still_above_the_re_arm_threshold(self):
        granted = self.breaker.re_arm(
            "alice",
            "reviewed",
            used_margin=90_000,
            account_equity=100_000,
            as_of=NOW,
            now=NOW,
        )
        self.assertFalse(granted)
        self.assertTrue(self.breaker.is_latched)

    def test_re_arm_granted_once_exposure_is_reduced(self):
        granted = self.breaker.re_arm(
            "alice",
            "de-risked to 50% and confirmed with the broker",
            used_margin=50_000,
            account_equity=100_000,
            as_of=NOW,
            now=NOW,
        )
        self.assertTrue(granted)
        self.assertFalse(self.breaker.is_latched)
        self.assertTrue(
            self.breaker.check_order(50_000, 100_000, as_of=NOW, now=NOW).approved
        )

    def test_re_arm_at_exactly_the_threshold_is_granted(self):
        # Default re_arm_threshold equals the 0.60 warning level.
        granted = self.breaker.re_arm(
            "alice",
            "reduced to the re-arm level",
            used_margin=60_000,
            account_equity=100_000,
            as_of=NOW,
            now=NOW,
        )
        self.assertTrue(granted)

    def test_blank_operator_is_refused(self):
        self.assertFalse(
            self.breaker.re_arm(
                "   ",
                "reason",
                used_margin=10_000,
                account_equity=100_000,
                as_of=NOW,
                now=NOW,
            )
        )
        self.assertTrue(self.breaker.is_latched)

    def test_blank_reason_is_refused(self):
        self.assertFalse(
            self.breaker.re_arm(
                "alice",
                "",
                used_margin=10_000,
                account_equity=100_000,
                as_of=NOW,
                now=NOW,
            )
        )
        self.assertTrue(self.breaker.is_latched)

    def test_re_arm_on_unusable_input_is_refused(self):
        self.assertFalse(
            self.breaker.re_arm(
                "alice",
                "reason",
                used_margin=float("nan"),
                account_equity=100_000,
                as_of=NOW,
                now=NOW,
            )
        )
        self.assertTrue(self.breaker.is_latched)

    def test_re_arm_on_stale_input_is_refused(self):
        self.assertFalse(
            self.breaker.re_arm(
                "alice",
                "reason",
                used_margin=10_000,
                account_equity=100_000,
                as_of=NOW - timedelta(seconds=120),
                now=NOW,
            )
        )
        self.assertTrue(self.breaker.is_latched)

    def test_every_attempt_is_recorded_granted_and_refused(self):
        self.breaker.re_arm(
            "", "x", used_margin=10_000, account_equity=100_000, as_of=NOW, now=NOW
        )
        self.breaker.re_arm(
            "alice",
            "still stressed",
            used_margin=90_000,
            account_equity=100_000,
            as_of=NOW,
            now=NOW,
        )
        self.breaker.re_arm(
            "bob",
            "de-risked",
            used_margin=10_000,
            account_equity=100_000,
            as_of=NOW,
            now=NOW,
        )
        log = self.breaker.re_arm_log
        self.assertEqual(len(log), 3)
        self.assertEqual([a.granted for a in log], [False, False, True])
        self.assertEqual(log[-1].operator, "bob")
        self.assertEqual(log[-1].timestamp, NOW)

    def test_re_trips_after_re_arm_if_utilization_climbs_again(self):
        self.breaker.re_arm(
            "alice",
            "de-risked",
            used_margin=10_000,
            account_equity=100_000,
            as_of=NOW,
            now=NOW,
        )
        result = self.breaker.check_order(85_000, 100_000, as_of=NOW, now=NOW)
        self.assertFalse(result.approved)
        self.assertTrue(self.breaker.is_latched)


class TestLoggingBehaviour(unittest.TestCase):
    def test_hard_stop_is_logged_once_per_transition_not_per_poll(self):
        breaker = _breaker()
        with self.assertLogs("margin_breaker", level="CRITICAL") as captured:
            breaker.evaluate_margin(90_000, 100_000, as_of=NOW, now=NOW)
            for _ in range(5):
                breaker.evaluate_margin(90_000, 100_000, as_of=NOW, now=NOW)
        self.assertEqual(len(captured.records), 1)

    def test_projected_rejection_does_not_log_a_critical(self):
        """A rejected hypothetical must not write a halt into the audit log."""
        breaker = _breaker()
        logger = logging.getLogger("margin_breaker")
        with self.assertLogs(logger, level="DEBUG") as captured:
            logger.debug("probe")
            breaker.check_order(
                70_000, 100_000, additional_margin_required=15_000, as_of=NOW, now=NOW
            )
        self.assertEqual(
            [r for r in captured.records if r.levelno >= logging.CRITICAL], []
        )


if __name__ == "__main__":
    unittest.main()
