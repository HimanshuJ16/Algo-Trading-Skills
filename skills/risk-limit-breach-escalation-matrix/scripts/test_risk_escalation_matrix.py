"""
Unit tests for the risk-limit breach escalation matrix.

Expected tier values are derived by hand from the default ladder
(1.0 INFO/WARN, 1.2 AMBER/REDUCE, 1.5 RED/HALT, 2.0 CRITICAL/FLATTEN) rather
than by re-running the engine's own arithmetic. The ``TestRegressions`` class
pins the specific defects this skill was corrected for: each of those tests
fails against the previous implementation.
"""
import logging
import unittest

from risk_escalation_matrix import (
    ACTION_ORDER, DEFAULT_POLICIES, BreachEvent, EscalationDecision,
    EscalationPolicy, InvalidBreachError, InvalidPolicyError, LimitDirection,
    NotificationChannel, ResponseAction, RiskEscalationMatrix, SeverityLevel,
)

TS = "2026-08-05T10:00:00Z"

# The engine logs a CRITICAL line for every FLATTEN decision. Without a handler
# these reach the root logger's last-resort stderr writer and bury the test
# output, so silence the module logger for the duration of the suite.
logging.getLogger("risk_escalation_matrix").addHandler(logging.NullHandler())
logging.getLogger("risk_escalation_matrix").propagate = False


def event(**overrides) -> BreachEvent:
    """A well-formed breach event with per-test overrides."""
    fields = dict(event_id="EVT", metric_name="DAILY_DRAWDOWN", strategy_id="S1",
                  current_value=1.0, limit_value=1.0, timestamp_iso=TS)
    fields.update(overrides)
    return BreachEvent(**fields)


class TestRiskEscalationMatrixLegacy(unittest.TestCase):
    """The original legacy-API contract, unchanged."""

    def test_warn(self):
        res = RiskEscalationMatrix().evaluate(110, 100)
        self.assertEqual(res.action, ResponseAction.WARN)
        self.assertEqual(res.level, 1.0)

    def test_reduce(self):
        self.assertEqual(RiskEscalationMatrix().evaluate(125, 100).action,
                         ResponseAction.REDUCE)

    def test_halt(self):
        self.assertEqual(RiskEscalationMatrix().evaluate(160, 100).action,
                         ResponseAction.HALT)

    def test_flatten(self):
        res = RiskEscalationMatrix().evaluate(210, 100)
        self.assertEqual(res.action, ResponseAction.FLATTEN)
        self.assertEqual(res.level, 2.0)

    def test_no_breach(self):
        self.assertEqual(RiskEscalationMatrix().evaluate(90, 100).action,
                         ResponseAction.NONE)

    def test_exact_threshold_is_a_breach(self):
        """100.0/100.0 == 1.0 must trip WARN, not fall through as 'no breach'."""
        self.assertEqual(RiskEscalationMatrix().evaluate(100, 100).action,
                         ResponseAction.WARN)


class TestRiskEscalationMatrixAdvanced(unittest.TestCase):
    """The original structured-event contract, unchanged."""

    def setUp(self):
        self.matrix = RiskEscalationMatrix()

    def test_process_breach_event_critical(self):
        decision = self.matrix.process_breach_event(BreachEvent(
            event_id="EVT_1001", metric_name="DAILY_DRAWDOWN",
            strategy_id="STAT_ARB_01", current_value=25000.0,
            limit_value=10000.0, timestamp_iso=TS))          # 2.5x limit
        self.assertEqual(decision.severity, SeverityLevel.CRITICAL)
        self.assertEqual(decision.action, ResponseAction.FLATTEN)
        self.assertIn(NotificationChannel.PAGERDUTY, decision.notification_channels)
        self.assertEqual(decision.ack_deadline_seconds, 60)

    def test_sustained_breach_auto_escalation(self):
        decision = self.matrix.process_breach_event(BreachEvent(
            event_id="EVT_1002", metric_name="POSITION_CAP",
            strategy_id="MOMENTUM_02", current_value=105.0, limit_value=100.0,
            timestamp_iso=TS, duration_seconds=360.0))       # 1.05x, sustained
        self.assertTrue(decision.is_sustained_breach)
        self.assertEqual(decision.action, ResponseAction.REDUCE)
        self.assertEqual(decision.severity, SeverityLevel.AMBER)

    def test_invalid_limit_raises_error(self):
        bad = BreachEvent(event_id="EVT_1003", metric_name="LEVERAGE",
                          strategy_id="HFT_01", current_value=5.0,
                          limit_value=0.0, timestamp_iso=TS)
        with self.assertRaises(InvalidBreachError):
            self.matrix.process_breach_event(bad)

    def test_audit_trail_recorded(self):
        self.matrix.process_breach_event(
            BreachEvent("E1", "METRIC1", "S1", 130.0, 100.0, TS))
        trail = self.matrix.get_audit_trail()
        self.assertEqual(len(trail), 1)
        self.assertEqual(trail[0].event_id, "E1")


