import base64
import concurrent.futures
import logging
import unittest

from phishing_resistant_authentication_for_custody_access import (
    AuthPolicyConfig,
    AuthVerificationReport,
    IssuedChallenge,
    PhishingResistantAuthenticationForCustodyAccessConfig,
    PhishingResistantAuthenticationForCustodyAccessEngine,
    PhishingResistantAuthError,
    RegisteredCredential,
    WebAuthnAssertion,
    compute_rp_id_hash,
)

T0 = 1_700_000_000.0
RP_ID = "custody.firm.com"
ORIGIN = "https://custody.firm.com"
PHISHED_ORIGIN = "https://cust0dy-firm.com"
USER = "custody_admin_1"
CREDENTIAL = "cred_yubikey_5c_001"


def setUpModule():
    # The engine logs every rejection; silence it so the shared runner's output
    # stays readable. Scoped to this module, not disabled globally.
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


class WebAuthnTestBase(unittest.TestCase):
    """One registered hardware credential for one custody administrator."""

    def build_engine(self, policy=None, enabled=True):
        engine = PhishingResistantAuthenticationForCustodyAccessEngine(
            PhishingResistantAuthenticationForCustodyAccessConfig(enabled=enabled),
            policy or AuthPolicyConfig(rp_id=RP_ID, allowed_origins=(ORIGIN,)),
        )
        engine.register_credential(
            credential_id=CREDENTIAL, user_id=USER, sign_count=10, aaguid="yubico-5c"
        )
        return engine

    def make_assertion(self, challenge, **overrides):
        params = dict(
            user_id=USER,
            credential_id=CREDENTIAL,
            client_origin=ORIGIN,
            challenge=challenge,
            rp_id_hash=compute_rp_id_hash(RP_ID),
            user_present=True,
            user_verified=True,
            signature_verified=True,
            sign_count=11,
            aaguid="yubico-5c",
        )
        params.update(overrides)
        return WebAuthnAssertion(**params)

    def authenticate(self, engine, now=T0, issue_at=T0, user_id=USER, **overrides):
        """Issues a challenge and immediately verifies an assertion against it."""
        issued = engine.issue_challenge(user_id, now=issue_at)
        assertion = self.make_assertion(issued.value, **overrides)
        return engine.verify_assertion(assertion, now=now)


class TestLegacyInterface(WebAuthnTestBase):

    def test_execute_true_legacy(self):
        engine = PhishingResistantAuthenticationForCustodyAccessEngine(
            PhishingResistantAuthenticationForCustodyAccessConfig(enabled=True)
        )
        self.assertTrue(engine.execute())

    def test_execute_false_legacy(self):
        engine = PhishingResistantAuthenticationForCustodyAccessEngine(
            PhishingResistantAuthenticationForCustodyAccessConfig(enabled=False)
        )
        self.assertFalse(engine.execute())

    def test_disabled_engine_authenticates_nobody(self):
        engine = self.build_engine(enabled=False)
        report = self.authenticate(engine)
        self.assertEqual(report.status, "ENGINE_DISABLED")
        self.assertFalse(report.is_authenticated)


class TestHappyPath(WebAuthnTestBase):

    def test_valid_webauthn_authentication(self):
        engine = self.build_engine()
        report = self.authenticate(engine)
        self.assertEqual(report.status, "AUTH_SUCCESSFUL")
        self.assertTrue(report.is_authenticated)
        self.assertTrue(report.is_origin_valid)
        self.assertTrue(report.is_user_verified)
        self.assertEqual(report.previous_sign_count, 10)
        self.assertEqual(report.sign_count, 11)
        self.assertEqual(report.warnings, ())

    def test_success_advances_stored_sign_count(self):
        engine = self.build_engine()
        self.authenticate(engine)
        self.assertEqual(engine.get_credential(CREDENTIAL).sign_count, 11)

    def test_every_returned_status_is_declared(self):
        """A status string absent from STATUSES is invisible to downstream alerting."""
        engine = self.build_engine()
        statuses = {
            self.authenticate(engine).status,
            self.authenticate(engine, client_origin=PHISHED_ORIGIN).status,
            self.authenticate(engine, user_present=False).status,
            self.authenticate(engine, user_verified=False).status,
            self.authenticate(engine, signature_verified=False).status,
            self.authenticate(engine, credential_id="unknown_cred").status,
            self.authenticate(engine, now=T0 + 3600).status,
        }
        declared = set(PhishingResistantAuthenticationForCustodyAccessEngine.STATUSES)
        self.assertTrue(statuses.issubset(declared), statuses - declared)


