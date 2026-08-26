import logging
import unittest

from key_rotation_schedule_for_hot_wallet_keys import (
    DEFAULT_CLOCK_SKEW_TOLERANCE_SECONDS,
    KEY_CLASS_EXCHANGE_API,
    KEY_CLASS_ONCHAIN_SIGNING,
    STATE_ACTIVE,
    STATE_DEPRECATED_GRACE_PERIOD,
    STATE_PENDING_FUND_SWEEP,
    STATE_REVOKED_SHREDDED,
    STATUS_EMERGENCY_REVOKED_COMPROMISED,
    STATUS_EMERGENCY_SWEEP_REQUIRED,
    STATUS_GRACE_PERIOD_ACTIVE,
    STATUS_GRACE_PERIOD_ELAPSED_PENDING_SWEEP,
    STATUS_KEY_ALREADY_REVOKED,
    STATUS_KEY_HEALTHY_ACTIVE,
    STATUS_ROTATION_COMPLETE_KEY_SHREDDED,
    STATUS_ROTATION_INITIATED_AGE_EXPIRED,
    STATUS_ROTATION_INITIATED_USAGE_EXPIRED,
    STATUS_ROTATION_INITIATED_VOLUME_EXPIRED,
    HotWalletKeyMetadata,
    HotWalletKeyRotationEngine,
    KeyRotationError,
)

logging.getLogger("key_rotation_schedule_for_hot_wallet_keys").setLevel(logging.CRITICAL + 1)

DAY = 86400.0
HOUR = 3600.0
NOW = 1700000000.0


def make_key(**overrides):
    """A healthy 30-day-old on-chain key with no residual balance."""
    params = dict(
        key_id="HOT_KEY_01",
        created_timestamp_epoch=NOW - 30 * DAY,
        last_used_timestamp_epoch=NOW,
        total_signatures_count=5_000,
        total_volume_usd_signed=500_000.0,
        is_compromised=False,
    )
    params.update(overrides)
    return HotWalletKeyMetadata(**params)


class TestHealthyAndTriggers(unittest.TestCase):
    def setUp(self):
        self.engine = HotWalletKeyRotationEngine()

    def test_healthy_active_key(self):
        report = self.engine.audit_and_rotate_key(make_key(), current_time_epoch=NOW)
        self.assertEqual(report.status, STATUS_KEY_HEALTHY_ACTIVE)
        self.assertFalse(report.is_rotation_required)
        self.assertEqual(report.new_key_state, STATE_ACTIVE)
        self.assertIsNone(report.replacement_key_id)
        self.assertEqual(report.key_age_days, 30.0)

    def test_age_trigger_initiates_rotation(self):
        meta = make_key(key_id="HOT_KEY_02", created_timestamp_epoch=NOW - 95 * DAY)
        report = self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW)

        self.assertEqual(report.status, STATUS_ROTATION_INITIATED_AGE_EXPIRED)
        self.assertTrue(report.is_rotation_required)
        self.assertEqual(report.new_key_state, STATE_DEPRECATED_GRACE_PERIOD)
        self.assertEqual(report.replacement_key_id, "HOT_KEY_02_V2")
        # The grace clock must actually be recorded, or the grace expiry in step 4 of the
        # workflow can never be evaluated.
        self.assertEqual(meta.grace_period_started_epoch, NOW)
        self.assertEqual(report.grace_period_ends_epoch, NOW + 24 * HOUR)

    def test_signature_count_trigger(self):
        meta = make_key(total_signatures_count=100_000)
        report = self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW)
        self.assertEqual(report.status, STATUS_ROTATION_INITIATED_USAGE_EXPIRED)

    def test_volume_trigger(self):
        # Volume was the one trigger with no test at all before.
        meta = make_key(total_volume_usd_signed=10_000_000.0)
        report = self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW)
        self.assertEqual(report.status, STATUS_ROTATION_INITIATED_VOLUME_EXPIRED)
        self.assertTrue(report.is_rotation_required)

    def test_age_takes_precedence_over_usage_and_volume(self):
        meta = make_key(
            created_timestamp_epoch=NOW - 95 * DAY,
            total_signatures_count=200_000,
            total_volume_usd_signed=50_000_000.0,
        )
        report = self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW)
        self.assertEqual(report.status, STATUS_ROTATION_INITIATED_AGE_EXPIRED)

    def test_exact_threshold_is_inclusive(self):
        """>= is the documented comparison; exactly 90.0 days must trigger."""
        meta = make_key(created_timestamp_epoch=NOW - 90 * DAY)
        report = self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW)
        self.assertEqual(report.status, STATUS_ROTATION_INITIATED_AGE_EXPIRED)

    def test_one_second_under_threshold_does_not_trigger(self):
        """Regression: classification once ran on an age rounded to 2dp.

        89.99998 days rounds to 90.0, so a rounded comparison flags this key as expired.
        Policy must be decided on the unrounded age; rounding is presentation only.
        """
        meta = make_key(created_timestamp_epoch=NOW - (90 * DAY - 1.0))
        report = self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW)
        self.assertEqual(report.status, STATUS_KEY_HEALTHY_ACTIVE)
        self.assertEqual(report.key_age_days, 90.0)  # display still rounds to 90.0

    def test_custom_thresholds_are_honoured(self):
        engine = HotWalletKeyRotationEngine(max_key_age_days=7.0)
        meta = make_key(created_timestamp_epoch=NOW - 10 * DAY)
        report = engine.audit_and_rotate_key(meta, current_time_epoch=NOW)
        self.assertEqual(report.status, STATUS_ROTATION_INITIATED_AGE_EXPIRED)


