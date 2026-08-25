import hashlib
import logging
import threading
import unittest
from datetime import datetime, timedelta, timezone

from override_access_control import (
    APPROVAL_BREAK_GLASS,
    APPROVAL_DUAL_SIGN_OFF,
    APPROVAL_SINGLE_OPERATOR,
    REJECT_BREAK_GLASS_INVALID,
    REJECT_BREAK_GLASS_NOT_CONFIGURED,
    REJECT_DUAL_SIGN_OFF_REQUIRED,
    REJECT_DUPLICATE_REQUEST_ID,
    REJECT_INVALID_FIELD,
    REJECT_INVALID_TTL,
    REJECT_MISSING_JUSTIFICATION,
    REJECT_SECONDARY_ROLE_UNAUTHORIZED,
    REJECT_SELF_APPROVAL,
    REJECT_UNAUTHORIZED_ROLE,
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    BreakGlassToken,
    BreakGlassTokenRegistry,
    EmergencyOverrideAccessEngine,
    OverrideAccessError,
    OverrideControlReport,
    OverridePolicy,
    OverrideRequest,
    compute_record_hash,
    verify_audit_chain,
)

T0 = datetime(2026, 3, 2, 13, 45, 0, tzinfo=timezone.utc)


def setUpModule():
    # The engine deliberately logs every decision at ERROR/CRITICAL; silence it
    # so a passing run is readable.
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


class FrozenClock:
    """Deterministic injectable clock; audit hashes bind a timestamp."""

    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now = self.now + timedelta(**kwargs)


def make_request(**overrides) -> OverrideRequest:
    base = dict(
        request_id="REQ_OVERRIDE_01",
        target_system_id="ALL_ALGOS",
        action_type="KILL_SWITCH_ALL_ALGOS",
        primary_operator_id="USR_RISK_01",
        primary_operator_role="RISK_OFFICER",
        justification_reason="Flash crash in tech sector; halting all execution engines.",
        secondary_operator_id="USR_TRADER_01",
        secondary_operator_role="HEAD_TRADER",
    )
    base.update(overrides)
    return OverrideRequest(**base)