class TestBreachRatio(unittest.TestCase):
    """Ratio arithmetic, both directions, computed by hand."""

    def test_upper_ratio(self):
        self.assertAlmostEqual(
            RiskEscalationMatrix.compute_ratio(25000.0, 10000.0), 2.5)

    def test_lower_ratio_on_the_floor_is_one(self):
        # 1 + (50000 - 50000)/50000 = 1.0
        self.assertAlmostEqual(
            RiskEscalationMatrix.compute_ratio(
                50000.0, 50000.0, LimitDirection.LOWER), 1.0)

    def test_lower_ratio_shortfall(self):
        # 1 + (50000 - 40000)/50000 = 1.2, exactly the AMBER tier.
        self.assertAlmostEqual(
            RiskEscalationMatrix.compute_ratio(
                40000.0, 50000.0, LimitDirection.LOWER), 1.2)

    def test_lower_ratio_exhausted_buffer_is_two(self):
        # 1 + (50000 - 0)/50000 = 2.0, the CRITICAL tier.
        self.assertAlmostEqual(
            RiskEscalationMatrix.compute_ratio(
                0.0, 50000.0, LimitDirection.LOWER), 2.0)

    def test_lower_ratio_is_floored_at_zero(self):
        # 1 + (50000 - 125000)/50000 = -0.5, reported as 0.0.
        self.assertEqual(
            RiskEscalationMatrix.compute_ratio(
                125000.0, 50000.0, LimitDirection.LOWER), 0.0)

    def test_lower_direction_healthy_buffer_is_no_breach(self):
        decision = RiskEscalationMatrix().process_breach_event(event(
            metric_name="FREE_MARGIN", current_value=60000.0,
            limit_value=50000.0, direction=LimitDirection.LOWER))
        self.assertEqual(decision.action, ResponseAction.NONE)
        self.assertAlmostEqual(decision.ratio, 0.8)

    def test_lower_direction_exhausted_buffer_flattens(self):
        decision = RiskEscalationMatrix().process_breach_event(event(
            metric_name="FREE_MARGIN", current_value=0.0, limit_value=50000.0,
            direction=LimitDirection.LOWER))
        self.assertEqual(decision.action, ResponseAction.FLATTEN)
        self.assertEqual(decision.direction, LimitDirection.LOWER)


class TestTierMatching(unittest.TestCase):
    """Threshold selection, including exact-boundary behaviour."""

    def setUp(self):
        self.matrix = RiskEscalationMatrix()

    def _tier(self, ratio: float) -> EscalationDecision:
        return self.matrix.process_breach_event(
            event(event_id=f"E{ratio}", current_value=ratio, limit_value=1.0))

    def test_below_lowest_tier_is_no_breach(self):
        decision = self._tier(0.99)
        self.assertEqual(decision.action, ResponseAction.NONE)
        self.assertIsNone(decision.matched_threshold)
        self.assertEqual(decision.notification_channels, ())

    def test_each_threshold_is_inclusive(self):
        for ratio, action, severity, ack in ((1.0, ResponseAction.WARN, SeverityLevel.INFO, 900),
                                             (1.2, ResponseAction.REDUCE, SeverityLevel.AMBER, 300),
                                             (1.5, ResponseAction.HALT, SeverityLevel.RED, 120),
                                             (2.0, ResponseAction.FLATTEN, SeverityLevel.CRITICAL, 60)):
            with self.subTest(ratio=ratio):
                decision = self._tier(ratio)
                self.assertEqual(decision.action, action)
                self.assertEqual(decision.severity, severity)
                self.assertEqual(decision.ack_deadline_seconds, ack)
                self.assertEqual(decision.matched_threshold, ratio)

    def test_just_below_a_threshold_stays_on_the_lower_tier(self):
        decision = self._tier(1.4999)
        self.assertEqual(decision.action, ResponseAction.REDUCE)
        self.assertEqual(decision.matched_threshold, 1.2)

    def test_critical_routes_to_pagerduty_and_compliance(self):
        self.assertEqual(
            self._tier(2.5).notification_channels,
            (NotificationChannel.PAGERDUTY, NotificationChannel.COMPLIANCE_TICKET))


