"""
Behavioural tests for the model registry and rollback engine.

Several tests are regressions against specific defects in the pre-2.0
implementation and are marked as such: registering a PRODUCTION artifact used to
silently unseat the live model, a NaN drawdown used to read as healthy, a
64-character non-hex string used to pass as a digest, a failed rollback used to
leave the registry with nothing serving while reporting the failing version as
active, and a repeated stale sample used to re-report a fresh rollback on
every poll.

Fail-safe wiring, exercised by ``test_unusable_telemetry_raises_not_healthy``:
a monitoring loop must treat ``ModelRegistryError`` as a *failed check*, e.g.

    try:
        report = engine.audit_telemetry_and_rollback(cfg, telem)
    except ModelRegistryError:
        halt_trading_and_page_on_call()   # never `continue`
"""
import logging
import threading
import unittest

from model_version_manager import (
    LivePerformanceTelemetry,
    ModelRegistryError,
    ModelVersion,
    ModelVersionManagerEngine,
    RollbackTriggerConfig,
    parse_semver,
    semver_precedence_key,
)


def setUpModule():
    """
    Keep the engine's (correct, deliberate) CRITICAL/WARNING audit output off the
    test console. `assertLogs` installs its own handler, so log assertions still
    work.
    """
    engine_logger = logging.getLogger("model_version_manager")
    engine_logger.addHandler(logging.NullHandler())
    engine_logger.propagate = False


HASH_V1 = ModelVersionManagerEngine.compute_sha256(b"v1_model_bytes_payload")
HASH_V2 = ModelVersionManagerEngine.compute_sha256(b"v2_model_bytes_payload")
HASH_V3 = ModelVersionManagerEngine.compute_sha256(b"v3_model_bytes_payload")


def make_version(version, **overrides):
    """A registrable ModelVersion with sane defaults, overridable per test."""
    defaults = dict(
        model_id="ML_ALPHA_101",
        version=version,
        sha256_hash=HASH_V1,
        training_dataset_id="DS_2025_Q4",
        sharpe_ratio=2.1,
        max_drawdown_pct=10.0,
        status="PRODUCTION",
        is_active=False,
        registered_at_epoch=1740000000.0,
        approved_by="risk.committee",
    )
    defaults.update(overrides)
    return ModelVersion(**defaults)


class TestSemanticVersionParsing(unittest.TestCase):
    """Semantic Versioning 2.0.0 rules 2, 10 and 11, checked independently."""

    def test_parses_normal_version_with_and_without_v_prefix(self):
        self.assertEqual(parse_semver("v1.2.3"), (1, 2, 3, None))
        self.assertEqual(parse_semver("1.2.3"), (1, 2, 3, None))
        self.assertEqual(parse_semver("v2.0.0-rc.1"), (2, 0, 0, "rc.1"))
        self.assertEqual(parse_semver("v0.0.0"), (0, 0, 0, None))

    def test_rejects_non_semver_strings(self):
        for bad in [
            "latest", "latest_model.pkl", "v1.0", "1.0.0.0", "v01.0.0",
            "v1.00.0", "", "v-1.0.0", "1.2.3+build.5", "v1.2.3+sha.abc",
        ]:
            with self.subTest(version=bad):
                with self.assertRaises(ModelRegistryError):
                    parse_semver(bad)

    def test_precedence_is_numeric_not_lexicographic(self):
        # The defect this guards: sorting version *strings* puts "v1.10.0"
        # below "v1.9.0", so a rollback would pick the wrong artifact.
        self.assertLess("v1.10.0", "v1.9.0")
        self.assertGreater(
            semver_precedence_key("v1.10.0"), semver_precedence_key("v1.9.0")
        )
        self.assertGreater(
            semver_precedence_key("v2.0.0"), semver_precedence_key("v1.99.99")
        )

    def test_prerelease_ranks_below_the_normal_release(self):
        # Semver rule 11: "a pre-release version has lower precedence than a
        # normal version".
        self.assertLess(
            semver_precedence_key("v1.0.0-alpha"), semver_precedence_key("v1.0.0")
        )
        # Numeric identifiers rank below alphanumeric ones.
        self.assertLess(
            semver_precedence_key("v1.0.0-1"), semver_precedence_key("v1.0.0-alpha")
        )
        # Fewer identifiers rank below more, all else equal.
        self.assertLess(
            semver_precedence_key("v1.0.0-alpha"),
            semver_precedence_key("v1.0.0-alpha.1"),
        )
        self.assertLess(
            semver_precedence_key("v1.0.0-alpha.1"),
            semver_precedence_key("v1.0.0-beta"),
        )