class TestAuthorizationDecisions(unittest.TestCase):
    def setUp(self):
        self.clock = FrozenClock()
        self.engine = EmergencyOverrideAccessEngine(clock=self.clock)

    def test_critical_kill_switch_dual_signoff_approved(self):
        report = self.engine.process_override_request(make_request())
        self.assertTrue(report.is_approved)
        self.assertEqual(report.severity_level, SEVERITY_CRITICAL)
        self.assertEqual(report.approval_mode, APPROVAL_DUAL_SIGN_OFF)
        self.assertEqual(len(report.audit_hash_sha256), 64)
        self.assertEqual(report.decision_timestamp_utc, "2026-03-02T13:45:00.000+00:00")
        self.assertEqual(report.expires_at_utc, "2026-03-02T14:45:00.000+00:00")
        self.assertFalse(report.break_glass_used)
        self.assertFalse(report.post_incident_review_required)

    def test_critical_kill_switch_without_dual_signoff_rejected(self):
        report = self.engine.process_override_request(
            make_request(secondary_operator_id=None, secondary_operator_role=None)
        )
        self.assertFalse(report.is_approved)
        self.assertEqual(report.rejection_code, REJECT_DUAL_SIGN_OFF_REQUIRED)
        self.assertEqual(report.ttl_minutes, 0)
        self.assertIsNone(report.expires_at_utc)

    def test_non_critical_action_is_high_severity_and_needs_no_second_approver(self):
        report = self.engine.process_override_request(
            make_request(
                request_id="REQ_HALT_01",
                action_type="HALT_STRATEGY",
                target_system_id="STRATEGY_STAT_ARB_01",
                secondary_operator_id=None,
                secondary_operator_role=None,
            )
        )
        self.assertTrue(report.is_approved)
        self.assertEqual(report.severity_level, SEVERITY_HIGH)
        self.assertEqual(report.approval_mode, APPROVAL_SINGLE_OPERATOR)

    def test_unknown_action_is_not_promoted_to_critical(self):
        report = self.engine.process_override_request(
            make_request(request_id="REQ_UNKNOWN", action_type="KILL_SWITCH_EVERYTHING_NOW",
                         secondary_operator_id=None, secondary_operator_role=None)
        )
        self.assertEqual(report.severity_level, SEVERITY_HIGH)

    def test_unauthorized_primary_role_rejected(self):
        report = self.engine.process_override_request(
            make_request(primary_operator_id="USR_DEV_07", primary_operator_role="JUNIOR_DEVELOPER")
        )
        self.assertFalse(report.is_approved)
        self.assertEqual(report.rejection_code, REJECT_UNAUTHORIZED_ROLE)

    def test_role_matching_is_case_and_whitespace_insensitive(self):
        report = self.engine.process_override_request(
            make_request(primary_operator_role=" risk_officer ")
        )
        self.assertTrue(report.is_approved)

    def test_self_approval_rejected_even_with_different_casing(self):
        # Regression: the 1.0.0 check was a raw `!=`, so 'usr_risk_01' passed
        # as a second approver for 'USR_RISK_01'.
        report = self.engine.process_override_request(
            make_request(secondary_operator_id="usr_risk_01 ", secondary_operator_role="CTO")
        )
        self.assertFalse(report.is_approved)
        self.assertEqual(report.rejection_code, REJECT_SELF_APPROVAL)

    def test_secondary_with_unauthorized_role_rejected(self):
        report = self.engine.process_override_request(
            make_request(secondary_operator_id="USR_INTERN_01", secondary_operator_role="INTERN")
        )
        self.assertFalse(report.is_approved)
        self.assertEqual(report.rejection_code, REJECT_SECONDARY_ROLE_UNAUTHORIZED)

    def test_secondary_identity_without_role_rejected_not_ignored(self):
        report = self.engine.process_override_request(
            make_request(secondary_operator_role=None)
        )
        self.assertFalse(report.is_approved)
        self.assertEqual(report.rejection_code, REJECT_SECONDARY_ROLE_UNAUTHORIZED)

    def test_critical_approver_role_restriction(self):
        policy = OverridePolicy(critical_approver_roles=frozenset({"RISK_OFFICER", "CTO"}))
        engine = EmergencyOverrideAccessEngine(policy=policy, clock=self.clock)
        report = engine.process_override_request(
            make_request(primary_operator_id="USR_TRADER_09", primary_operator_role="HEAD_TRADER",
                         secondary_operator_id="USR_RISK_01", secondary_operator_role="RISK_OFFICER")
        )
        self.assertFalse(report.is_approved)
        self.assertEqual(report.rejection_code, REJECT_UNAUTHORIZED_ROLE)


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.clock = FrozenClock()
        self.engine = EmergencyOverrideAccessEngine(clock=self.clock)

    def test_blank_required_fields_rejected(self):
        for field_name in (
            "request_id", "target_system_id", "action_type",
            "primary_operator_id", "primary_operator_role",
        ):
            with self.subTest(field=field_name):
                report = self.engine.process_override_request(make_request(**{field_name: "   "}))
                self.assertFalse(report.is_approved)
                self.assertEqual(report.rejection_code, REJECT_INVALID_FIELD)

    def test_none_role_does_not_raise(self):
        # Regression: 1.0.0 called .upper() on the role and raised AttributeError.
        report = self.engine.process_override_request(make_request(primary_operator_role=None))
        self.assertFalse(report.is_approved)
        self.assertEqual(report.rejection_code, REJECT_INVALID_FIELD)

    def test_short_justification_rejected(self):
        report = self.engine.process_override_request(make_request(justification_reason="oops"))
        self.assertFalse(report.is_approved)
        self.assertEqual(report.rejection_code, REJECT_MISSING_JUSTIFICATION)

    def test_whitespace_only_justification_rejected(self):
        report = self.engine.process_override_request(make_request(justification_reason=" " * 40))
        self.assertFalse(report.is_approved)
        self.assertEqual(report.rejection_code, REJECT_MISSING_JUSTIFICATION)

    def test_ttl_bounds_enforced(self):
        # Regression: 1.0.0 accepted 0, negative and unbounded TTLs, so an
        # override could be left in force indefinitely.
        for bad_ttl in (0, -30, 61, 100000):
            with self.subTest(ttl=bad_ttl):
                report = self.engine.process_override_request(
                    make_request(request_id=f"REQ_TTL_{bad_ttl}", ttl_minutes=bad_ttl)
                )
                self.assertFalse(report.is_approved)
                self.assertEqual(report.rejection_code, REJECT_INVALID_TTL)

    def test_non_integer_ttl_rejected(self):
        for bad_ttl in (30.5, "30", True):
            with self.subTest(ttl=bad_ttl):
                report = self.engine.process_override_request(
                    make_request(request_id=f"REQ_TTL_{bad_ttl}", ttl_minutes=bad_ttl)
                )
                self.assertFalse(report.is_approved)
                self.assertEqual(report.rejection_code, REJECT_INVALID_TTL)

    def test_ttl_boundaries_accepted(self):
        for good_ttl in (1, 60):
            with self.subTest(ttl=good_ttl):
                report = self.engine.process_override_request(
                    make_request(request_id=f"REQ_OK_{good_ttl}", ttl_minutes=good_ttl)
                )
                self.assertTrue(report.is_approved)

    def test_policy_rejects_invalid_configuration(self):
        with self.assertRaises(OverrideAccessError):
            OverridePolicy(authorized_roles=frozenset())
        with self.assertRaises(OverrideAccessError):
            OverridePolicy(max_ttl_minutes=0)
        with self.assertRaises(OverrideAccessError):
            OverridePolicy(min_justification_chars=0)
        with self.assertRaises(OverrideAccessError):
            OverridePolicy(critical_approver_roles=frozenset({"NOT_AN_AUTHORIZED_ROLE"}))