class TestOriginBinding(WebAuthnTestBase):
    """The checks an adversary-in-the-middle proxy cannot satisfy."""

    def test_origin_mismatch_phishing_rejection(self):
        engine = self.build_engine()
        # Simulated Evilginx2-style reverse-proxy origin, everything else valid.
        report = self.authenticate(engine, client_origin=PHISHED_ORIGIN)
        self.assertEqual(report.status, "ORIGIN_MISMATCH_PHISHING_ATTEMPT")
        self.assertFalse(report.is_origin_valid)
        self.assertFalse(report.is_authenticated)

    def test_rejection_never_reports_claimed_user_verification_as_established(self):
        """An assertion claiming UV=1 that is rejected has verified nobody.

        Reporting the claim back as ``is_user_verified=True`` would put an
        attacker-supplied flag into the field an auditor reads as fact.
        """
        engine = self.build_engine()
        report = self.authenticate(engine, client_origin=PHISHED_ORIGIN, user_verified=True)
        self.assertFalse(report.is_user_verified)

    def test_subdomain_of_rp_id_is_not_accepted_by_default(self):
        """RP ID suffix matching is not origin matching; the allowlist is exact."""
        engine = self.build_engine()
        report = self.authenticate(engine, client_origin="https://evil.custody.firm.com")
        self.assertEqual(report.status, "ORIGIN_MISMATCH_PHISHING_ATTEMPT")

    def test_multi_origin_deployment_accepts_each_configured_origin(self):
        policy = AuthPolicyConfig(
            rp_id=RP_ID, allowed_origins=(ORIGIN, "https://login.custody.firm.com")
        )
        engine = self.build_engine(policy=policy)
        report = self.authenticate(engine, client_origin="https://login.custody.firm.com")
        self.assertEqual(report.status, "AUTH_SUCCESSFUL")

    def test_rp_id_hash_mismatch_rejected(self):
        engine = self.build_engine()
        report = self.authenticate(engine, rp_id_hash=compute_rp_id_hash("attacker.example"))
        self.assertEqual(report.status, "RP_ID_HASH_MISMATCH")
        self.assertFalse(report.is_authenticated)

    def test_absent_rp_id_hash_fails_closed_by_default(self):
        engine = self.build_engine()
        report = self.authenticate(engine, rp_id_hash=None)
        self.assertEqual(report.status, "RP_ID_HASH_MISMATCH")

    def test_absent_rp_id_hash_allowed_when_policy_relaxed(self):
        policy = AuthPolicyConfig(
            rp_id=RP_ID, allowed_origins=(ORIGIN,), require_rp_id_hash=False
        )
        engine = self.build_engine(policy=policy)
        report = self.authenticate(engine, rp_id_hash=None)
        self.assertEqual(report.status, "AUTH_SUCCESSFUL")

    def test_rp_id_hash_is_sha256_of_rp_id(self):
        """Fixed vector, so a change to the hashed input cannot pass silently.

        SHA-256("custody.firm.com"), derived outside this module.
        """
        expected = bytes.fromhex(
            "47dc04a9179ef041a2d32dcb6a2122a0c75dd84629c498f59de670f636a2cde9"
        )
        self.assertEqual(compute_rp_id_hash(RP_ID), expected)
        self.assertNotEqual(compute_rp_id_hash("cust0dy-firm.com"), expected)