class TestRegistration(unittest.TestCase):

    def setUp(self):
        self.engine = ModelVersionManagerEngine()

    def test_registers_and_returns_a_defensive_copy(self):
        original = make_version("v1.0.0")
        stored = self.engine.register_version(original)
        self.assertEqual(stored.version, "v1.0.0")

        # Mutating the caller's object must not rewrite registry history.
        original.sha256_hash = HASH_V2
        original.training_dataset_id = "DS_TAMPERED"
        self.assertEqual(self.engine.registry["ML_ALPHA_101"]["v1.0.0"].sha256_hash, HASH_V1)
        self.assertEqual(
            self.engine.registry["ML_ALPHA_101"]["v1.0.0"].training_dataset_id, "DS_2025_Q4"
        )

    def test_reregistration_with_different_artifact_is_rejected(self):
        # Regression: the previous implementation overwrote the entry, so a
        # version string could silently come to mean a different artifact.
        self.engine.register_version(make_version("v1.0.0", sha256_hash=HASH_V1))
        with self.assertRaises(ModelRegistryError) as ctx:
            self.engine.register_version(make_version("v1.0.0", sha256_hash=HASH_V2))
        self.assertIn("immutable", str(ctx.exception))
        self.assertEqual(self.engine.registry["ML_ALPHA_101"]["v1.0.0"].sha256_hash, HASH_V1)

    def test_identical_reregistration_is_an_idempotent_no_op(self):
        self.engine.register_version(make_version("v1.0.0"))
        self.engine.register_version(make_version("v1.0.0"))
        self.assertEqual(len(self.engine.registry["ML_ALPHA_101"]), 1)
        register_events = [e for e in self.engine.audit_log if e.event == "REGISTER"]
        self.assertEqual(len(register_events), 1)

    def test_rejects_non_hex_and_wrong_length_hashes(self):
        # Regression: length-64 was the only check, so "z" * 64 passed.
        for bad in ["z" * 64, "", "abc", HASH_V1[:63], HASH_V1 + "a"]:
            with self.subTest(sha256_hash=bad):
                with self.assertRaises(ModelRegistryError):
                    self.engine.register_version(make_version("v1.0.0", sha256_hash=bad))

    def test_uppercase_hash_is_normalised_and_still_verifies(self):
        self.engine.register_version(
            make_version("v1.0.0", sha256_hash=HASH_V1.upper())
        )
        self.assertEqual(self.engine.registry["ML_ALPHA_101"]["v1.0.0"].sha256_hash, HASH_V1)
        self.assertTrue(
            self.engine.verify_artifact("ML_ALPHA_101", "v1.0.0", b"v1_model_bytes_payload")
        )

    def test_rejects_unregistrable_status_and_engine_managed_status(self):
        for bad in ["DEACTIVATED_ROLLBACK", "production", "LIVE", ""]:
            with self.subTest(status=bad):
                with self.assertRaises(ModelRegistryError):
                    self.engine.register_version(make_version("v1.0.0", status=bad))

    def test_active_requires_production_status(self):
        with self.assertRaises(ModelRegistryError):
            self.engine.register_version(
                make_version("v1.0.0", status="STAGING", is_active=True)
            )

    def test_rejects_missing_training_dataset_and_bad_metrics(self):
        with self.assertRaises(ModelRegistryError):
            self.engine.register_version(make_version("v1.0.0", training_dataset_id=""))
        with self.assertRaises(ModelRegistryError):
            self.engine.register_version(make_version("v1.0.0", max_drawdown_pct=float("nan")))
        with self.assertRaises(ModelRegistryError):
            self.engine.register_version(make_version("v1.0.0", max_drawdown_pct=-5.0))
        with self.assertRaises(ModelRegistryError):
            self.engine.register_version(make_version("v1.0.0", sharpe_ratio=float("inf")))

    def test_registering_a_production_artifact_does_not_unseat_the_live_model(self):
        # Regression: the previous implementation deactivated every version
        # whenever `status == "PRODUCTION"`, so staging the next release left
        # the model with NO active version at all.
        self.engine.register_version(make_version("v1.0.0", is_active=True))
        self.engine.register_version(
            make_version("v1.1.0", sha256_hash=HASH_V2, is_active=False)
        )
        active = self.engine.get_active_version("ML_ALPHA_101")
        self.assertIsNotNone(active)
        self.assertEqual(active.version, "v1.0.0")

    def test_active_registration_unseats_exactly_one_incumbent(self):
        self.engine.register_version(make_version("v1.0.0", is_active=True))
        self.engine.register_version(
            make_version("v1.1.0", sha256_hash=HASH_V2, is_active=True)
        )
        self.assertEqual(self.engine.get_active_version("ML_ALPHA_101").version, "v1.1.0")
        actives = [
            v.version for v in self.engine.registry["ML_ALPHA_101"].values() if v.is_active
        ]
        self.assertEqual(actives, ["v1.1.0"])

    def test_version_with_trailing_whitespace_is_rejected(self):
        # Regex `$` matches before a trailing newline, so an unanchored
        # pattern would accept a version ending in one as a key distinct
        # from "v1.0.0".
        for bad in ["v1.0.0" + chr(10), "v1.0.0 ", " v1.0.0", "v1.0.0" + chr(9)]:
            with self.subTest(version=bad):
                with self.assertRaises(ModelRegistryError):
                    self.engine.register_version(make_version(bad))

    def test_non_finite_registration_epoch_is_rejected(self):
        # Otherwise it only surfaces as a TypeError inside the rollback sort,
        # during an incident.
        for bad in [float("nan"), float("inf"), -1.0, "yesterday"]:
            with self.subTest(registered_at_epoch=bad):
                with self.assertRaises(ModelRegistryError):
                    self.engine.register_version(
                        make_version("v1.0.0", registered_at_epoch=bad)
                    )

    def test_replayed_registration_still_claims_the_pointer(self):
        # A deployer that crash-loops re-sends the same registration. The
        # identity is unchanged, but the intent to deploy must not be dropped.
        self.engine.register_version(make_version("v1.0.0", is_active=False))
        self.assertIsNone(self.engine.get_active_version("ML_ALPHA_101"))
        self.engine.register_version(make_version("v1.0.0", is_active=True))
        self.assertEqual(self.engine.get_active_version("ML_ALPHA_101").version, "v1.0.0")

    def test_replayed_registration_cannot_resurrect_a_quarantined_version(self):
        self.engine.register_version(make_version("v1.0.0"))
        self.engine.register_version(
            make_version("v1.1.0", sha256_hash=HASH_V2, is_active=True)
        )
        self.engine.audit_telemetry_and_rollback(
            RollbackTriggerConfig(),
            LivePerformanceTelemetry("ML_ALPHA_101", "v1.1.0", 18.5, 1.0, 0.4),
        )
        with self.assertRaises(ModelRegistryError):
            self.engine.register_version(
                make_version("v1.1.0", sha256_hash=HASH_V2, is_active=True)
            )
        self.assertEqual(self.engine.get_active_version("ML_ALPHA_101").version, "v1.0.0")

    def test_missing_approver_is_logged_as_a_warning(self):
        with self.assertLogs("model_version_manager", level="WARNING") as logs:
            self.engine.register_version(make_version("v1.0.0", approved_by=None))
        self.assertTrue(any("approved_by" in line for line in logs.output))