class TestBreakGlass(unittest.TestCase):
    def setUp(self):
        self.clock = FrozenClock()
        self.secret = "brk-glass-secret-4f2a9c7e"
        self.registry = BreakGlassTokenRegistry(
            [
                BreakGlassToken.from_secret(
                    token_id="BG_001",
                    secret=self.secret,
                    expires_at=T0 + timedelta(hours=4),
                    issued_to_operator_id="USR_RISK_01",
                )
            ]
        )
        self.engine = EmergencyOverrideAccessEngine(
            break_glass_registry=self.registry, clock=self.clock
        )

    def _break_glass_request(self, **overrides):
        kwargs = dict(
            secondary_operator_id=None,
            secondary_operator_role=None,
            break_glass_token=self.secret,
        )
        kwargs.update(overrides)
        return make_request(**kwargs)

    def test_arbitrary_token_string_no_longer_bypasses_dual_signoff(self):
        # Regression: 1.0.0 accepted ANY token of length >= 8, so 'aaaaaaaa'
        # unlocked a firm-wide kill switch with one operator.
        report = self.engine.process_override_request(
            self._break_glass_request(break_glass_token="aaaaaaaa")
        )
        self.assertFalse(report.is_approved)
        self.assertEqual(report.rejection_code, REJECT_BREAK_GLASS_INVALID)

    def test_valid_token_approves_and_flags_post_incident_review(self):
        report = self.engine.process_override_request(self._break_glass_request())
        self.assertTrue(report.is_approved)
        self.assertEqual(report.approval_mode, APPROVAL_BREAK_GLASS)
        self.assertTrue(report.break_glass_used)
        self.assertTrue(report.post_incident_review_required)

    def test_token_is_single_use(self):
        first = self.engine.process_override_request(self._break_glass_request())
        self.assertTrue(first.is_approved)
        self.assertTrue(self.registry.is_consumed("BG_001"))
        second = self.engine.process_override_request(
            self._break_glass_request(request_id="REQ_OVERRIDE_02")
        )
        self.assertFalse(second.is_approved)
        self.assertEqual(second.rejection_code, REJECT_BREAK_GLASS_INVALID)

    def test_expired_token_rejected(self):
        self.clock.advance(hours=5)
        report = self.engine.process_override_request(self._break_glass_request())
        self.assertFalse(report.is_approved)
        self.assertEqual(report.rejection_code, REJECT_BREAK_GLASS_INVALID)

    def test_token_bound_to_a_different_operator_rejected(self):
        report = self.engine.process_override_request(
            self._break_glass_request(
                primary_operator_id="USR_TRADER_02", primary_operator_role="HEAD_TRADER"
            )
        )
        self.assertFalse(report.is_approved)
        self.assertEqual(report.rejection_code, REJECT_BREAK_GLASS_INVALID)

    def test_failed_request_does_not_burn_the_token(self):
        rejected = self.engine.process_override_request(
            self._break_glass_request(justification_reason="short")
        )
        self.assertFalse(rejected.is_approved)
        self.assertFalse(self.registry.is_consumed("BG_001"))
        approved = self.engine.process_override_request(
            self._break_glass_request(request_id="REQ_RETRY")
        )
        self.assertTrue(approved.is_approved)

    def test_break_glass_unavailable_when_no_registry_configured(self):
        engine = EmergencyOverrideAccessEngine(clock=self.clock)
        report = engine.process_override_request(self._break_glass_request())
        self.assertFalse(report.is_approved)
        self.assertEqual(report.rejection_code, REJECT_BREAK_GLASS_NOT_CONFIGURED)

    def test_registry_stores_only_the_digest(self):
        token = BreakGlassToken.from_secret("BG_002", self.secret, T0 + timedelta(hours=1))
        self.assertNotIn(self.secret, token.secret_sha256)
        self.assertEqual(
            token.secret_sha256, hashlib.sha256(self.secret.encode("utf-8")).hexdigest()
        )

    def test_weak_secret_rejected_at_issue_time(self):
        with self.assertRaises(OverrideAccessError):
            BreakGlassToken.from_secret("BG_003", "short", T0 + timedelta(hours=1))
        with self.assertRaises(OverrideAccessError):
            BreakGlassToken.from_secret(
                "BG_004", "a-perfectly-long-secret", datetime(2026, 3, 2, 14, 0)
            )

    def test_duplicate_token_id_rejected(self):
        with self.assertRaises(OverrideAccessError):
            self.registry.issue(
                BreakGlassToken.from_secret("BG_001", "another-long-secret-value",
                                            T0 + timedelta(hours=1))
            )