class TestCompromise(unittest.TestCase):
    def setUp(self):
        self.engine = HotWalletKeyRotationEngine()

    def test_compromised_api_key_is_revoked_immediately(self):
        meta = make_key(
            key_id="HOT_KEY_03",
            is_compromised=True,
            key_class=KEY_CLASS_EXCHANGE_API,
        )
        report = self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW)
        self.assertEqual(report.status, STATUS_EMERGENCY_REVOKED_COMPROMISED)
        self.assertTrue(report.is_rotation_required)
        self.assertEqual(report.new_key_state, STATE_REVOKED_SHREDDED)

    def test_compromised_onchain_key_with_balance_demands_sweep_not_shred(self):
        """The core domain rule: an on-chain key cannot be revoked.

        Marking it shredded while funds remain either strands them (if the material was
        really destroyed) or leaves an attacker in control of an address the audit trail
        says is dead.
        """
        meta = make_key(
            key_id="HOT_KEY_ETH",
            is_compromised=True,
            key_class=KEY_CLASS_ONCHAIN_SIGNING,
            residual_balance_usd=250_000.0,
        )
        report = self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW)

        self.assertEqual(report.status, STATUS_EMERGENCY_SWEEP_REQUIRED)
        self.assertEqual(report.new_key_state, STATE_PENDING_FUND_SWEEP)
        self.assertNotEqual(report.new_key_state, STATE_REVOKED_SHREDDED)
        self.assertTrue(report.requires_fund_sweep)

    def test_compromised_onchain_key_with_zero_balance_is_shredded(self):
        meta = make_key(
            is_compromised=True,
            key_class=KEY_CLASS_ONCHAIN_SIGNING,
            residual_balance_usd=0.0,
        )
        report = self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW)
        self.assertEqual(report.status, STATUS_EMERGENCY_REVOKED_COMPROMISED)
        self.assertEqual(report.new_key_state, STATE_REVOKED_SHREDDED)

    def test_compromise_overrides_a_healthy_young_key(self):
        meta = make_key(
            created_timestamp_epoch=NOW - 1 * DAY,
            total_signatures_count=1,
            key_class=KEY_CLASS_EXCHANGE_API,
            is_compromised=True,
        )
        report = self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW)
        self.assertEqual(report.status, STATUS_EMERGENCY_REVOKED_COMPROMISED)

    def test_compromise_never_grants_a_grace_period(self):
        meta = make_key(key_class=KEY_CLASS_EXCHANGE_API, is_compromised=True)
        report = self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW)
        self.assertNotEqual(report.new_key_state, STATE_DEPRECATED_GRACE_PERIOD)
        self.assertIsNone(report.grace_period_ends_epoch)


