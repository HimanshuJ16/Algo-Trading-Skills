import logging
import unittest

from training_freshness_sla import (
    ACTION_ALERT_ONLY,
    ACTION_ESCALATE_BACKFILL_URGENT,
    ACTION_HALT_MODEL_RETRAINING,
    ACTION_PROCEED_NORMAL,
    ACTION_REDUCE_CONFIDENCE,
    ACTION_TRIGGER_BACKFILL_ALERT,
    BASIS_INGESTION_TIME,
    STATUS_BREACH_CRITICAL,
    STATUS_COMPLIANT,
    STATUS_WARNING_NEAR_LIMIT,
    STATUS_WARNING_OFF_TARGET,
    DatasetMetadataPayload,
    FreshnessSlaConfig,
    TrainingFreshnessSlaEngine,
)

HOUR = 3600.0
DATASET = "EQUITY_DAILY_BARS"


def setUpModule():
    # The engine logs every verdict; silence it so test output stays readable.
    logging.getLogger("training_freshness_sla").addHandler(logging.NullHandler())
    logging.getLogger("training_freshness_sla").propagate = False


class FreshnessSlaTestBase(unittest.TestCase):
    """Shared fixtures. Ladder is target=24h, warning=36h, breach=48h."""

    def setUp(self):
        self.engine = TrainingFreshnessSlaEngine()
        self.now = 1750000000.0  # Fixed epoch anchor; the engine reads no clock.

    def config(self, **overrides):
        kwargs = dict(
            model_id="ALPHA_MODEL_01",
            dataset_name=DATASET,
            target_sla_hours=24.0,
            warning_sla_hours=36.0,
            breach_sla_hours=48.0,
            action_on_breach=ACTION_HALT_MODEL_RETRAINING,
        )
        kwargs.update(overrides)
        return FreshnessSlaConfig(**kwargs)

    def metadata(self, lag_seconds, **overrides):
        kwargs = dict(
            dataset_name=DATASET,
            latest_record_timestamp_epoch=self.now - lag_seconds,
            current_system_timestamp_epoch=self.now,
            total_record_count=10000,
        )
        kwargs.update(overrides)
        return DatasetMetadataPayload(**kwargs)

    def evaluate(self, lag_seconds, config_kwargs=None, **meta_overrides):
        cfg = self.config(**(config_kwargs or {}))
        meta = self.metadata(lag_seconds, **meta_overrides)
        return self.engine.evaluate_training_freshness_sla(cfg, meta)


class TestSlaLadder(FreshnessSlaTestBase):
    """Each configured threshold must map to a distinct, reachable rung."""

    def test_fresh_dataset_is_compliant(self):
        report = self.evaluate(5 * HOUR)
        self.assertEqual(report.status, STATUS_COMPLIANT)
        self.assertEqual(report.recommended_governance_action, ACTION_PROCEED_NORMAL)
        self.assertTrue(report.is_sla_compliant)
        self.assertFalse(report.is_sla_breached)
        self.assertEqual(report.data_lag_hours, 5.0)
        self.assertEqual(report.effective_lag_hours, 5.0)

    def test_lag_between_target_and_warning_is_off_target(self):
        report = self.evaluate(30 * HOUR)
        self.assertEqual(report.status, STATUS_WARNING_OFF_TARGET)
        self.assertEqual(
            report.recommended_governance_action, ACTION_TRIGGER_BACKFILL_ALERT)
        self.assertFalse(report.is_sla_compliant)
        self.assertFalse(report.is_sla_breached)

    def test_lag_between_warning_and_breach_is_near_limit(self):
        # Regression: warning_sla_hours was never compared against anything,
        # so 40h and 25h both reported SLA_WARNING_NEAR_LIMIT with the same
        # backfill action and is_sla_compliant=True, one hour from a halt.
        report = self.evaluate(40 * HOUR)
        self.assertEqual(report.status, STATUS_WARNING_NEAR_LIMIT)
        self.assertEqual(
            report.recommended_governance_action, ACTION_ESCALATE_BACKFILL_URGENT)
        self.assertFalse(report.is_sla_compliant)
        self.assertFalse(report.is_sla_breached)

    def test_lag_past_breach_halts_retraining(self):
        report = self.evaluate(52 * HOUR)
        self.assertEqual(report.status, STATUS_BREACH_CRITICAL)
        self.assertEqual(
            report.recommended_governance_action, ACTION_HALT_MODEL_RETRAINING)
        self.assertFalse(report.is_sla_compliant)
        self.assertTrue(report.is_sla_breached)
        self.assertEqual(report.data_lag_hours, 52.0)

    def test_off_target_and_near_limit_are_distinguishable(self):
        off_target = self.evaluate(25 * HOUR)
        near_limit = self.evaluate(47 * HOUR)
        self.assertNotEqual(off_target.status, near_limit.status)
        self.assertNotEqual(
            off_target.recommended_governance_action,
            near_limit.recommended_governance_action,
        )

    def test_configured_breach_action_is_returned_verbatim(self):
        for action in (ACTION_REDUCE_CONFIDENCE, ACTION_ALERT_ONLY):
            with self.subTest(action=action):
                report = self.evaluate(52 * HOUR, {"action_on_breach": action})
                self.assertEqual(report.recommended_governance_action, action)
                self.assertTrue(report.is_sla_breached)