class TestIdempotencyAndReplay(unittest.TestCase):
    def setUp(self):
        self.clock = FrozenClock()
        self.engine = EmergencyOverrideAccessEngine(clock=self.clock)

    def test_identical_resubmission_returns_original_decision(self):
        first = self.engine.process_override_request(make_request())
        self.clock.advance(minutes=5)
        second = self.engine.process_override_request(make_request())
        self.assertEqual(first.audit_hash_sha256, second.audit_hash_sha256)
        self.assertEqual(first.decision_timestamp_utc, second.decision_timestamp_utc)
        self.assertEqual(len(self.engine.audit_chain), 1)

    def test_reused_request_id_with_different_payload_rejected(self):
        # Regression: 1.0.0 silently overwrote active_overrides[request_id].
        self.engine.process_override_request(make_request())
        report = self.engine.process_override_request(
            make_request(target_system_id="STRATEGY_STAT_ARB_01")
        )
        self.assertFalse(report.is_approved)
        self.assertEqual(report.rejection_code, REJECT_DUPLICATE_REQUEST_ID)
        self.assertEqual(
            self.engine.active_overrides["REQ_OVERRIDE_01"].request.target_system_id, "ALL_ALGOS"
        )

    def test_denied_request_id_may_be_corrected_and_resubmitted(self):
        denied = self.engine.process_override_request(make_request(justification_reason="x"))
        self.assertFalse(denied.is_approved)
        approved = self.engine.process_override_request(make_request())
        self.assertTrue(approved.is_approved)