class TestGracePeriodLifecycle(unittest.TestCase):
    """The grace period was previously declared but never enforced."""

    def setUp(self):
        self.engine = HotWalletKeyRotationEngine()

    def _rotate(self, **overrides):
        meta = make_key(created_timestamp_epoch=NOW - 95 * DAY, **overrides)
        self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW)
        return meta

    def test_re_audit_inside_grace_is_idempotent(self):
        meta = self._rotate()
        report = self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW + 6 * HOUR)

        self.assertEqual(report.status, STATUS_GRACE_PERIOD_ACTIVE)
        self.assertEqual(report.new_key_state, STATE_DEPRECATED_GRACE_PERIOD)
        self.assertEqual(report.replacement_key_id, "HOT_KEY_01_V2")
        # The grace clock must not restart on every audit, or the key never ages out.
        self.assertEqual(meta.grace_period_started_epoch, NOW)

    def test_grace_expiry_shreds_a_key_holding_nothing(self):
        meta = self._rotate()
        report = self.engine.audit_and_rotate_key(
            meta, current_time_epoch=NOW + 24 * HOUR + 1
        )
        self.assertEqual(report.status, STATUS_ROTATION_COMPLETE_KEY_SHREDDED)
        self.assertEqual(report.new_key_state, STATE_REVOKED_SHREDDED)
        self.assertFalse(report.is_rotation_required)

    def test_grace_expiry_with_residual_balance_blocks_shredding(self):
        meta = self._rotate(residual_balance_usd=1_000.0)
        report = self.engine.audit_and_rotate_key(
            meta, current_time_epoch=NOW + 25 * HOUR
        )
        self.assertEqual(report.status, STATUS_GRACE_PERIOD_ELAPSED_PENDING_SWEEP)
        self.assertEqual(report.new_key_state, STATE_PENDING_FUND_SWEEP)
        self.assertTrue(report.requires_fund_sweep)

    def test_pending_sweep_resolves_once_balance_is_zero(self):
        meta = self._rotate(residual_balance_usd=1_000.0)
        self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW + 25 * HOUR)
        self.assertEqual(meta.current_state, STATE_PENDING_FUND_SWEEP)

        meta.residual_balance_usd = 0.0
        report = self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW + 26 * HOUR)
        self.assertEqual(report.status, STATUS_ROTATION_COMPLETE_KEY_SHREDDED)
        self.assertEqual(report.new_key_state, STATE_REVOKED_SHREDDED)

    def test_exchange_api_key_ignores_residual_balance(self):
        """An API key controls no address of its own; the account balance is irrelevant."""
        meta = self._rotate(
            key_class=KEY_CLASS_EXCHANGE_API, residual_balance_usd=5_000_000.0
        )
        report = self.engine.audit_and_rotate_key(
            meta, current_time_epoch=NOW + 25 * HOUR
        )
        self.assertEqual(report.status, STATUS_ROTATION_COMPLETE_KEY_SHREDDED)
        self.assertFalse(report.requires_fund_sweep)

    def test_key_still_signing_during_grace_is_flagged(self):
        meta = self._rotate()
        meta.last_used_timestamp_epoch = NOW + 2 * HOUR  # signed after cutover began
        report = self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW + 3 * HOUR)

        self.assertEqual(report.status, STATUS_GRACE_PERIOD_ACTIVE)
        self.assertTrue(report.warnings)
        self.assertIn("cutover", " ".join(report.warnings).lower())

    def test_grace_state_without_a_start_time_is_rejected(self):
        meta = make_key(current_state=STATE_DEPRECATED_GRACE_PERIOD)
        with self.assertRaises(KeyRotationError):
            self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW)

    def test_future_dated_grace_start_is_rejected(self):
        """A grace start in the future never elapses -- the key would never leave grace."""
        meta = make_key(
            current_state=STATE_DEPRECATED_GRACE_PERIOD,
            grace_period_started_epoch=NOW + 365 * DAY,
        )
        with self.assertRaises(KeyRotationError) as ctx:
            self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW)
        self.assertIn("future", str(ctx.exception))

    def test_dust_blocks_completion_by_default(self):
        """Fail-closed: any residual value at all blocks destruction of the key."""
        meta = self._rotate(residual_balance_usd=1e-9)
        report = self.engine.audit_and_rotate_key(
            meta, current_time_epoch=NOW + 25 * HOUR
        )
        self.assertEqual(report.status, STATUS_GRACE_PERIOD_ELAPSED_PENDING_SWEEP)

    def test_dust_threshold_allows_completion_when_opted_in(self):
        engine = HotWalletKeyRotationEngine(dust_threshold_usd=0.01)
        meta = make_key(
            created_timestamp_epoch=NOW - 95 * DAY, residual_balance_usd=1e-9
        )
        engine.audit_and_rotate_key(meta, current_time_epoch=NOW)
        report = engine.audit_and_rotate_key(meta, current_time_epoch=NOW + 25 * HOUR)
        self.assertEqual(report.status, STATUS_ROTATION_COMPLETE_KEY_SHREDDED)
        self.assertFalse(report.requires_fund_sweep)

    def test_balance_above_dust_threshold_still_blocks(self):
        engine = HotWalletKeyRotationEngine(dust_threshold_usd=0.01)
        meta = make_key(
            created_timestamp_epoch=NOW - 95 * DAY, residual_balance_usd=5_000.0
        )
        engine.audit_and_rotate_key(meta, current_time_epoch=NOW)
        report = engine.audit_and_rotate_key(meta, current_time_epoch=NOW + 25 * HOUR)
        self.assertEqual(report.status, STATUS_GRACE_PERIOD_ELAPSED_PENDING_SWEEP)

    def test_grace_window_length_is_configurable(self):
        engine = HotWalletKeyRotationEngine(grace_period_hours=1.0)
        meta = make_key(created_timestamp_epoch=NOW - 95 * DAY)
        engine.audit_and_rotate_key(meta, current_time_epoch=NOW)
        report = engine.audit_and_rotate_key(meta, current_time_epoch=NOW + 2 * HOUR)
        self.assertEqual(report.status, STATUS_ROTATION_COMPLETE_KEY_SHREDDED)