class TestThresholdBoundaries(FreshnessSlaTestBase):
    """Thresholds are inclusive ceilings: `lag <= threshold` stays on the rung."""

    def test_lag_exactly_at_target_is_compliant(self):
        self.assertEqual(self.evaluate(24 * HOUR).status, STATUS_COMPLIANT)

    def test_lag_one_second_past_target_is_off_target(self):
        self.assertEqual(
            self.evaluate(24 * HOUR + 1.0).status, STATUS_WARNING_OFF_TARGET)

    def test_lag_exactly_at_warning_is_off_target(self):
        self.assertEqual(self.evaluate(36 * HOUR).status, STATUS_WARNING_OFF_TARGET)

    def test_lag_one_second_past_warning_is_near_limit(self):
        self.assertEqual(
            self.evaluate(36 * HOUR + 1.0).status, STATUS_WARNING_NEAR_LIMIT)

    def test_lag_exactly_at_breach_is_not_a_breach(self):
        report = self.evaluate(48 * HOUR)
        self.assertEqual(report.status, STATUS_WARNING_NEAR_LIMIT)
        self.assertFalse(report.is_sla_breached)

    def test_ten_seconds_past_breach_is_a_breach(self):
        # Regression: classification compared round(lag, 2), so a 10s overshoot
        # (48.00278h -> 48.0) was reported as a warning, not a breach.
        report = self.evaluate(48 * HOUR + 10.0)
        self.assertEqual(report.status, STATUS_BREACH_CRITICAL)
        self.assertTrue(report.is_sla_breached)
        self.assertEqual(report.data_lag_hours, 48.0)  # presentation still rounds

    def test_zero_lag_is_compliant(self):
        self.assertEqual(self.evaluate(0.0).status, STATUS_COMPLIANT)