class TestChallengeHandling(WebAuthnTestBase):
    """Replay protection: the property version 1.x did not actually have."""

    def test_challenge_cannot_be_replayed(self):
        engine = self.build_engine()
        issued = engine.issue_challenge(USER, now=T0)
        first = engine.verify_assertion(self.make_assertion(issued.value), now=T0)
        second = engine.verify_assertion(
            self.make_assertion(issued.value, sign_count=12), now=T0 + 1
        )
        self.assertEqual(first.status, "AUTH_SUCCESSFUL")
        self.assertEqual(second.status, "CHALLENGE_UNKNOWN")

    def test_unissued_challenge_rejected(self):
        engine = self.build_engine()
        report = engine.verify_assertion(
            self.make_assertion("attacker-invented-challenge"), now=T0
        )
        self.assertEqual(report.status, "CHALLENGE_UNKNOWN")

    def test_challenge_is_consumed_even_when_the_assertion_is_rejected(self):
        """Otherwise one captured challenge could be probed until something passes."""
        engine = self.build_engine()
        issued = engine.issue_challenge(USER, now=T0)
        engine.verify_assertion(
            self.make_assertion(issued.value, client_origin=PHISHED_ORIGIN), now=T0
        )
        retry = engine.verify_assertion(self.make_assertion(issued.value), now=T0)
        self.assertEqual(retry.status, "CHALLENGE_UNKNOWN")

    def test_challenge_is_not_transferable_between_users(self):
        engine = self.build_engine()
        engine.register_credential("cred_other", "custody_admin_2")
        issued = engine.issue_challenge("custody_admin_2", now=T0)
        report = engine.verify_assertion(self.make_assertion(issued.value), now=T0)
        self.assertEqual(report.status, "CHALLENGE_USER_MISMATCH")

    def test_expired_challenge_rejected(self):
        engine = self.build_engine()
        report = self.authenticate(engine, issue_at=T0, now=T0 + 61.0)
        self.assertEqual(report.status, "CHALLENGE_EXPIRED")

    def test_challenge_at_exact_max_age_is_still_accepted(self):
        engine = self.build_engine()
        report = self.authenticate(engine, issue_at=T0, now=T0 + 60.0)
        self.assertEqual(report.status, "AUTH_SUCCESSFUL")

    def test_future_dated_challenge_beyond_skew_rejected(self):
        """A negative age must not read as 'very fresh' and bypass expiry."""
        engine = self.build_engine()
        report = self.authenticate(engine, issue_at=T0, now=T0 - 600.0)
        self.assertEqual(report.status, "CHALLENGE_EXPIRED")

    def test_small_negative_age_within_skew_tolerance_accepted(self):
        engine = self.build_engine()
        report = self.authenticate(engine, issue_at=T0, now=T0 - 2.0)
        self.assertEqual(report.status, "AUTH_SUCCESSFUL")

    def test_issued_challenges_are_unique_and_high_entropy(self):
        engine = self.build_engine()
        values = {engine.issue_challenge(USER, now=T0).value for _ in range(200)}
        self.assertEqual(len(values), 200)
        sample = values.pop()
        padded = sample + "=" * (-len(sample) % 4)
        self.assertGreaterEqual(len(base64.urlsafe_b64decode(padded)), 16)

    def test_purge_expired_challenges(self):
        engine = self.build_engine()
        issued = engine.issue_challenge(USER, now=T0)
        self.assertEqual(engine.purge_expired_challenges(now=T0 + 61.0), 1)
        report = engine.verify_assertion(self.make_assertion(issued.value), now=T0 + 61.0)
        self.assertEqual(report.status, "CHALLENGE_UNKNOWN")

    def test_concurrent_use_of_one_challenge_succeeds_exactly_once(self):
        engine = self.build_engine()
        issued = engine.issue_challenge(USER, now=T0)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            reports = list(
                pool.map(
                    lambda _: engine.verify_assertion(
                        self.make_assertion(issued.value), now=T0
                    ),
                    range(8),
                )
            )
        successes = [r for r in reports if r.status == "AUTH_SUCCESSFUL"]
        self.assertEqual(len(successes), 1)