class TestDurationEscalation(unittest.TestCase):
    """Promotion is one full rung: severity, action, channels and ack together."""

    def setUp(self):
        self.matrix = RiskEscalationMatrix()

    def _sustained(self, ratio: float, duration: float) -> EscalationDecision:
        return self.matrix.process_breach_event(event(
            event_id=f"E{ratio}-{duration}", current_value=ratio,
            limit_value=1.0, duration_seconds=duration))

    def test_boundary_at_exactly_300s(self):
        self.assertTrue(self._sustained(1.05, 300.0).is_sustained_breach)

    def test_just_under_300s_is_not_sustained(self):
        decision = self._sustained(1.05, 299.999)
        self.assertFalse(decision.is_sustained_breach)
        self.assertEqual(decision.action, ResponseAction.WARN)

    def test_amber_promotion_carries_channels_and_ack(self):
        """1.3x sustained -> the full RED rung, not RED action on AMBER routing."""
        decision = self._sustained(1.3, 600.0)
        self.assertEqual(decision.action, ResponseAction.HALT)
        self.assertEqual(decision.severity, SeverityLevel.RED)
        self.assertEqual(decision.ack_deadline_seconds, 120)
        self.assertIn(NotificationChannel.PAGERDUTY, decision.notification_channels)
        self.assertTrue(decision.is_duration_escalated)

    def test_red_promotes_to_critical(self):
        decision = self._sustained(1.6, 14400.0)
        self.assertEqual(decision.action, ResponseAction.FLATTEN)
        self.assertEqual(decision.severity, SeverityLevel.CRITICAL)

    def test_top_tier_cannot_promote_further(self):
        decision = self._sustained(2.5, 86400.0)
        self.assertEqual(decision.action, ResponseAction.FLATTEN)
        self.assertFalse(decision.is_duration_escalated)
        self.assertTrue(decision.is_sustained_breach)
        self.assertIn("no higher rung exists", decision.audit_notes)

    def test_promotion_is_one_rung_not_straight_to_the_top(self):
        self.assertEqual(self._sustained(1.05, 999999.0).action, ResponseAction.REDUCE)

    def test_configurable_sustained_window(self):
        matrix = RiskEscalationMatrix(sustained_breach_seconds=60.0)
        decision = matrix.process_breach_event(
            event(current_value=1.05, duration_seconds=90.0))
        self.assertTrue(decision.is_sustained_breach)

    def test_custom_ladder_actions_still_escalate(self):
        """A ladder built from THROTTLE/HALT must promote by position, not name."""
        ladder = [
            EscalationPolicy(1.0, SeverityLevel.INFO, ResponseAction.THROTTLE,
                             [NotificationChannel.SLACK], 900),
            EscalationPolicy(1.5, SeverityLevel.RED, ResponseAction.HALT,
                             [NotificationChannel.PAGERDUTY], 60),
        ]
        decision = RiskEscalationMatrix(policies=ladder).process_breach_event(
            event(current_value=1.1, duration_seconds=600.0))
        self.assertEqual(decision.action, ResponseAction.HALT)
        self.assertEqual(decision.severity, SeverityLevel.RED)
        self.assertTrue(decision.is_duration_escalated)