class TestNonFiniteInputFailsClosed(FreshnessSlaTestBase):
    """Every `>` comparison against NaN is False, so NaN must never reach the ladder."""

    def test_nan_record_timestamp_raises_instead_of_reporting_compliant(self):
        # Regression: NaN produced a NaN lag, fell through every comparison and
        # returned SLA_COMPLIANT / PROCEED_NORMAL -- the gate failing open.
        with self.assertRaises(ValueError):
            self.evaluate(0.0, latest_record_timestamp_epoch=float("nan"))

    def test_nan_system_timestamp_raises(self):
        with self.assertRaises(ValueError):
            self.evaluate(0.0, current_system_timestamp_epoch=float("nan"))

    def test_infinite_timestamps_raise(self):
        with self.assertRaises(ValueError):
            self.evaluate(0.0, latest_record_timestamp_epoch=float("-inf"))
        with self.assertRaises(ValueError):
            self.evaluate(0.0, current_system_timestamp_epoch=float("inf"))

    def test_nan_threshold_raises(self):
        with self.assertRaises(ValueError):
            self.evaluate(52 * HOUR, {"breach_sla_hours": float("nan")})

    def test_nan_calendar_exclusion_raises(self):
        with self.assertRaises(ValueError):
            self.evaluate(52 * HOUR, calendar_excluded_hours=float("nan"))

    def test_non_numeric_timestamp_raises_type_error(self):
        with self.assertRaises(TypeError):
            self.evaluate(0.0, latest_record_timestamp_epoch="1750000000")


class TestConfigValidation(FreshnessSlaTestBase):

    def test_inverted_ladder_raises(self):
        with self.assertRaises(ValueError):
            self.evaluate(5 * HOUR, {"target_sla_hours": 40.0})  # target > warning
        with self.assertRaises(ValueError):
            self.evaluate(5 * HOUR, {"warning_sla_hours": 60.0})  # warning > breach

    def test_equal_thresholds_are_allowed(self):
        report = self.evaluate(
            5 * HOUR,
            {"target_sla_hours": 24.0, "warning_sla_hours": 24.0, "breach_sla_hours": 24.0},
        )
        self.assertEqual(report.status, STATUS_COMPLIANT)

    def test_non_positive_target_raises(self):
        with self.assertRaises(ValueError):
            self.evaluate(5 * HOUR, {"target_sla_hours": 0.0})
        with self.assertRaises(ValueError):
            self.evaluate(
                5 * HOUR,
                {"target_sla_hours": -1.0, "warning_sla_hours": -1.0, "breach_sla_hours": -1.0},
            )

    def test_unrecognised_breach_action_raises(self):
        # A typo must not flow through to automation that string-matches the
        # action and would therefore never halt.
        with self.assertRaises(ValueError):
            self.evaluate(52 * HOUR, {"action_on_breach": "HALT_RETRAINING"})

    def test_empty_identifiers_raise(self):
        with self.assertRaises(ValueError):
            self.evaluate(5 * HOUR, {"model_id": "   "})

    def test_negative_max_missing_days_raises(self):
        with self.assertRaises(ValueError):
            self.evaluate(5 * HOUR, {"max_missing_days": -1})

    def test_negative_skew_tolerance_raises(self):
        with self.assertRaises(ValueError):
            self.evaluate(5 * HOUR, {"clock_skew_tolerance_seconds": -1.0})


class TestPayloadValidation(FreshnessSlaTestBase):

    def test_dataset_name_mismatch_raises(self):
        cfg = self.config()
        meta = self.metadata(5 * HOUR, dataset_name="FX_SPOT_TICKS")
        with self.assertRaises(ValueError):
            self.engine.evaluate_training_freshness_sla(cfg, meta)

    def test_negative_counts_raise(self):
        with self.assertRaises(ValueError):
            self.evaluate(5 * HOUR, total_record_count=-1)
        with self.assertRaises(ValueError):
            self.evaluate(5 * HOUR, missing_days_count=-3)

    def test_boolean_count_raises_type_error(self):
        with self.assertRaises(TypeError):
            self.evaluate(5 * HOUR, missing_days_count=True)

    def test_unknown_timestamp_basis_raises(self):
        with self.assertRaises(ValueError):
            self.evaluate(5 * HOUR, timestamp_basis="ARRIVAL_TIME")