class TestCredentialBinding(WebAuthnTestBase):

    def test_unknown_credential_rejected(self):
        engine = self.build_engine()
        report = self.authenticate(engine, credential_id="cred_never_registered")
        self.assertEqual(report.status, "CREDENTIAL_UNKNOWN")

    def test_credential_registered_to_another_user_rejected(self):
        """A valid assertion authenticates its own owner, not a claimed user_id."""
        engine = self.build_engine()
        engine.register_credential("cred_of_admin_2", "custody_admin_2")
        report = self.authenticate(engine, credential_id="cred_of_admin_2")
        self.assertEqual(report.status, "CREDENTIAL_USER_MISMATCH")

    def test_revoked_credential_rejected(self):
        engine = self.build_engine()
        self.assertTrue(engine.revoke_credential(CREDENTIAL))
        report = self.authenticate(engine)
        self.assertEqual(report.status, "CREDENTIAL_REVOKED")

    def test_revoking_unknown_credential_returns_false(self):
        engine = self.build_engine()
        self.assertFalse(engine.revoke_credential("cred_never_registered"))

    def test_rebinding_a_credential_to_another_user_raises(self):
        engine = self.build_engine()
        with self.assertRaises(PhishingResistantAuthError):
            engine.register_credential(CREDENTIAL, "custody_admin_2")

    def test_aaguid_allowlist_rejects_unapproved_authenticator_model(self):
        policy = AuthPolicyConfig(
            rp_id=RP_ID, allowed_origins=(ORIGIN,), allowed_aaguids=("yubico-5c",)
        )
        engine = self.build_engine(policy=policy)
        report = self.authenticate(engine, aaguid="unknown-vendor-key")
        self.assertEqual(report.status, "AUTHENTICATOR_NOT_ALLOWED")

    def test_aaguid_allowlist_accepts_approved_model(self):
        policy = AuthPolicyConfig(
            rp_id=RP_ID, allowed_origins=(ORIGIN,), allowed_aaguids=("YUBICO-5C",)
        )
        engine = self.build_engine(policy=policy)
        report = self.authenticate(engine, aaguid="yubico-5c")
        self.assertEqual(report.status, "AUTH_SUCCESSFUL")


class TestFlagSemantics(WebAuthnTestBase):

    def test_missing_user_presence_is_not_reported_as_verification_failure(self):
        """UP and UV are separate §7.2 steps and must be separate audit outcomes."""
        engine = self.build_engine()
        report = self.authenticate(engine, user_present=False)
        self.assertEqual(report.status, "USER_PRESENCE_MISSING")

    def test_missing_user_verification_rejected_when_required(self):
        engine = self.build_engine()
        report = self.authenticate(engine, user_verified=False)
        self.assertEqual(report.status, "USER_VERIFICATION_FAILED")

    def test_user_verification_optional_when_policy_allows(self):
        policy = AuthPolicyConfig(
            rp_id=RP_ID, allowed_origins=(ORIGIN,), require_user_verification=False
        )
        engine = self.build_engine(policy=policy)
        report = self.authenticate(engine, user_verified=False)
        self.assertEqual(report.status, "AUTH_SUCCESSFUL")
        self.assertFalse(report.is_user_verified)

    def test_skipped_signature_verification_is_flagged_in_the_report(self):
        """A success that rests on policy alone must say so in the audit record."""
        policy = AuthPolicyConfig(
            rp_id=RP_ID, allowed_origins=(ORIGIN,), require_signature_verification=False
        )
        engine = self.build_engine(policy=policy)
        report = self.authenticate(engine, signature_verified=False)
        self.assertEqual(report.status, "AUTH_SUCCESSFUL")
        self.assertTrue(any("without signature" in w for w in report.warnings))

    def test_unverified_signature_fails_closed(self):
        engine = self.build_engine()
        report = self.authenticate(engine, signature_verified=False)
        self.assertEqual(report.status, "SIGNATURE_NOT_VERIFIED")

    def test_assertion_defaults_are_deny_by_default(self):
        """An assertion built without wiring any verifier must not authenticate."""
        engine = self.build_engine()
        issued = engine.issue_challenge(USER, now=T0)
        bare = WebAuthnAssertion(
            user_id=USER,
            credential_id=CREDENTIAL,
            client_origin=ORIGIN,
            challenge=issued.value,
            rp_id_hash=compute_rp_id_hash(RP_ID),
        )
        report = engine.verify_assertion(bare, now=T0)
        self.assertFalse(report.is_authenticated)
        self.assertEqual(report.status, "USER_PRESENCE_MISSING")

    def test_registration_response_replayed_into_authentication_rejected(self):
        engine = self.build_engine()
        report = self.authenticate(engine, client_data_type="webauthn.create")
        self.assertEqual(report.status, "CLIENT_DATA_TYPE_INVALID")