class TestTerminalState(unittest.TestCase):
    def setUp(self):
        self.engine = HotWalletKeyRotationEngine()

    def test_revoked_key_is_never_reported_active_again(self):
        """Regression: a young revoked key used to fall through and report ACTIVE.

        An automated caller reading new_key_state == 'ACTIVE' would resume signing with a
        key whose material is supposed to be destroyed.
        """
        meta = make_key(
            created_timestamp_epoch=NOW - 10 * DAY,
            current_state=STATE_REVOKED_SHREDDED,
        )
        report = self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW)

        self.assertEqual(report.status, STATUS_KEY_ALREADY_REVOKED)
        self.assertEqual(report.new_key_state, STATE_REVOKED_SHREDDED)
        self.assertNotEqual(report.new_key_state, STATE_ACTIVE)
        self.assertFalse(report.is_rotation_required)

    def test_revoked_key_holding_funds_raises_a_stranded_warning(self):
        meta = make_key(
            current_state=STATE_REVOKED_SHREDDED,
            key_class=KEY_CLASS_ONCHAIN_SIGNING,
            residual_balance_usd=42_000.0,
        )
        report = self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW)
        self.assertEqual(report.status, STATUS_KEY_ALREADY_REVOKED)
        self.assertTrue(report.warnings)

    def test_compromise_flag_on_revoked_key_does_not_reopen_it(self):
        meta = make_key(current_state=STATE_REVOKED_SHREDDED, is_compromised=True)
        report = self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW)
        self.assertEqual(report.status, STATUS_KEY_ALREADY_REVOKED)

    def test_compromise_flag_on_revoked_key_is_not_dropped_silently(self):
        """Rotation has nothing left to do, but the flag must still surface for forensics."""
        meta = make_key(current_state=STATE_REVOKED_SHREDDED, is_compromised=True)
        report = self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW)
        self.assertTrue(report.warnings)
        self.assertIn("forensics", " ".join(report.warnings).lower())