class TestGapAndVolumeGates(FreshnessSlaTestBase):
    """Zero lag on a gapped or truncated dataset is not freshness."""

    def test_missing_days_at_limit_is_not_a_breach(self):
        report = self.evaluate(5 * HOUR, missing_days_count=2)
        self.assertEqual(report.status, STATUS_COMPLIANT)
        self.assertEqual(report.missing_days_count, 2)

    def test_missing_days_past_limit_breaches_despite_zero_lag(self):
        report = self.evaluate(0.0, missing_days_count=3)
        self.assertEqual(report.status, STATUS_BREACH_CRITICAL)
        self.assertTrue(report.is_sla_breached)

    def test_max_missing_days_is_configurable(self):
        report = self.evaluate(
            5 * HOUR, {"max_missing_days": 5}, missing_days_count=4)
        self.assertEqual(report.status, STATUS_COMPLIANT)

    def test_missing_days_breach_note_does_not_claim_a_lag_breach(self):
        # Regression: the breach note was a fixed string asserting the lag
        # "exceeds hard limit (48.00h) or missing days (N)" regardless of which
        # condition fired, so the audit record misstated its own trigger.
        report = self.evaluate(5 * HOUR, missing_days_count=9)
        self.assertIn("missing days", report.audit_notes)
        self.assertNotIn("exceeds the hard limit", report.audit_notes)

    def test_lag_breach_note_does_not_mention_absent_gaps(self):
        report = self.evaluate(52 * HOUR, missing_days_count=0)
        self.assertIn("exceeds the hard limit", report.audit_notes)
        self.assertNotIn("missing days", report.audit_notes)

    def test_both_triggers_are_reported_together(self):
        report = self.evaluate(52 * HOUR, missing_days_count=9)
        self.assertIn("exceeds the hard limit", report.audit_notes)
        self.assertIn("missing days", report.audit_notes)

    def test_min_record_count_breaches_a_fresh_but_truncated_dataset(self):
        report = self.evaluate(
            1 * HOUR, {"min_record_count": 5000}, total_record_count=120)
        self.assertEqual(report.status, STATUS_BREACH_CRITICAL)
        self.assertIn("below min_record_count", report.audit_notes)

    def test_min_record_count_defaults_to_disabled(self):
        report = self.evaluate(1 * HOUR, total_record_count=0)
        self.assertEqual(report.status, STATUS_COMPLIANT)


class TestSessionCalendarAdjustment(FreshnessSlaTestBase):
    """Wall-clock lag alone halts healthy daily-bar pipelines every Monday."""

    def test_monday_audit_of_friday_close_breaches_without_calendar_context(self):
        # Friday 16:00 close audited Monday 09:00 is 65h of wall clock on a
        # pipeline that did exactly what it was supposed to do.
        report = self.evaluate(65 * HOUR)
        self.assertEqual(report.status, STATUS_BREACH_CRITICAL)

    def test_same_audit_is_compliant_once_the_weekend_is_excluded(self):
        report = self.evaluate(65 * HOUR, calendar_excluded_hours=63.0)
        self.assertEqual(report.status, STATUS_COMPLIANT)
        self.assertEqual(report.data_lag_hours, 65.0)      # raw age preserved
        self.assertEqual(report.effective_lag_hours, 2.0)  # verdict basis
        self.assertIn("non-publishing calendar time", report.audit_notes)

    def test_exclusion_cannot_mask_a_genuine_stall(self):
        # A pipeline stalled for a full extra day still breaches after the
        # same weekend allowance is applied.
        report = self.evaluate(120 * HOUR, calendar_excluded_hours=63.0)
        self.assertEqual(report.status, STATUS_BREACH_CRITICAL)
        self.assertEqual(report.effective_lag_hours, 57.0)

    def test_load_bearing_exclusion_is_flagged_in_the_audit_record(self):
        # calendar_excluded_hours is a trusted caller input; when it is the only
        # reason a dataset passed, the audit record must say so.
        report = self.evaluate(65 * HOUR, calendar_excluded_hours=63.0)
        self.assertEqual(report.status, STATUS_COMPLIANT)
        self.assertIn("CALENDAR EXCLUSION IS LOAD-BEARING", report.audit_notes)

    def test_routine_exclusion_is_not_flagged_as_load_bearing(self):
        # Raw lag well inside the hard limit: the exclusion did not decide it.
        report = self.evaluate(30 * HOUR, calendar_excluded_hours=10.0)
        self.assertEqual(report.status, STATUS_COMPLIANT)
        self.assertNotIn("LOAD-BEARING", report.audit_notes)

    def test_exclusion_exceeding_elapsed_time_raises(self):
        with self.assertRaises(ValueError):
            self.evaluate(5 * HOUR, calendar_excluded_hours=9.0)

    def test_exclusion_equal_to_elapsed_time_yields_zero_effective_lag(self):
        report = self.evaluate(5 * HOUR, calendar_excluded_hours=5.0)
        self.assertEqual(report.effective_lag_hours, 0.0)
        self.assertEqual(report.status, STATUS_COMPLIANT)