class TestBackupStateAndCloning(WebAuthnTestBase):

    def test_backup_eligible_zero_with_backup_state_one_rejected(self):
        """WebAuthn L3 §6.1.3 marks BE=0, BS=1 as a combination that is not allowed."""
        engine = self.build_engine()
        report = self.authenticate(engine, backup_eligible=False, backup_state=True)
        self.assertEqual(report.status, "BACKUP_STATE_INVALID")

    def test_backup_eligibility_change_since_registration_rejected(self):
        engine = self.build_engine()  # registered with backup_eligible=False
        report = self.authenticate(engine, backup_eligible=True, backup_state=True)
        self.assertEqual(report.status, "BACKUP_STATE_INVALID")

    def test_syncable_passkey_rejected_when_device_bound_required(self):
        policy = AuthPolicyConfig(
            rp_id=RP_ID, allowed_origins=(ORIGIN,), require_device_bound_credential=True
        )
        engine = PhishingResistantAuthenticationForCustodyAccessEngine(
            PhishingResistantAuthenticationForCustodyAccessConfig(), policy
        )
        engine.register_credential(CREDENTIAL, USER, sign_count=10, backup_eligible=True)
        report = self.authenticate(engine, backup_eligible=True, backup_state=True)
        self.assertEqual(report.status, "DEVICE_BOUND_CREDENTIAL_REQUIRED")

    def test_sign_count_regression_rejected_by_default(self):
        engine = self.build_engine()  # stored sign_count = 10
        report = self.authenticate(engine, sign_count=9)
        self.assertEqual(report.status, "SIGN_COUNT_REGRESSION_CLONE_SUSPECTED")

    def test_repeated_sign_count_rejected_by_default(self):
        engine = self.build_engine()
        report = self.authenticate(engine, sign_count=10)
        self.assertEqual(report.status, "SIGN_COUNT_REGRESSION_CLONE_SUSPECTED")

    def test_sign_count_regression_may_be_downgraded_to_a_warning(self):
        policy = AuthPolicyConfig(
            rp_id=RP_ID, allowed_origins=(ORIGIN,), reject_sign_count_regression=False
        )
        engine = self.build_engine(policy=policy)
        report = self.authenticate(engine, sign_count=9)
        self.assertEqual(report.status, "AUTH_SUCCESSFUL")
        self.assertEqual(len(report.warnings), 1)
        self.assertIn("cloned", report.warnings[0])

    def test_stored_counter_never_moves_backwards_on_a_tolerated_regression(self):
        policy = AuthPolicyConfig(
            rp_id=RP_ID, allowed_origins=(ORIGIN,), reject_sign_count_regression=False
        )
        engine = self.build_engine(policy=policy)
        self.authenticate(engine, sign_count=9)
        self.assertEqual(engine.get_credential(CREDENTIAL).sign_count, 10)

    def test_authenticator_without_a_counter_is_accepted(self):
        """§7.2 runs the counter step only when either value is nonzero."""
        engine = PhishingResistantAuthenticationForCustodyAccessEngine(
            PhishingResistantAuthenticationForCustodyAccessConfig(),
            AuthPolicyConfig(rp_id=RP_ID, allowed_origins=(ORIGIN,)),
        )
        engine.register_credential(CREDENTIAL, USER, sign_count=0)
        report = self.authenticate(engine, sign_count=0)
        self.assertEqual(report.status, "AUTH_SUCCESSFUL")

    def test_rejected_assertion_does_not_advance_stored_counter(self):
        """§7.2 defers state updates until every check has passed."""
        engine = self.build_engine()
        self.authenticate(engine, sign_count=9_999, client_origin=PHISHED_ORIGIN)
        self.assertEqual(engine.get_credential(CREDENTIAL).sign_count, 10)