class TestLatching(unittest.TestCase):
    """An escalation ratchets until it is deliberately reset."""

    def setUp(self):
        self.matrix = RiskEscalationMatrix()

    def test_oscillation_cannot_cancel_a_flatten(self):
        self.matrix.process_breach_event(event(event_id="A", current_value=2.5))
        decision = self.matrix.process_breach_event(
            event(event_id="B", current_value=1.05))
        self.assertEqual(decision.action, ResponseAction.FLATTEN)
        self.assertTrue(decision.is_latched)
        self.assertEqual(decision.ack_deadline_seconds, 60)

    def test_latch_still_allows_further_escalation(self):
        self.matrix.process_breach_event(event(event_id="A", current_value=1.3))
        decision = self.matrix.process_breach_event(
            event(event_id="B", current_value=2.5))
        self.assertEqual(decision.action, ResponseAction.FLATTEN)
        self.assertFalse(decision.is_latched)

    def test_latch_is_per_strategy_and_metric(self):
        self.matrix.process_breach_event(
            event(event_id="A", strategy_id="S1", current_value=2.5))
        other = self.matrix.process_breach_event(
            event(event_id="B", strategy_id="S2", current_value=1.05))
        self.assertEqual(other.action, ResponseAction.WARN)

    def test_reset_incident_allows_de_escalation(self):
        self.matrix.process_breach_event(event(event_id="A", current_value=2.5))
        self.assertTrue(self.matrix.reset_incident("S1", "DAILY_DRAWDOWN"))
        decision = self.matrix.process_breach_event(
            event(event_id="B", current_value=1.05))
        self.assertEqual(decision.action, ResponseAction.WARN)
        self.assertFalse(decision.is_latched)

    def test_reset_of_unknown_incident_reports_false(self):
        self.assertFalse(self.matrix.reset_incident("NOPE", "NOPE"))

    def test_latching_can_be_disabled(self):
        matrix = RiskEscalationMatrix(latch_escalations=False)
        matrix.process_breach_event(event(event_id="A", current_value=2.5))
        decision = matrix.process_breach_event(event(event_id="B", current_value=1.05))
        self.assertEqual(decision.action, ResponseAction.WARN)
        self.assertEqual(matrix.get_active_incidents(), {})

    def test_active_incidents_snapshot_is_a_copy(self):
        self.matrix.process_breach_event(event(event_id="A", current_value=2.5))
        snapshot = self.matrix.get_active_incidents()
        snapshot.clear()
        self.assertEqual(len(self.matrix.get_active_incidents()), 1)


class TestReplaySafety(unittest.TestCase):
    """A retried alert must not re-fire a destructive action."""

    def setUp(self):
        self.matrix = RiskEscalationMatrix()

    def test_identical_resubmission_is_suppressed(self):
        breach = event(event_id="DUP", current_value=2.5)
        first = self.matrix.process_breach_event(breach)
        second = self.matrix.process_breach_event(breach)
        self.assertFalse(first.is_replay)
        self.assertTrue(second.is_replay)
        self.assertEqual(first.action, second.action)
        self.assertEqual(len(self.matrix.get_audit_trail()), 1)

    def test_same_id_with_new_duration_is_re_evaluated(self):
        """An ongoing breach reported again with a longer duration must escalate."""
        self.matrix.process_breach_event(
            event(event_id="ONGOING", current_value=1.05, duration_seconds=10.0))
        later = self.matrix.process_breach_event(
            event(event_id="ONGOING", current_value=1.05, duration_seconds=600.0))
        self.assertFalse(later.is_replay)
        self.assertEqual(later.action, ResponseAction.REDUCE)
        self.assertEqual(len(self.matrix.get_audit_trail()), 2)

    def test_replay_cache_can_be_disabled(self):
        matrix = RiskEscalationMatrix(replay_cache_size=0)
        breach = event(event_id="DUP", current_value=2.5)
        matrix.process_breach_event(breach)
        self.assertFalse(matrix.process_breach_event(breach).is_replay)
        self.assertEqual(len(matrix.get_audit_trail()), 2)

    def test_replay_cache_evicts_oldest(self):
        matrix = RiskEscalationMatrix(replay_cache_size=2)
        for index in range(3):
            matrix.process_breach_event(event(event_id=f"E{index}", current_value=2.5))
        # E0 was evicted, so its resubmission is processed as a fresh event.
        self.assertFalse(
            matrix.process_breach_event(event(event_id="E0", current_value=2.5)).is_replay)