class TestTimestampValidation(unittest.TestCase):
    def setUp(self):
        self.engine = HotWalletKeyRotationEngine()

    def test_millisecond_epoch_is_rejected(self):
        """Regression: an ms timestamp used to clamp age to 0 and report HEALTHY forever."""
        meta = make_key(created_timestamp_epoch=NOW * 1000.0)
        with self.assertRaises(KeyRotationError):
            self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW)

    def test_creation_far_in_the_future_is_rejected(self):
        # last_used moves with creation so this isolates the future-creation check rather
        # than tripping the earlier "signed before it existed" check.
        meta = make_key(
            created_timestamp_epoch=NOW + 30 * DAY,
            last_used_timestamp_epoch=NOW + 30 * DAY,
        )
        with self.assertRaises(KeyRotationError) as ctx:
            self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW)
        self.assertIn("future", str(ctx.exception))

    def test_small_clock_skew_is_tolerated(self):
        skew = DEFAULT_CLOCK_SKEW_TOLERANCE_SECONDS / 2
        meta = make_key(
            created_timestamp_epoch=NOW + skew,
            last_used_timestamp_epoch=NOW + skew,
        )
        report = self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW)
        self.assertEqual(report.status, STATUS_KEY_HEALTHY_ACTIVE)
        self.assertEqual(report.key_age_days, 0.0)

    def test_last_used_before_creation_is_rejected(self):
        meta = make_key(last_used_timestamp_epoch=NOW - 60 * DAY)
        with self.assertRaises(KeyRotationError):
            self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW)

    def test_non_finite_timestamp_is_rejected(self):
        for bad in (float("nan"), float("inf"), 0.0, -1.0):
            with self.subTest(bad=bad):
                meta = make_key(created_timestamp_epoch=bad)
                with self.assertRaises(KeyRotationError):
                    self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW)


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.engine = HotWalletKeyRotationEngine()

    def test_nan_volume_is_rejected(self):
        meta = make_key(total_volume_usd_signed=float("nan"))
        with self.assertRaises(KeyRotationError):
            self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW)

    def test_negative_signature_count_is_rejected(self):
        meta = make_key(total_signatures_count=-1)
        with self.assertRaises(KeyRotationError):
            self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW)

    def test_float_signature_count_is_rejected(self):
        meta = make_key(total_signatures_count=5_000.5)
        with self.assertRaises(KeyRotationError):
            self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW)

    def test_negative_residual_balance_is_rejected(self):
        meta = make_key(residual_balance_usd=-1.0)
        with self.assertRaises(KeyRotationError):
            self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW)

    def test_empty_key_id_is_rejected(self):
        meta = make_key(key_id="   ")
        with self.assertRaises(KeyRotationError):
            self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW)

    def test_unknown_state_is_rejected(self):
        meta = make_key(current_state="NEEDS_ROTATION")
        with self.assertRaises(KeyRotationError):
            self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW)

    def test_unknown_key_class_is_rejected(self):
        meta = make_key(key_class="HSM_BACKED")
        with self.assertRaises(KeyRotationError):
            self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW)

    def test_invalid_engine_configuration_is_rejected(self):
        for kwargs in (
            {"max_key_age_days": 0.0},
            {"max_key_age_days": -1.0},
            {"max_signatures_limit": 0},
            {"max_volume_usd_limit": -5.0},
            {"grace_period_hours": -1.0},
            {"max_key_age_days": float("nan")},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(KeyRotationError):
                    HotWalletKeyRotationEngine(**kwargs)


class TestReportIntegrity(unittest.TestCase):
    def setUp(self):
        self.engine = HotWalletKeyRotationEngine()

    def test_report_echoes_usage_figures(self):
        meta = make_key(total_signatures_count=1_234, total_volume_usd_signed=99.5)
        report = self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW)
        self.assertEqual(report.total_signatures_count, 1_234)
        self.assertEqual(report.total_volume_usd_signed, 99.5)

    def test_warnings_are_not_shared_between_reports(self):
        a = self.engine.audit_and_rotate_key(make_key(), current_time_epoch=NOW)
        b = self.engine.audit_and_rotate_key(make_key(), current_time_epoch=NOW)
        a.warnings.append("local only")
        self.assertEqual(b.warnings, [])

    def test_full_lifecycle_reaches_a_terminal_state(self):
        meta = make_key(
            created_timestamp_epoch=NOW - 95 * DAY, residual_balance_usd=10_000.0
        )
        statuses = [
            self.engine.audit_and_rotate_key(meta, current_time_epoch=NOW).status,
            self.engine.audit_and_rotate_key(
                meta, current_time_epoch=NOW + 1 * HOUR
            ).status,
            self.engine.audit_and_rotate_key(
                meta, current_time_epoch=NOW + 25 * HOUR
            ).status,
        ]
        meta.residual_balance_usd = 0.0
        statuses.append(
            self.engine.audit_and_rotate_key(
                meta, current_time_epoch=NOW + 26 * HOUR
            ).status
        )
        statuses.append(
            self.engine.audit_and_rotate_key(
                meta, current_time_epoch=NOW + 27 * HOUR
            ).status
        )

        self.assertEqual(
            statuses,
            [
                STATUS_ROTATION_INITIATED_AGE_EXPIRED,
                STATUS_GRACE_PERIOD_ACTIVE,
                STATUS_GRACE_PERIOD_ELAPSED_PENDING_SWEEP,
                STATUS_ROTATION_COMPLETE_KEY_SHREDDED,
                STATUS_KEY_ALREADY_REVOKED,
            ],
        )
        self.assertEqual(meta.current_state, STATE_REVOKED_SHREDDED)


if __name__ == "__main__":
    unittest.main()