class TestInputValidation(WebAuthnTestBase):

    def test_empty_allowed_origins_rejected_at_configuration_time(self):
        with self.assertRaises(PhishingResistantAuthError):
            AuthPolicyConfig(rp_id=RP_ID, allowed_origins=())

    def test_bare_string_allowed_origins_rejected(self):
        """A string would be iterated character by character into an origin list."""
        with self.assertRaises(PhishingResistantAuthError):
            AuthPolicyConfig(rp_id=RP_ID, allowed_origins=ORIGIN)

    def test_non_https_origin_rejected(self):
        with self.assertRaises(PhishingResistantAuthError):
            AuthPolicyConfig(rp_id=RP_ID, allowed_origins=("http://custody.firm.com",))

    def test_origin_with_trailing_slash_rejected(self):
        with self.assertRaises(PhishingResistantAuthError):
            AuthPolicyConfig(rp_id=RP_ID, allowed_origins=("https://custody.firm.com/",))

    def test_non_positive_challenge_age_rejected(self):
        with self.assertRaises(PhishingResistantAuthError):
            AuthPolicyConfig(rp_id=RP_ID, allowed_origins=(ORIGIN,), max_challenge_age_sec=0)

    def test_non_finite_challenge_age_rejected(self):
        with self.assertRaises(PhishingResistantAuthError):
            AuthPolicyConfig(
                rp_id=RP_ID, allowed_origins=(ORIGIN,), max_challenge_age_sec=float("nan")
            )

    def test_negative_skew_tolerance_rejected(self):
        with self.assertRaises(PhishingResistantAuthError):
            AuthPolicyConfig(
                rp_id=RP_ID, allowed_origins=(ORIGIN,), clock_skew_tolerance_sec=-1.0
            )

    def test_blank_rp_id_rejected(self):
        with self.assertRaises(PhishingResistantAuthError):
            AuthPolicyConfig(rp_id="   ", allowed_origins=(ORIGIN,))

    def test_blank_identifiers_in_assertion_raise(self):
        engine = self.build_engine()
        issued = engine.issue_challenge(USER, now=T0)
        with self.assertRaises(PhishingResistantAuthError):
            engine.verify_assertion(self.make_assertion(issued.value, user_id="  "), now=T0)

    def test_non_assertion_input_raises(self):
        engine = self.build_engine()
        with self.assertRaises(PhishingResistantAuthError):
            engine.verify_assertion({"user_id": USER}, now=T0)

    def test_negative_registration_sign_count_raises(self):
        engine = self.build_engine()
        with self.assertRaises(PhishingResistantAuthError):
            engine.register_credential("cred_negative", "custody_admin_2", sign_count=-1)

    def test_negative_assertion_sign_count_raises(self):
        engine = self.build_engine()
        with self.assertRaises(PhishingResistantAuthError):
            self.authenticate(engine, sign_count=-5)

    def test_hex_string_rp_id_hash_raises_rather_than_silently_failing(self):
        """authenticatorData[0:32] is raw bytes; hex text must be a loud error."""
        engine = self.build_engine()
        with self.assertRaises(PhishingResistantAuthError):
            self.authenticate(engine, rp_id_hash=compute_rp_id_hash(RP_ID).hex())

    def test_non_integer_sign_count_raises(self):
        engine = self.build_engine()
        with self.assertRaises(PhishingResistantAuthError):
            self.authenticate(engine, sign_count="not-a-counter")

    def test_non_finite_now_raises(self):
        engine = self.build_engine()
        with self.assertRaises(PhishingResistantAuthError):
            engine.issue_challenge(USER, now=float("inf"))

    def test_unicode_user_id_is_rejected_not_crashed(self):
        """A non-ASCII identifier must reach a decision, not a TypeError."""
        engine = self.build_engine()
        issued = engine.issue_challenge("custody_admin_ünïcode", now=T0)
        report = engine.verify_assertion(self.make_assertion(issued.value), now=T0)
        self.assertEqual(report.status, "CHALLENGE_USER_MISMATCH")


class TestDataclassSurface(unittest.TestCase):

    def test_report_and_records_are_constructible(self):
        report = AuthVerificationReport(
            user_id=USER,
            credential_id=CREDENTIAL,
            rp_id=RP_ID,
            client_origin=ORIGIN,
            is_origin_valid=True,
            is_user_verified=True,
            is_authenticated=True,
            status="AUTH_SUCCESSFUL",
            audit_notes="ok",
        )
        self.assertEqual(report.warnings, ())
        self.assertEqual(RegisteredCredential(CREDENTIAL, USER).sign_count, 0)
        self.assertEqual(IssuedChallenge("value", USER, T0).issued_at, T0)


if __name__ == '__main__':
    unittest.main()