class TestInputValidation(unittest.TestCase):
    """Malformed or ambiguous input fails closed, loudly."""

    def setUp(self):
        self.matrix = RiskEscalationMatrix()

    def _reject(self, **overrides):
        with self.assertRaises(InvalidBreachError):
            self.matrix.process_breach_event(event(**overrides))

    def test_nan_metric_rejected(self):
        self._reject(current_value=float("nan"))

    def test_infinite_metric_rejected(self):
        self._reject(current_value=float("inf"))

    def test_negative_upper_metric_rejected(self):
        self._reject(current_value=-25000.0, limit_value=10000.0)

    def test_non_positive_limit_rejected(self):
        self._reject(limit_value=0.0)
        self._reject(limit_value=-100.0)

    def test_negative_duration_rejected(self):
        self._reject(duration_seconds=-1.0)

    def test_blank_identifiers_rejected(self):
        self._reject(event_id="   ")
        self._reject(metric_name="")
        self._reject(strategy_id="")

    def test_boolean_metric_rejected(self):
        self._reject(current_value=True)

    def test_non_numeric_metric_rejected(self):
        self._reject(current_value="not-a-number")

    def test_numeric_string_from_json_is_accepted(self):
        decision = self.matrix.process_breach_event(
            event(current_value="2.5", limit_value="1.0"))
        self.assertEqual(decision.action, ResponseAction.FLATTEN)

    def test_naive_timestamp_rejected(self):
        self._reject(timestamp_iso="2026-08-05T10:00:00")

    def test_unparseable_timestamp_rejected(self):
        self._reject(timestamp_iso="last Tuesday")

    def test_timestamp_normalised_to_utc(self):
        decision = self.matrix.process_breach_event(
            event(current_value=2.5, timestamp_iso="2026-08-05T15:30:00+05:30"))
        self.assertEqual(decision.timestamp_iso, "2026-08-05T10:00:00Z")

    def test_bogus_direction_raises_the_documented_exception(self):
        """
        Callers are told to wrap the engine in ``except EscalationMatrixError``.
        A typo'd direction must not escape that handler as a bare ValueError.
        """
        self._reject(direction="SIDEWAYS")

    def test_direction_accepts_its_string_value(self):
        decision = self.matrix.process_breach_event(event(
            current_value=0.0, limit_value=50000.0, direction="LOWER"))
        self.assertEqual(decision.action, ResponseAction.FLATTEN)

    def test_compute_ratio_rejects_a_bogus_direction(self):
        with self.assertRaises(InvalidBreachError):
            RiskEscalationMatrix.compute_ratio(1.0, 1.0, "NOPE")

    def test_legacy_evaluate_rejects_non_positive_limit(self):
        with self.assertRaises(InvalidBreachError):
            self.matrix.evaluate(1e9, 0.0)

    def test_legacy_evaluate_rejects_nan(self):
        with self.assertRaises(InvalidBreachError):
            self.matrix.evaluate(float("nan"), 100.0)