class TestLifecycle(unittest.TestCase):
    def setUp(self):
        self.clock = FrozenClock()
        self.engine = EmergencyOverrideAccessEngine(clock=self.clock)
        self.report = self.engine.process_override_request(make_request(ttl_minutes=30))

    def test_override_is_active_until_ttl_elapses(self):
        self.assertTrue(self.engine.is_override_active("REQ_OVERRIDE_01"))
        self.clock.advance(minutes=29)
        self.assertTrue(self.engine.is_override_active("REQ_OVERRIDE_01"))
        self.assertEqual(len(self.engine.list_active_overrides()), 1)

    def test_override_is_not_active_at_the_expiry_instant(self):
        self.clock.advance(minutes=30)
        self.assertFalse(self.engine.is_override_active("REQ_OVERRIDE_01"))
        self.assertEqual(self.engine.list_active_overrides(), [])

    def test_expire_due_overrides_removes_and_returns_them(self):
        # Regression: 1.0.0 documented TTL auto-expiry but never expired anything.
        self.assertEqual(self.engine.expire_due_overrides(), [])
        self.clock.advance(minutes=31)
        expired = self.engine.expire_due_overrides()
        self.assertEqual(len(expired), 1)
        self.assertEqual(expired[0].report.request_id, "REQ_OVERRIDE_01")
        self.assertNotIn("REQ_OVERRIDE_01", self.engine.active_overrides)
        self.assertEqual(self.engine.expire_due_overrides(), [])

    def test_revoke_override(self):
        active = self.engine.revoke_override("REQ_OVERRIDE_01", revoked_by="USR_CTO_01")
        self.assertIsNotNone(active)
        self.assertEqual(active.revoked_by, "USR_CTO_01")
        self.assertEqual(active.revoked_at, self.clock.now)
        self.assertFalse(self.engine.is_override_active("REQ_OVERRIDE_01"))
        self.assertIsNone(self.engine.revoke_override("REQ_OVERRIDE_01", revoked_by="USR_CTO_01"))

    def test_rejected_request_never_becomes_active(self):
        self.engine.process_override_request(
            make_request(request_id="REQ_BAD", secondary_operator_id=None,
                         secondary_operator_role=None)
        )
        self.assertNotIn("REQ_BAD", self.engine.active_overrides)