class TestArtifactVerification(unittest.TestCase):

    def setUp(self):
        self.engine = ModelVersionManagerEngine()
        self.engine.register_version(make_version("v1.0.0", sha256_hash=HASH_V1))

    def test_matching_payload_verifies(self):
        self.assertTrue(
            self.engine.verify_artifact("ML_ALPHA_101", "v1.0.0", b"v1_model_bytes_payload")
        )

    def test_tampered_payload_fails_and_logs(self):
        with self.assertLogs("model_version_manager", level="ERROR") as logs:
            matched = self.engine.verify_artifact(
                "ML_ALPHA_101", "v1.0.0", b"v1_model_bytes_payload_TAMPERED"
            )
        self.assertFalse(matched)
        self.assertTrue(any("MISMATCH" in line for line in logs.output))

    def test_unknown_identifiers_raise(self):
        with self.assertRaises(ModelRegistryError):
            self.engine.verify_artifact("NO_SUCH_MODEL", "v1.0.0", b"x")
        with self.assertRaises(ModelRegistryError):
            self.engine.verify_artifact("ML_ALPHA_101", "v9.9.9", b"x")

    def test_compute_sha256_matches_an_independently_known_digest(self):
        # NIST FIPS 180-4 / RFC 6234 published test vector for the empty string.
        self.assertEqual(
            ModelVersionManagerEngine.compute_sha256(b""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )


class TestPromotion(unittest.TestCase):

    def setUp(self):
        self.engine = ModelVersionManagerEngine()
        self.engine.register_version(make_version("v1.0.0", is_active=True))
        self.engine.register_version(make_version("v1.1.0", sha256_hash=HASH_V2))

    def test_promote_swaps_the_pointer_and_records_the_change(self):
        promoted = self.engine.promote_version("ML_ALPHA_101", "v1.1.0", approved_by="head.of.quant")
        self.assertTrue(promoted.is_active)
        self.assertEqual(self.engine.get_active_version("ML_ALPHA_101").version, "v1.1.0")
        event = [e for e in self.engine.audit_log if e.event == "PROMOTE"][-1]
        self.assertEqual(event.version, "v1.1.0")
        self.assertEqual(event.approved_by, "head.of.quant")
        self.assertIn("previous_active=v1.0.0", event.detail)

    def test_cannot_repromote_a_quarantined_version(self):
        cfg = RollbackTriggerConfig()
        self.engine.promote_version("ML_ALPHA_101", "v1.1.0")
        self.engine.audit_telemetry_and_rollback(
            cfg,
            LivePerformanceTelemetry("ML_ALPHA_101", "v1.1.0", 18.5, 1.0, 0.4),
        )
        with self.assertRaises(ModelRegistryError):
            self.engine.promote_version("ML_ALPHA_101", "v1.1.0")

    def test_unknown_version_raises(self):
        with self.assertRaises(ModelRegistryError):
            self.engine.promote_version("ML_ALPHA_101", "v9.9.9")

    def test_get_active_version_returns_a_copy(self):
        active = self.engine.get_active_version("ML_ALPHA_101")
        active.is_active = False
        active.status = "ARCHIVED"
        self.assertEqual(self.engine.get_active_version("ML_ALPHA_101").version, "v1.0.0")
        self.assertEqual(self.engine.registry["ML_ALPHA_101"]["v1.0.0"].status, "PRODUCTION")


class TestCircuitBreaker(unittest.TestCase):

    def setUp(self):
        self.engine = ModelVersionManagerEngine()
        self.engine.register_version(
            make_version(
                "v1.0.0", sha256_hash=HASH_V1, sharpe_ratio=2.1,
                max_drawdown_pct=10.0, registered_at_epoch=1740000000.0,
            )
        )
        self.engine.register_version(
            make_version(
                "v1.1.0", sha256_hash=HASH_V2, sharpe_ratio=2.4,
                max_drawdown_pct=11.0, registered_at_epoch=1750000000.0,
                is_active=True,
            )
        )
        self.cfg = RollbackTriggerConfig(
            max_allowed_drawdown_pct=15.0, max_allowed_error_rate_pct=5.0
        )

    def test_healthy_telemetry_no_rollback(self):
        telem = LivePerformanceTelemetry(
            "ML_ALPHA_101", "v1.1.0", live_drawdown_pct=8.0,
            live_error_rate_pct=1.0, recent_sharpe=2.3,
        )
        report = self.engine.audit_telemetry_and_rollback(self.cfg, telem)

        self.assertFalse(report.is_rollback_executed)
        self.assertEqual(report.status, "MODEL_VERSION_HEALTHY")
        self.assertEqual(report.active_version, "v1.1.0")
        self.assertFalse(report.is_serving_halted)

    def test_drawdown_breach_triggers_rollback_to_v1(self):
        telem = LivePerformanceTelemetry(
            "ML_ALPHA_101", "v1.1.0", live_drawdown_pct=18.5,
            live_error_rate_pct=1.0, recent_sharpe=0.4,
        )
        report = self.engine.audit_telemetry_and_rollback(self.cfg, telem)

        self.assertTrue(report.is_rollback_executed)
        self.assertEqual(report.status, "ROLLBACK_SUCCESSFUL")
        self.assertEqual(report.active_version, "v1.0.0")
        self.assertEqual(report.previous_version, "v1.1.0")
        self.assertEqual(report.sha256_hash, HASH_V1)
        self.assertFalse(report.is_serving_halted)
        self.assertEqual(self.engine.get_active_version("ML_ALPHA_101").version, "v1.0.0")
        self.assertEqual(
            self.engine.registry["ML_ALPHA_101"]["v1.1.0"].status, "DEACTIVATED_ROLLBACK"
        )

    def test_error_rate_breach_triggers_rollback(self):
        telem = LivePerformanceTelemetry("ML_ALPHA_101", "v1.1.0", 2.0, 6.0, 1.9)
        report = self.engine.audit_telemetry_and_rollback(self.cfg, telem)
        self.assertTrue(report.is_rollback_executed)
        self.assertEqual(report.active_version, "v1.0.0")

    def test_reading_exactly_at_the_limit_is_not_a_breach(self):
        telem = LivePerformanceTelemetry("ML_ALPHA_101", "v1.1.0", 15.0, 5.0, 1.0)
        report = self.engine.audit_telemetry_and_rollback(self.cfg, telem)
        self.assertEqual(report.status, "MODEL_VERSION_HEALTHY")

        telem_over = LivePerformanceTelemetry(
            "ML_ALPHA_101", "v1.1.0", 15.000001, 5.0, 1.0
        )
        self.assertTrue(
            self.engine.audit_telemetry_and_rollback(self.cfg, telem_over).is_rollback_executed
        )

    def test_unusable_telemetry_raises_not_healthy(self):
        # Regression: NaN > 15.0 is False under IEEE 754, so a missing-data NaN
        # used to return MODEL_VERSION_HEALTHY and silently disable the breaker.
        for drawdown in [float("nan"), float("inf"), -18.5]:
            with self.subTest(live_drawdown_pct=drawdown):
                telem = LivePerformanceTelemetry("ML_ALPHA_101", "v1.1.0", drawdown, 1.0, 0.4)
                with self.assertRaises(ModelRegistryError):
                    self.engine.audit_telemetry_and_rollback(self.cfg, telem)
        telem_nan_errors = LivePerformanceTelemetry(
            "ML_ALPHA_101", "v1.1.0", 1.0, float("nan"), 0.4
        )
        with self.assertRaises(ModelRegistryError):
            self.engine.audit_telemetry_and_rollback(self.cfg, telem_nan_errors)
        # The failed check must not have moved the pointer.
        self.assertEqual(self.engine.get_active_version("ML_ALPHA_101").version, "v1.1.0")

    def test_non_finite_config_limits_are_rejected(self):
        bad_cfg = RollbackTriggerConfig(max_allowed_drawdown_pct=float("nan"))
        telem = LivePerformanceTelemetry("ML_ALPHA_101", "v1.1.0", 18.5, 1.0, 0.4)
        with self.assertRaises(ModelRegistryError):
            self.engine.audit_telemetry_and_rollback(bad_cfg, telem)

    def test_unknown_model_or_version_raises(self):
        with self.assertRaises(ModelRegistryError):
            self.engine.audit_telemetry_and_rollback(
                self.cfg, LivePerformanceTelemetry("NO_SUCH", "v1.1.0", 1.0, 1.0, 1.0)
            )
        with self.assertRaises(ModelRegistryError):
            self.engine.audit_telemetry_and_rollback(
                self.cfg, LivePerformanceTelemetry("ML_ALPHA_101", "v9.9.9", 1.0, 1.0, 1.0)
            )

    def test_stale_telemetry_does_not_cascade_a_second_rollback(self):
        # Regression: repeating the same breaching sample used to return
        # ROLLBACK_SUCCESSFUL with is_rollback_executed=True on every call, so
        # every side effect the caller attaches to a rollback re-fired per poll.
        telem = LivePerformanceTelemetry("ML_ALPHA_101", "v1.1.0", 18.5, 1.0, 0.4)
        first = self.engine.audit_telemetry_and_rollback(self.cfg, telem)
        self.assertTrue(first.is_rollback_executed)

        second = self.engine.audit_telemetry_and_rollback(self.cfg, telem)
        self.assertFalse(second.is_rollback_executed)
        self.assertEqual(second.status, "TELEMETRY_STALE_NO_ACTION")
        self.assertEqual(second.active_version, "v1.0.0")
        self.assertEqual(self.engine.get_active_version("ML_ALPHA_101").version, "v1.0.0")
        self.assertEqual(
            len([e for e in self.engine.audit_log if e.event == "ROLLBACK"]), 1
        )

    def test_rollback_is_recorded_in_the_audit_log(self):
        telem = LivePerformanceTelemetry("ML_ALPHA_101", "v1.1.0", 18.5, 1.0, 0.4)
        self.engine.audit_telemetry_and_rollback(self.cfg, telem)
        event = [e for e in self.engine.audit_log if e.event == "ROLLBACK"][-1]
        self.assertEqual(event.version, "v1.0.0")
        self.assertIn("from=v1.1.0", event.detail)
        self.assertIn("drawdown=18.5000", event.detail)
        sequences = [e.sequence for e in self.engine.audit_log]
        self.assertEqual(sequences, sorted(sequences))


class TestRollbackTargetSelection(unittest.TestCase):

    def setUp(self):
        self.engine = ModelVersionManagerEngine()
        self.cfg = RollbackTriggerConfig(
            max_allowed_drawdown_pct=15.0, max_allowed_error_rate_pct=5.0
        )
        self.breach = LivePerformanceTelemetry("ML_ALPHA_101", "v1.1.0", 18.5, 1.0, 0.4)

    def _register_failing_active(self):
        self.engine.register_version(
            make_version("v1.1.0", sha256_hash=HASH_V2, is_active=True)
        )

    def test_staging_is_not_a_fallback_by_default(self):
        self.engine.register_version(make_version("v1.0.0", status="STAGING"))
        self._register_failing_active()
        report = self.engine.audit_telemetry_and_rollback(self.cfg, self.breach)
        self.assertEqual(report.status, "ROLLBACK_FAILED_NO_HEALTHY_VERSION")
        self.assertTrue(report.is_serving_halted)

    def test_staging_is_a_fallback_when_explicitly_allowed(self):
        self.engine.register_version(make_version("v1.0.0", status="STAGING"))
        self._register_failing_active()
        cfg = RollbackTriggerConfig(
            max_allowed_drawdown_pct=15.0, max_allowed_error_rate_pct=5.0,
            allow_staging_fallback=True,
        )
        report = self.engine.audit_telemetry_and_rollback(cfg, self.breach)
        self.assertTrue(report.is_rollback_executed)
        self.assertEqual(report.active_version, "v1.0.0")

    def test_archived_is_never_a_fallback(self):
        self.engine.register_version(make_version("v1.0.0", status="ARCHIVED"))
        self._register_failing_active()
        cfg = RollbackTriggerConfig(
            max_allowed_drawdown_pct=15.0, max_allowed_error_rate_pct=5.0,
            allow_staging_fallback=True,
        )
        report = self.engine.audit_telemetry_and_rollback(cfg, self.breach)
        self.assertEqual(report.status, "ROLLBACK_FAILED_NO_HEALTHY_VERSION")

    def test_candidate_whose_validated_drawdown_exceeds_the_limit_is_skipped(self):
        self.engine.register_version(make_version("v1.0.0", max_drawdown_pct=22.0))
        self._register_failing_active()
        with self.assertLogs("model_version_manager", level="WARNING") as logs:
            report = self.engine.audit_telemetry_and_rollback(self.cfg, self.breach)
        self.assertEqual(report.status, "ROLLBACK_FAILED_NO_HEALTHY_VERSION")
        self.assertTrue(any("max drawdown of 22.0%" in line for line in logs.output))

    def test_never_served_higher_version_is_not_a_roll_forward_target(self):
        self.engine.register_version(make_version("v1.0.0"))
        self._register_failing_active()
        self.engine.register_version(
            make_version("v1.2.0", sha256_hash=HASH_V3, registered_at_epoch=1760000000.0)
        )
        report = self.engine.audit_telemetry_and_rollback(self.cfg, self.breach)
        self.assertTrue(report.is_rollback_executed)
        self.assertEqual(report.active_version, "v1.0.0")

    def test_a_version_that_actually_served_outranks_one_that_never_did(self):
        # v1.0.9 served and was superseded; v1.0.5 was only ever registered.
        self.engine.register_version(make_version("v1.0.9", is_active=True))
        self.engine.register_version(
            make_version("v1.0.5", sha256_hash=HASH_V3, registered_at_epoch=1745000000.0)
        )
        self._register_failing_active()
        self.engine.promote_version("ML_ALPHA_101", "v1.1.0")
        report = self.engine.audit_telemetry_and_rollback(self.cfg, self.breach)
        self.assertEqual(report.active_version, "v1.0.9")

    def test_ties_break_on_semver_precedence_not_string_order(self):
        # Neither candidate has ever served; "v1.9.0" > "v1.10.0" as strings.
        self.engine.register_version(make_version("v1.9.0"))
        self.engine.register_version(make_version("v1.10.0", sha256_hash=HASH_V3))
        self.engine.register_version(
            make_version("v1.11.0", sha256_hash=HASH_V2, is_active=True)
        )
        breach = LivePerformanceTelemetry("ML_ALPHA_101", "v1.11.0", 18.5, 1.0, 0.4)
        report = self.engine.audit_telemetry_and_rollback(self.cfg, breach)
        self.assertEqual(report.active_version, "v1.10.0")

    def test_selection_is_deterministic_across_identical_runs(self):
        def build():
            engine = ModelVersionManagerEngine()
            for version, digest in (("v1.0.0", HASH_V1), ("v1.0.1", HASH_V3)):
                engine.register_version(make_version(version, sha256_hash=digest))
            engine.register_version(
                make_version("v1.1.0", sha256_hash=HASH_V2, is_active=True)
            )
            return engine.audit_telemetry_and_rollback(self.cfg, self.breach)

        self.assertEqual(build(), build())


class TestNoFallbackPolicy(unittest.TestCase):

    def setUp(self):
        self.engine = ModelVersionManagerEngine()
        self.engine.register_version(
            make_version("v1.1.0", sha256_hash=HASH_V2, is_active=True)
        )
        self.breach = LivePerformanceTelemetry("ML_ALPHA_101", "v1.1.0", 18.5, 1.0, 0.4)

    def test_default_is_fail_safe_halt(self):
        # Regression: the previous implementation deactivated the failing
        # version and then reported it as still active, so the registry and the
        # report disagreed about what was serving.
        cfg = RollbackTriggerConfig()
        report = self.engine.audit_telemetry_and_rollback(cfg, self.breach)

        self.assertEqual(report.status, "ROLLBACK_FAILED_NO_HEALTHY_VERSION")
        self.assertFalse(report.is_rollback_executed)
        self.assertTrue(report.is_serving_halted)
        self.assertIsNone(report.active_version)
        self.assertEqual(report.previous_version, "v1.1.0")
        self.assertIsNone(self.engine.get_active_version("ML_ALPHA_101"))
        self.assertEqual(
            self.engine.registry["ML_ALPHA_101"]["v1.1.0"].status, "DEACTIVATED_ROLLBACK"
        )
        self.assertEqual([e.event for e in self.engine.audit_log if e.event == "HALT"], ["HALT"])

    def test_continuing_to_serve_requires_an_explicit_opt_out(self):
        cfg = RollbackTriggerConfig(halt_on_missing_rollback_target=False)
        with self.assertLogs("model_version_manager", level="CRITICAL") as logs:
            report = self.engine.audit_telemetry_and_rollback(cfg, self.breach)

        self.assertEqual(report.status, "ROLLBACK_FAILED_NO_HEALTHY_VERSION")
        self.assertFalse(report.is_serving_halted)
        self.assertEqual(report.active_version, "v1.1.0")
        self.assertEqual(self.engine.get_active_version("ML_ALPHA_101").version, "v1.1.0")
        self.assertEqual(
            self.engine.registry["ML_ALPHA_101"]["v1.1.0"].status, "PRODUCTION"
        )
        self.assertTrue(any("REMAINS ACTIVE" in line for line in logs.output))
        # No rollback happened, so no ROLLBACK event may claim one.
        events = [e.event for e in self.engine.audit_log]
        self.assertIn("ROLLBACK_FAILED", events)
        self.assertNotIn("ROLLBACK", events)


class TestConcurrency(unittest.TestCase):

    def test_concurrent_registration_leaves_a_consistent_registry(self):
        engine = ModelVersionManagerEngine()
        errors = []

        def register(i):
            try:
                engine.register_version(
                    make_version(
                        f"v1.0.{i}",
                        sha256_hash=ModelVersionManagerEngine.compute_sha256(
                            f"artifact-{i}".encode()
                        ),
                        is_active=(i == 0),
                    )
                )
            except Exception as exc:  # noqa: BLE001 - recorded, then re-raised by the assert
                errors.append(exc)

        threads = [threading.Thread(target=register, args=(i,)) for i in range(24)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(engine.registry["ML_ALPHA_101"]), 24)
        actives = [v.version for v in engine.registry["ML_ALPHA_101"].values() if v.is_active]
        self.assertEqual(actives, ["v1.0.0"])
        sequences = [e.sequence for e in engine.audit_log]
        self.assertEqual(len(sequences), len(set(sequences)))


if __name__ == '__main__':
    unittest.main()