class TestLadderValidation(unittest.TestCase):
    """A ladder that is not a ladder is rejected at construction."""

    def test_empty_policy_list_rejected(self):
        with self.assertRaises(InvalidPolicyError):
            RiskEscalationMatrix(policies=[])

    def test_none_policies_uses_defaults(self):
        self.assertEqual(RiskEscalationMatrix(policies=None).policies, DEFAULT_POLICIES)

    def test_duplicate_thresholds_rejected(self):
        with self.assertRaises(InvalidPolicyError):
            RiskEscalationMatrix(policies=[
                EscalationPolicy(1.0, SeverityLevel.INFO, ResponseAction.WARN,
                                 [NotificationChannel.SLACK], 900),
                EscalationPolicy(1.0, SeverityLevel.RED, ResponseAction.HALT,
                                 [NotificationChannel.PAGERDUTY], 60)])

    def test_weakening_action_rejected(self):
        with self.assertRaises(InvalidPolicyError):
            RiskEscalationMatrix(policies=[
                EscalationPolicy(1.0, SeverityLevel.INFO, ResponseAction.FLATTEN,
                                 [NotificationChannel.PAGERDUTY], 60),
                EscalationPolicy(2.0, SeverityLevel.CRITICAL, ResponseAction.WARN,
                                 [NotificationChannel.SLACK], 900)])

    def test_decreasing_severity_rejected(self):
        with self.assertRaises(InvalidPolicyError):
            RiskEscalationMatrix(policies=[
                EscalationPolicy(1.0, SeverityLevel.CRITICAL, ResponseAction.WARN,
                                 [NotificationChannel.SLACK], 900),
                EscalationPolicy(2.0, SeverityLevel.INFO, ResponseAction.FLATTEN,
                                 [NotificationChannel.PAGERDUTY], 60)])

    def test_unrouted_tier_rejected(self):
        with self.assertRaises(InvalidPolicyError):
            RiskEscalationMatrix(policies=[
                EscalationPolicy(1.0, SeverityLevel.INFO, ResponseAction.WARN, [], 900)])

    def test_non_positive_ack_timeout_rejected(self):
        with self.assertRaises(InvalidPolicyError):
            RiskEscalationMatrix(policies=[
                EscalationPolicy(1.0, SeverityLevel.INFO, ResponseAction.WARN,
                                 [NotificationChannel.SLACK], 0)])

    def test_wrong_enum_types_raise_the_documented_exception(self):
        """Plain strings must not reach the ACTION_ORDER lookup as a KeyError."""
        for bad in (
            EscalationPolicy(1.0, "RED", ResponseAction.HALT,
                             [NotificationChannel.SLACK], 60),
            EscalationPolicy(1.0, SeverityLevel.RED, "HALT",
                             [NotificationChannel.SLACK], 60),
            EscalationPolicy(1.0, SeverityLevel.RED, ResponseAction.HALT,
                             ["SLACK"], 60),
        ):
            with self.subTest(policy=bad):
                with self.assertRaises(InvalidPolicyError):
                    RiskEscalationMatrix(policies=[bad])

    def test_unordered_ladder_is_sorted_not_rejected(self):
        matrix = RiskEscalationMatrix(policies=list(reversed(DEFAULT_POLICIES)))
        self.assertEqual([p.ratio_threshold for p in matrix.policies],
                         [1.0, 1.2, 1.5, 2.0])

    def test_non_ascending_legacy_levels_rejected(self):
        with self.assertRaises(InvalidPolicyError):
            RiskEscalationMatrix(1.0, 1.0, 1.0, 1.0)
        with self.assertRaises(InvalidPolicyError):
            RiskEscalationMatrix(warn_lvl=2.0, flatten_lvl=1.0)

    def test_non_positive_legacy_level_rejected(self):
        with self.assertRaises(InvalidPolicyError):
            RiskEscalationMatrix(warn_lvl=0.0)

    def test_sustained_window_must_be_positive(self):
        with self.assertRaises(InvalidPolicyError):
            RiskEscalationMatrix(sustained_breach_seconds=0.0)


class TestAuditTrail(unittest.TestCase):
    """The audit trail must be complete and genuinely immutable."""

    def setUp(self):
        self.matrix = RiskEscalationMatrix()

    def test_decision_rows_are_frozen(self):
        self.matrix.process_breach_event(event(current_value=2.5))
        row = self.matrix.get_audit_trail()[0]
        with self.assertRaises(Exception):
            row.action = ResponseAction.NONE           # type: ignore[misc]
        self.assertEqual(self.matrix.get_audit_trail()[0].action,
                         ResponseAction.FLATTEN)

    def test_sub_threshold_events_are_recorded(self):
        self.matrix.process_breach_event(event(current_value=0.5))
        trail = self.matrix.get_audit_trail()
        self.assertEqual(len(trail), 1)
        self.assertEqual(trail[0].action, ResponseAction.NONE)
        self.assertIn("NO BREACH", trail[0].audit_notes)

    def test_row_carries_the_inputs_behind_the_verdict(self):
        self.matrix.process_breach_event(
            event(current_value=25000.0, limit_value=10000.0, duration_seconds=42.0))
        row = self.matrix.get_audit_trail()[0]
        self.assertEqual(row.current_value, 25000.0)
        self.assertEqual(row.limit_value, 10000.0)
        self.assertEqual(row.duration_seconds, 42.0)
        self.assertEqual(row.timestamp_iso, TS)
        self.assertAlmostEqual(row.ratio, 2.5)

    def test_trail_preserves_order(self):
        for index, ratio in enumerate((1.05, 1.3, 2.5)):
            self.matrix.process_breach_event(
                event(event_id=f"E{index}", current_value=ratio))
        self.assertEqual([r.event_id for r in self.matrix.get_audit_trail()],
                         ["E0", "E1", "E2"])