class TestConcurrency(unittest.TestCase):
    def test_racing_operators_produce_exactly_one_approval(self):
        # Two consoles submitting the same request_id must not both fire the
        # kill switch: the duplicate check and the state write must be atomic.
        engine = EmergencyOverrideAccessEngine(clock=FrozenClock())
        barrier = threading.Barrier(8)
        results = []
        lock = threading.Lock()

        def submit():
            barrier.wait()
            report = engine.process_override_request(make_request())
            with lock:
                results.append(report)

        threads = [threading.Thread(target=submit) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        approvals = {r.audit_hash_sha256 for r in results if r.is_approved}
        self.assertEqual(len(results), 8)
        self.assertEqual(len(approvals), 1)
        self.assertEqual(len(engine.audit_chain), 1)
        self.assertTrue(engine.verify_audit_chain()[0])


class TestAuditTrail(unittest.TestCase):
    def setUp(self):
        self.clock = FrozenClock()
        self.engine = EmergencyOverrideAccessEngine(clock=self.clock)

    def test_denials_are_recorded_in_the_chain(self):
        # Regression: 1.0.0 returned audit_hash="" for every rejection, so a
        # denied firm-wide kill-switch attempt left no hashed evidence.
        denied = self.engine.process_override_request(
            make_request(secondary_operator_id=None, secondary_operator_role=None)
        )
        self.assertEqual(len(denied.audit_hash_sha256), 64)
        self.assertEqual(len(self.engine.audit_chain), 1)
        self.assertEqual(denied.audit_chain_index, 0)

    def test_chain_links_successive_records(self):
        first = self.engine.process_override_request(make_request(request_id="REQ_A"))
        self.clock.advance(minutes=1)
        second = self.engine.process_override_request(make_request(request_id="REQ_B"))
        self.assertEqual(first.previous_audit_hash, "")
        self.assertEqual(second.previous_audit_hash, first.audit_hash_sha256)
        self.assertEqual(second.audit_chain_index, 1)
        intact, broken = self.engine.verify_audit_chain()
        self.assertTrue(intact)
        self.assertIsNone(broken)

    def test_hash_is_reproducible_from_the_published_timestamp(self):
        # Regression: 1.0.0 hashed time.time() but never published it, so the
        # audit hash could not be independently recomputed by anyone.
        report = self.engine.process_override_request(make_request())
        request, stored = self.engine.audit_chain[0]
        self.assertEqual(compute_record_hash(request, stored), report.audit_hash_sha256)

    def test_editing_the_secondary_approver_breaks_the_chain(self):
        # Regression: the 1.0.0 payload omitted the secondary approver, so the
        # record of WHO co-signed could be rewritten with the hash still valid.
        self.engine.process_override_request(make_request())
        request, _ = self.engine.audit_chain[0]
        request.secondary_operator_id = "USR_SOMEONE_ELSE"
        intact, broken = self.engine.verify_audit_chain()
        self.assertFalse(intact)
        self.assertEqual(broken, 0)

    def test_editing_the_ttl_breaks_the_chain(self):
        self.engine.process_override_request(make_request(ttl_minutes=15))
        request, _ = self.engine.audit_chain[0]
        request.ttl_minutes = 60
        self.assertFalse(self.engine.verify_audit_chain()[0])

    def test_deleting_a_middle_record_breaks_the_chain(self):
        self.engine.process_override_request(make_request(request_id="REQ_A"))
        self.clock.advance(minutes=1)
        self.engine.process_override_request(make_request(request_id="REQ_B"))
        self.clock.advance(minutes=1)
        self.engine.process_override_request(make_request(request_id="REQ_C"))
        tampered = [self.engine.audit_chain[0], self.engine.audit_chain[2]]
        intact, broken = verify_audit_chain(tampered)
        self.assertFalse(intact)
        self.assertEqual(broken, 1)

    def test_field_boundary_attack_does_not_collide(self):
        # Length-prefixed encoding: moving a delimiter between adjacent fields
        # must change the hash.
        a = make_request(request_id="REQ_A", primary_operator_id="USR|X",
                         target_system_id="ALL_ALGOS")
        b = make_request(request_id="REQ_A", primary_operator_id="USR",
                         target_system_id="X|ALL_ALGOS")
        self.assertNotEqual(
            self.engine.compute_audit_hash(a, T0), self.engine.compute_audit_hash(b, T0)
        )

    def test_hmac_keying_changes_the_digest_and_labels_the_algorithm(self):
        keyed = EmergencyOverrideAccessEngine(audit_hmac_key=b"unit-test-key", clock=FrozenClock())
        unkeyed_report = self.engine.process_override_request(make_request())
        keyed_report = keyed.process_override_request(make_request())
        self.assertEqual(unkeyed_report.hash_algorithm, "sha256")
        self.assertEqual(keyed_report.hash_algorithm, "hmac-sha256")
        self.assertNotEqual(unkeyed_report.audit_hash_sha256, keyed_report.audit_hash_sha256)
        self.assertTrue(keyed.verify_audit_chain()[0])
        # Verifying a keyed chain without the key must fail, not silently pass.
        self.assertFalse(verify_audit_chain(list(keyed.audit_chain))[0])

    def test_naive_timestamp_is_rejected(self):
        with self.assertRaises(OverrideAccessError):
            self.engine.compute_audit_hash(make_request(), datetime(2026, 3, 2, 13, 45))

    def test_report_is_constructible_from_the_original_nine_fields(self):
        # Backwards compatibility for callers building reports positionally.
        report = OverrideControlReport(
            "REQ", "ALL_ALGOS", "KILL_SWITCH_ALL_ALGOS", SEVERITY_CRITICAL, True,
            "0" * 64, 60, None, "summary",
        )
        self.assertEqual(report.rejection_code, None)
        self.assertEqual(report.hash_algorithm, "sha256")


if __name__ == "__main__":
    unittest.main()