class TestClockSkew(FreshnessSlaTestBase):

    def test_sub_second_skew_is_absorbed_not_fatal(self):
        # Routine NTP skew between the pipeline writer and the auditing host
        # must not crash a nightly governance job.
        report = self.evaluate(-0.4)
        self.assertEqual(report.status, STATUS_COMPLIANT)
        self.assertEqual(report.effective_lag_hours, 0.0)
        self.assertAlmostEqual(report.clock_skew_seconds, 0.4, places=3)
        self.assertIn("Clock skew", report.audit_notes)

    def test_skew_beyond_tolerance_raises(self):
        with self.assertRaises(ValueError):
            self.evaluate(-3600.0)

    def test_skew_tolerance_is_configurable(self):
        report = self.evaluate(-30.0, {"clock_skew_tolerance_seconds": 60.0})
        self.assertEqual(report.status, STATUS_COMPLIANT)
        self.assertAlmostEqual(report.clock_skew_seconds, 30.0, places=3)

    def test_forward_dated_record_is_never_treated_as_negative_lag(self):
        report = self.evaluate(-0.9)
        self.assertGreaterEqual(report.effective_lag_hours, 0.0)


class TestTimestampBasis(FreshnessSlaTestBase):

    def test_ingestion_basis_is_flagged_in_the_audit_record(self):
        report = self.evaluate(5 * HOUR, timestamp_basis=BASIS_INGESTION_TIME)
        self.assertEqual(report.timestamp_basis, BASIS_INGESTION_TIME)
        self.assertIn("understates true staleness", report.audit_notes)

    def test_event_basis_adds_no_caveat(self):
        report = self.evaluate(5 * HOUR)
        self.assertNotIn("understates true staleness", report.audit_notes)


class TestUnitsAndDeterminism(FreshnessSlaTestBase):

    def test_epoch_milliseconds_fail_closed(self):
        # Passing milliseconds inflates lag ~1000x. It is a caller bug, but it
        # must surface as a breach, never as a pass.
        meta = DatasetMetadataPayload(
            dataset_name=DATASET,
            latest_record_timestamp_epoch=(self.now - 5 * HOUR) * 1000.0,
            current_system_timestamp_epoch=self.now * 1000.0,
            total_record_count=10000,
        )
        report = self.engine.evaluate_training_freshness_sla(self.config(), meta)
        self.assertEqual(report.status, STATUS_BREACH_CRITICAL)

    def test_evaluation_is_deterministic(self):
        first = self.evaluate(30 * HOUR)
        second = self.evaluate(30 * HOUR)
        self.assertEqual(first, second)

    def test_report_echoes_the_full_ladder_for_audit(self):
        report = self.evaluate(30 * HOUR)
        self.assertEqual(report.model_id, "ALPHA_MODEL_01")
        self.assertEqual(report.dataset_name, DATASET)
        self.assertEqual(report.target_sla_hours, 24.0)
        self.assertEqual(report.warning_sla_hours, 36.0)
        self.assertEqual(report.breach_sla_hours, 48.0)
        self.assertEqual(report.total_record_count, 10000)


if __name__ == "__main__":
    unittest.main()