class TestRegressions(unittest.TestCase):
    """
    Each test here fails against the pre-2.0.0 implementation. They are the
    reason this skill was revised, not general coverage.
    """

    def setUp(self):
        self.matrix = RiskEscalationMatrix()

    def test_ratio_not_rounded_before_comparison(self):
        """
        1.99996x rounded to 4dp is 2.0000. The old engine compared the rounded
        value and force-liquidated at a threshold the metric never reached.
        """
        decision = self.matrix.process_breach_event(
            event(current_value=1.99996, limit_value=1.0))
        self.assertEqual(decision.action, ResponseAction.HALT)
        self.assertEqual(decision.matched_threshold, 1.5)

    def test_sustained_red_breach_escalates_to_critical(self):
        """The old chain stopped at HALT: a 1.6x breach held for 4h never moved."""
        decision = self.matrix.process_breach_event(
            event(current_value=1.6, limit_value=1.0, duration_seconds=14400.0))
        self.assertEqual(decision.action, ResponseAction.FLATTEN)

    def test_promotion_upgrades_notification_routing(self):
        """
        The old engine raised a sustained AMBER breach to a RED/HALT action while
        leaving it on Slack + e-mail with the 300s AMBER ack deadline -- exactly
        the 'unrouted critical notification' the skill warns against.
        """
        decision = self.matrix.process_breach_event(
            event(current_value=1.3, limit_value=1.0, duration_seconds=600.0))
        self.assertEqual(decision.severity, SeverityLevel.RED)
        self.assertIn(NotificationChannel.PAGERDUTY, decision.notification_channels)
        self.assertNotIn(NotificationChannel.EMAIL, decision.notification_channels)
        self.assertEqual(decision.ack_deadline_seconds, 120)

    def test_signed_drawdown_is_not_silently_a_non_breach(self):
        """-25000 against a 10000 limit gave ratio -2.5 and action NONE."""
        with self.assertRaises(InvalidBreachError):
            self.matrix.process_breach_event(
                event(current_value=-25000.0, limit_value=10000.0))

    def test_nan_metric_is_not_silently_a_non_breach(self):
        """NaN compares False against every threshold, so the old engine said NONE."""
        with self.assertRaises(InvalidBreachError):
            self.matrix.process_breach_event(event(current_value=float("nan")))

    def test_empty_policy_list_does_not_silently_restore_defaults(self):
        """``policies or DEFAULT_POLICIES`` swallowed a deliberately empty ladder."""
        with self.assertRaises(InvalidPolicyError):
            RiskEscalationMatrix(policies=[])

    def test_legacy_evaluate_does_not_fail_open_on_zero_limit(self):
        """evaluate(1e9, 0) previously returned NONE: fail-open on a risk control."""
        with self.assertRaises(InvalidBreachError):
            self.matrix.evaluate(1e9, 0.0)

    def test_default_ladder_cannot_be_mutated_through_an_instance(self):
        """DEFAULT_POLICIES was a module-level list of mutable dataclasses."""
        with self.assertRaises(Exception):
            self.matrix.policies[0].action = ResponseAction.NONE  # type: ignore[misc]
        self.assertEqual(DEFAULT_POLICIES[0].action, ResponseAction.WARN)

    def test_duplicate_event_does_not_re_fire_flatten(self):
        """The same alert delivered twice produced two FLATTEN decisions."""
        breach = event(event_id="RETRY", current_value=2.5)
        self.matrix.process_breach_event(breach)
        self.matrix.process_breach_event(breach)
        flattens = [r for r in self.matrix.get_audit_trail()
                    if r.action == ResponseAction.FLATTEN]
        self.assertEqual(len(flattens), 1)

    def test_audit_row_records_the_breach_timestamp(self):
        """timestamp_iso was accepted on the event and then dropped entirely."""
        self.matrix.process_breach_event(event(current_value=2.5))
        self.assertEqual(self.matrix.get_audit_trail()[0].timestamp_iso, TS)


class TestActionOrdering(unittest.TestCase):
    """The ordering table the latch and ladder validation both depend on."""

    def test_every_action_is_ranked(self):
        self.assertEqual(set(ACTION_ORDER), set(ResponseAction))

    def test_ordering_is_strictly_ascending_by_severity_of_response(self):
        ranks = [ACTION_ORDER[a] for a in (
            ResponseAction.NONE, ResponseAction.WARN, ResponseAction.THROTTLE,
            ResponseAction.REDUCE, ResponseAction.HALT, ResponseAction.FLATTEN,
            ResponseAction.GLOBAL_KILL_SWITCH)]
        self.assertEqual(ranks, sorted(ranks))
        self.assertEqual(len(set(ranks)), len(ranks))


if __name__ == '__main__':
    unittest.main()
