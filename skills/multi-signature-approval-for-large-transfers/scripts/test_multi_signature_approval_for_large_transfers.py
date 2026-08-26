import logging
import unittest

from multi_signature_approval_for_large_transfers import (
    MultiSigApprovalEngine,
    MultiSigApprovalError,
    MultiSigConfig,
    SignerApproval,
    TransferRequestPayload,
    compute_transfer_digest,
)

T0 = 1_000.0
TIMELOCK = 3_600.0


def setUpModule():
    # The engine logs every rejected approval; silence it so the shared runner's
    # output stays readable. Scoped to this module, not disabled globally.
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


def make_request(
    request_id="TX_LARGE_001",
    amount_usd=250_000.0,
    destination="0xExchangeVault",
    initiated_by="BOT_TRADER",
    creation_timestamp=T0,
    asset_quantity=100.0,
    nonce=0,
):
    return TransferRequestPayload(
        request_id=request_id,
        amount_usd=amount_usd,
        source_wallet="0xTreasury",
        destination_address=destination,
        initiated_by=initiated_by,
        creation_timestamp=creation_timestamp,
        asset_symbol="ETH",
        asset_quantity=asset_quantity,
        chain="ethereum",
        nonce=nonce,
    )


class MultiSigTestBase(unittest.TestCase):
    """Shared 5-signer roster covering 5 distinct roles."""

    ROSTER = (
        ("SIGNER_CFO", "CFO"),
        ("SIGNER_RISK", "RISK_OFFICER"),
        ("SIGNER_SEC", "SECURITY_OFFICER"),
        ("SIGNER_CIO", "CIO"),
        ("SIGNER_COMPLIANCE", "COMPLIANCE"),
    )

    def setUp(self):
        self.config = MultiSigConfig(
            auto_approve_threshold_usd=10_000.0,
            high_value_threshold_usd=100_000.0,
            high_value_timelock_seconds=TIMELOCK,
        )
        self.engine = MultiSigApprovalEngine(self.config)
        for signer_id, role in self.ROSTER:
            self.engine.register_signer(signer_id, role)

    def bound_approvals(self, request, signer_ids, at=None):
        """Approvals bound to the request's own digest, as real signers produce."""
        digest = compute_transfer_digest(request)
        stamp = T0 + 100.0 if at is None else at
        roles = dict(self.ROSTER)
        return [
            SignerApproval(sid, roles[sid], stamp + index, approved_digest=digest)
            for index, sid in enumerate(signer_ids)
        ]


class TestTierClassification(MultiSigTestBase):

    def test_below_auto_threshold_is_low_tier(self):
        req = make_request(amount_usd=9_999.99)
        report = self.engine.evaluate_transfer_approval(req, [], current_time=T0)
        self.assertEqual(report.risk_tier, "LOW_AUTO")
        self.assertEqual(report.m_required, 1)

    def test_exactly_auto_threshold_is_medium_tier(self):
        # Boundary: the low tier is strictly below auto_approve_threshold_usd.
        req = make_request(amount_usd=10_000.0)
        report = self.engine.evaluate_transfer_approval(req, [], current_time=T0)
        self.assertEqual(report.risk_tier, "MEDIUM_MULTISIG")
        self.assertEqual((report.m_required, report.n_total), (2, 3))

    def test_exactly_high_threshold_is_medium_tier_without_timelock(self):
        # Boundary: high tier is strictly above high_value_threshold_usd.
        req = make_request(amount_usd=100_000.0)
        approvals = self.bound_approvals(req, ["SIGNER_CFO", "SIGNER_RISK"])
        report = self.engine.evaluate_transfer_approval(req, approvals, current_time=T0)
        self.assertEqual(report.risk_tier, "MEDIUM_MULTISIG")
        self.assertEqual(report.timelock_required_seconds, 0.0)
        self.assertEqual(report.status, "TRANSFER_APPROVED")

    def test_one_cent_above_high_threshold_is_high_tier(self):
        req = make_request(amount_usd=100_000.01)
        report = self.engine.evaluate_transfer_approval(req, [], current_time=T0)
        self.assertEqual(report.risk_tier, "HIGH_MULTISIG_TIMELOCK")
        self.assertEqual((report.m_required, report.n_total), (3, 5))
        self.assertEqual(report.timelock_required_seconds, TIMELOCK)


class TestHappyPath(MultiSigTestBase):

    def test_high_tier_multisig_and_timelock_approval(self):
        # $250k -> high tier: 3-of-5 across 3 distinct roles, plus a 3600s timelock.
        req = make_request(amount_usd=250_000.0)
        self.engine.register_request(req, current_time=T0)
        approvals = self.bound_approvals(req, ["SIGNER_CFO", "SIGNER_RISK", "SIGNER_SEC"])

        report = self.engine.evaluate_transfer_approval(req, approvals, current_time=T0 + 4_000.0)

        self.assertTrue(report.is_approved)
        self.assertEqual(report.status, "TRANSFER_APPROVED")
        self.assertEqual(report.risk_tier, "HIGH_MULTISIG_TIMELOCK")
        self.assertEqual(report.submitted_approvals_count, 3)
        self.assertTrue(report.timelock_satisfied)
        self.assertEqual(report.remaining_timelock_seconds, 0.0)
        self.assertEqual(report.rejected_approvals, ())
        self.assertEqual(
            report.distinct_roles_present, ("CFO", "RISK_OFFICER", "SECURITY_OFFICER")
        )

    def test_timelock_boundary_is_inclusive(self):
        # elapsed == timelock approves; one second short does not.
        req = make_request(amount_usd=250_000.0)
        self.engine.register_request(req, current_time=T0)
        approvals = self.bound_approvals(req, ["SIGNER_CFO", "SIGNER_RISK", "SIGNER_SEC"])

        just_short = self.engine.evaluate_transfer_approval(
            req, approvals, current_time=T0 + TIMELOCK - 1.0
        )
        self.assertEqual(just_short.status, "TIMELOCK_PENDING")
        self.assertEqual(just_short.remaining_timelock_seconds, 1.0)

        exact = self.engine.evaluate_transfer_approval(req, approvals, current_time=T0 + TIMELOCK)
        self.assertEqual(exact.status, "TRANSFER_APPROVED")

    def test_low_tier_permits_a_single_initiator_self_approval(self):
        req = make_request(amount_usd=500.0, initiated_by="BOT_TRADER")
        self.engine.register_signer("BOT_TRADER", "AUTOMATION")
        approvals = [
            SignerApproval("BOT_TRADER", "AUTOMATION", T0, compute_transfer_digest(req))
        ]
        report = self.engine.evaluate_transfer_approval(req, approvals, current_time=T0)
        self.assertEqual(report.status, "TRANSFER_APPROVED")

    def test_low_tier_self_approval_can_be_disabled_by_policy(self):
        strict = MultiSigApprovalEngine(
            MultiSigConfig(low_tier_allows_self_approval=False)
        )
        strict.register_signer("BOT_TRADER", "AUTOMATION")
        req = make_request(amount_usd=500.0, initiated_by="BOT_TRADER")
        approvals = [
            SignerApproval("BOT_TRADER", "AUTOMATION", T0, compute_transfer_digest(req))
        ]
        report = strict.evaluate_transfer_approval(req, approvals, current_time=T0)
        self.assertEqual(report.status, "INSUFFICIENT_SIGNATURES")
        self.assertIn(("BOT_TRADER", "SELF_APPROVAL_BY_INITIATOR"), report.rejected_approvals)


class TestQuorumIntegrity(MultiSigTestBase):
    """Regression tests for defects that let a $5m transfer through unauthorised."""

    def test_unregistered_signers_cannot_form_a_quorum(self):
        # Previously any three invented ids satisfied 3-of-5.
        req = make_request(amount_usd=5_000_000.0, destination="0xATTACKER")
        digest = compute_transfer_digest(req)
        ghosts = [SignerApproval(f"ghost{i}", "CFO", T0, digest) for i in range(3)]

        report = self.engine.evaluate_transfer_approval(req, ghosts, current_time=T0 + 100_000.0)

        self.assertFalse(report.is_approved)
        self.assertEqual(report.status, "INSUFFICIENT_SIGNATURES")
        self.assertEqual(report.submitted_approvals_count, 0)
        self.assertEqual(report.total_submitted_approvals, 3)
        for entry in report.rejected_approvals:
            self.assertEqual(entry[1], "SIGNER_NOT_ON_ROSTER")

    def test_duplicate_approvals_from_one_signer_count_once(self):
        req = make_request(amount_usd=250_000.0)
        approvals = self.bound_approvals(req, ["SIGNER_CFO", "SIGNER_CFO", "SIGNER_CFO"])
        report = self.engine.evaluate_transfer_approval(req, approvals, current_time=T0 + 100_000.0)

        self.assertEqual(report.submitted_approvals_count, 1)
        self.assertEqual(report.status, "INSUFFICIENT_SIGNATURES")
        self.assertEqual(
            [r for _, r in report.rejected_approvals],
            ["DUPLICATE_APPROVAL_FROM_SAME_SIGNER", "DUPLICATE_APPROVAL_FROM_SAME_SIGNER"],
        )

    def test_initiator_cannot_self_approve_above_low_tier(self):
        req = make_request(amount_usd=250_000.0, initiated_by="SIGNER_CFO")
        approvals = self.bound_approvals(req, ["SIGNER_CFO", "SIGNER_RISK", "SIGNER_SEC"])
        report = self.engine.evaluate_transfer_approval(req, approvals, current_time=T0 + 100_000.0)

        self.assertEqual(report.status, "INSUFFICIENT_SIGNATURES")
        self.assertEqual(report.submitted_approvals_count, 2)
        self.assertIn(("SIGNER_CFO", "SELF_APPROVAL_BY_INITIATOR"), report.rejected_approvals)

    def test_quorum_from_a_single_role_is_rejected(self):
        # Three signers, one role: a compromised desk cannot self-authorise.
        one_role = MultiSigApprovalEngine(MultiSigConfig(high_value_timelock_seconds=0.0))
        for sid in ("A", "B", "C", "D", "E"):
            one_role.register_signer(sid, "TRADING")
        req = make_request(amount_usd=250_000.0)
        digest = compute_transfer_digest(req)
        approvals = [SignerApproval(sid, "TRADING", T0, digest) for sid in ("A", "B", "C")]

        report = one_role.evaluate_transfer_approval(req, approvals, current_time=T0)

        self.assertEqual(report.status, "INSUFFICIENT_DISTINCT_ROLES")
        self.assertEqual(report.submitted_approvals_count, 3)
        self.assertEqual(report.distinct_roles_present, ("TRADING",))
        self.assertEqual(report.distinct_roles_required, 3)

    def test_role_declared_on_the_approval_must_match_the_roster(self):
        req = make_request(amount_usd=250_000.0)
        digest = compute_transfer_digest(req)
        approvals = [
            SignerApproval("SIGNER_CFO", "SECURITY_OFFICER", T0, digest),  # claims another role
            SignerApproval("SIGNER_RISK", "RISK_OFFICER", T0, digest),
            SignerApproval("SIGNER_SEC", "SECURITY_OFFICER", T0, digest),
        ]
        report = self.engine.evaluate_transfer_approval(req, approvals, current_time=T0 + 100_000.0)

        self.assertIn(("SIGNER_CFO", "ROLE_MISMATCH_WITH_ROSTER"), report.rejected_approvals)
        self.assertEqual(report.submitted_approvals_count, 2)

    def test_suspended_signer_stops_counting_within_the_timelock_window(self):
        req = make_request(amount_usd=250_000.0)
        self.engine.register_request(req, current_time=T0)
        approvals = self.bound_approvals(req, ["SIGNER_CFO", "SIGNER_RISK", "SIGNER_SEC"])

        before = self.engine.evaluate_transfer_approval(req, approvals, current_time=T0 + 60.0)
        self.assertEqual(before.status, "TIMELOCK_PENDING")

        self.engine.suspend_signer("SIGNER_RISK", reason="suspected key compromise")
        after = self.engine.evaluate_transfer_approval(req, approvals, current_time=T0 + 4_000.0)

        self.assertEqual(after.status, "INSUFFICIENT_SIGNATURES")
        self.assertIn(("SIGNER_RISK", "SIGNER_SUSPENDED"), after.rejected_approvals)
        self.assertEqual(after.eligible_signer_count, 4)


class TestPayloadBinding(MultiSigTestBase):

    def test_digest_changes_when_the_destination_changes(self):
        original = make_request(destination="0xExchangeVault")
        swapped = make_request(destination="0xATTACKER")
        self.assertNotEqual(compute_transfer_digest(original), compute_transfer_digest(swapped))

    def test_digest_is_unambiguous_across_field_boundaries(self):
        # Length-prefixing prevents ("A","B") and ("AB","") colliding.
        a = make_request(request_id="AB", destination="C")
        b = make_request(request_id="A", destination="BC")
        self.assertNotEqual(compute_transfer_digest(a), compute_transfer_digest(b))

    def test_approvals_for_one_payload_do_not_authorise_another(self):
        approved = make_request(amount_usd=250_000.0, destination="0xExchangeVault")
        approvals = self.bound_approvals(approved, ["SIGNER_CFO", "SIGNER_RISK", "SIGNER_SEC"])

        redirected = make_request(amount_usd=250_000.0, destination="0xATTACKER")
        report = self.engine.evaluate_transfer_approval(
            redirected, approvals, current_time=T0 + 100_000.0
        )

        self.assertFalse(report.is_approved)
        self.assertEqual(report.submitted_approvals_count, 0)
        for entry in report.rejected_approvals:
            self.assertEqual(entry[1], "APPROVAL_BOUND_TO_DIFFERENT_PAYLOAD")

    def test_unbound_approvals_are_rejected_by_default(self):
        req = make_request(amount_usd=250_000.0)
        approvals = [
            SignerApproval("SIGNER_CFO", "CFO", T0),
            SignerApproval("SIGNER_RISK", "RISK_OFFICER", T0),
            SignerApproval("SIGNER_SEC", "SECURITY_OFFICER", T0),
        ]
        report = self.engine.evaluate_transfer_approval(req, approvals, current_time=T0 + 100_000.0)

        self.assertEqual(report.status, "INSUFFICIENT_SIGNATURES")
        for entry in report.rejected_approvals:
            self.assertEqual(entry[1], "APPROVAL_NOT_BOUND_TO_PAYLOAD")

    def test_amount_change_re_anchors_the_timelock(self):
        original = make_request(amount_usd=250_000.0)
        self.engine.register_request(original, current_time=T0)
        raised = make_request(amount_usd=900_000.0)
        approvals = self.bound_approvals(raised, ["SIGNER_CFO", "SIGNER_RISK", "SIGNER_SEC"])

        # The original request's timelock has long elapsed, but this is a new payload.
        report = self.engine.evaluate_transfer_approval(raised, approvals, current_time=T0 + 4_000.0)

        self.assertEqual(report.status, "TIMELOCK_PENDING")
        self.assertEqual(report.timelock_anchor_timestamp, T0 + 4_000.0)

    def test_missing_asset_quantity_is_warned_above_low_tier(self):
        req = make_request(amount_usd=250_000.0, asset_quantity=None)
        report = self.engine.evaluate_transfer_approval(req, [], current_time=T0)
        self.assertTrue(any("asset_quantity" in w for w in report.warnings))


class TestTimelockClockIntegrity(MultiSigTestBase):

    def test_backdated_creation_timestamp_does_not_open_the_timelock(self):
        # Previously creation_timestamp=-1e9 approved a $5m transfer instantly.
        req = make_request(amount_usd=5_000_000.0, creation_timestamp=-1e9)
        approvals = self.bound_approvals(
            req, ["SIGNER_CFO", "SIGNER_RISK", "SIGNER_SEC"], at=T0
        )
        report = self.engine.evaluate_transfer_approval(req, approvals, current_time=T0)

        self.assertFalse(report.is_approved)
        self.assertEqual(report.status, "TIMELOCK_PENDING")
        self.assertEqual(report.timelock_anchor_timestamp, T0)
        self.assertEqual(report.remaining_timelock_seconds, TIMELOCK)

    def test_future_creation_timestamp_is_warned_not_trusted(self):
        req = make_request(amount_usd=250_000.0, creation_timestamp=T0 + 10_000.0)
        report = self.engine.evaluate_transfer_approval(req, [], current_time=T0)
        self.assertTrue(any("ahead of the evaluation clock" in w for w in report.warnings))

    def test_zero_current_time_is_honoured_not_treated_as_missing(self):
        # `current_time or time.time()` used to discard a legitimate 0.0.
        req = make_request(amount_usd=250_000.0, creation_timestamp=0.0)
        self.engine.register_request(req, current_time=0.0)
        approvals = self.bound_approvals(
            req, ["SIGNER_CFO", "SIGNER_RISK", "SIGNER_SEC"], at=0.0
        )
        report = self.engine.evaluate_transfer_approval(req, approvals, current_time=0.0)

        self.assertEqual(report.timelock_anchor_timestamp, 0.0)
        self.assertEqual(report.status, "TIMELOCK_PENDING")
        self.assertEqual(report.remaining_timelock_seconds, TIMELOCK)

    def test_registering_twice_does_not_move_the_anchor(self):
        req = make_request(amount_usd=250_000.0)
        first = self.engine.register_request(req, current_time=T0)
        second = self.engine.register_request(req, current_time=T0 + 5_000.0)
        self.assertEqual(first, T0)
        self.assertEqual(second, T0)

    def test_approval_timestamped_in_the_future_is_rejected(self):
        req = make_request(amount_usd=250_000.0)
        approvals = self.bound_approvals(
            req, ["SIGNER_CFO", "SIGNER_RISK", "SIGNER_SEC"], at=T0 + 100_000.0
        )
        report = self.engine.evaluate_transfer_approval(req, approvals, current_time=T0)
        self.assertEqual(report.submitted_approvals_count, 0)
        for entry in report.rejected_approvals:
            self.assertEqual(entry[1], "APPROVAL_TIMESTAMP_IN_FUTURE")

    def test_expired_approvals_are_rejected_when_validity_is_configured(self):
        expiring = MultiSigApprovalEngine(
            MultiSigConfig(high_value_timelock_seconds=0.0, approval_validity_seconds=600.0)
        )
        for signer_id, role in self.ROSTER:
            expiring.register_signer(signer_id, role)
        req = make_request(amount_usd=250_000.0)
        digest = compute_transfer_digest(req)
        approvals = [
            SignerApproval("SIGNER_CFO", "CFO", T0, digest),
            SignerApproval("SIGNER_RISK", "RISK_OFFICER", T0, digest),
            SignerApproval("SIGNER_SEC", "SECURITY_OFFICER", T0 + 590.0, digest),
        ]
        report = expiring.evaluate_transfer_approval(req, approvals, current_time=T0 + 601.0)

        self.assertEqual(report.submitted_approvals_count, 1)
        self.assertEqual(report.approving_signers, ("SIGNER_SEC",))
        self.assertEqual(
            sorted(sid for sid, reason in report.rejected_approvals if reason == "APPROVAL_EXPIRED"),
            ["SIGNER_CFO", "SIGNER_RISK"],
        )


class TestRevocationAndExecution(MultiSigTestBase):

    def test_revoked_request_is_blocked_even_with_full_quorum(self):
        req = make_request(amount_usd=250_000.0)
        self.engine.register_request(req, current_time=T0)
        approvals = self.bound_approvals(req, ["SIGNER_CFO", "SIGNER_RISK", "SIGNER_SEC"])

        self.engine.revoke_request("TX_LARGE_001", "SIGNER_SEC", reason="destination unrecognised")
        report = self.engine.evaluate_transfer_approval(req, approvals, current_time=T0 + 4_000.0)

        self.assertFalse(report.is_approved)
        self.assertEqual(report.status, "REQUEST_REVOKED")
        self.assertIn("SIGNER_SEC", report.audit_notes)

    def test_revocation_survives_a_nonce_bump(self):
        req = make_request(amount_usd=250_000.0)
        self.engine.revoke_request("TX_LARGE_001", "SIGNER_SEC", reason="fraud suspected")
        resubmitted = make_request(amount_usd=250_000.0, nonce=7)
        approvals = self.bound_approvals(
            resubmitted, ["SIGNER_CFO", "SIGNER_RISK", "SIGNER_SEC"]
        )
        report = self.engine.evaluate_transfer_approval(
            resubmitted, approvals, current_time=T0 + 100_000.0
        )
        self.assertEqual(report.status, "REQUEST_REVOKED")

    def test_executed_payload_cannot_be_approved_twice(self):
        req = make_request(amount_usd=250_000.0)
        self.engine.register_request(req, current_time=T0)
        approvals = self.bound_approvals(req, ["SIGNER_CFO", "SIGNER_RISK", "SIGNER_SEC"])

        first = self.engine.evaluate_transfer_approval(req, approvals, current_time=T0 + 4_000.0)
        self.assertTrue(first.is_approved)

        self.engine.mark_executed(first.transfer_digest, current_time=T0 + 4_001.0)
        replay = self.engine.evaluate_transfer_approval(req, approvals, current_time=T0 + 5_000.0)

        self.assertFalse(replay.is_approved)
        self.assertEqual(replay.status, "ALREADY_EXECUTED")

    def test_marking_the_same_digest_executed_twice_raises(self):
        req = make_request(amount_usd=250_000.0)
        digest = compute_transfer_digest(req)
        self.engine.mark_executed(digest, current_time=T0)
        with self.assertRaises(MultiSigApprovalError):
            self.engine.mark_executed(digest, current_time=T0 + 1.0)


class TestInputValidation(MultiSigTestBase):

    def test_nan_amount_raises_rather_than_being_scored(self):
        # NaN previously fell through every comparison into the high tier and approved.
        with self.assertRaises(MultiSigApprovalError):
            self.engine.evaluate_transfer_approval(
                make_request(amount_usd=float("nan")), [], current_time=T0
            )

    def test_infinite_amount_raises(self):
        with self.assertRaises(MultiSigApprovalError):
            self.engine.evaluate_transfer_approval(
                make_request(amount_usd=float("inf")), [], current_time=T0
            )

    def test_negative_amount_raises_instead_of_downgrading_to_low_tier(self):
        with self.assertRaises(MultiSigApprovalError):
            self.engine.evaluate_transfer_approval(
                make_request(amount_usd=-1.0), [], current_time=T0
            )

    def test_zero_amount_raises(self):
        with self.assertRaises(MultiSigApprovalError):
            self.engine.evaluate_transfer_approval(
                make_request(amount_usd=0.0), [], current_time=T0
            )

    def test_blank_destination_raises(self):
        with self.assertRaises(MultiSigApprovalError):
            self.engine.evaluate_transfer_approval(
                make_request(destination="   "), [], current_time=T0
            )

    def test_non_finite_evaluation_clock_raises(self):
        with self.assertRaises(MultiSigApprovalError):
            self.engine.evaluate_transfer_approval(
                make_request(), [], current_time=float("nan")
            )

    def test_non_finite_approval_timestamp_is_rejected_not_counted(self):
        req = make_request(amount_usd=250_000.0)
        digest = compute_transfer_digest(req)
        approvals = [SignerApproval("SIGNER_CFO", "CFO", float("nan"), digest)]
        report = self.engine.evaluate_transfer_approval(req, approvals, current_time=T0)
        self.assertIn(("SIGNER_CFO", "NON_FINITE_APPROVAL_TIMESTAMP"), report.rejected_approvals)

    def test_suspending_an_unknown_signer_raises(self):
        with self.assertRaises(MultiSigApprovalError):
            self.engine.suspend_signer("NOBODY")


class TestConfigValidation(unittest.TestCase):

    def test_m_greater_than_n_is_rejected(self):
        with self.assertRaises(MultiSigApprovalError):
            MultiSigConfig(high_m_required=9, high_n_total=2)

    def test_inverted_thresholds_are_rejected(self):
        with self.assertRaises(MultiSigApprovalError):
            MultiSigConfig(auto_approve_threshold_usd=100_000.0, high_value_threshold_usd=10_000.0)

    def test_distinct_roles_cannot_exceed_m(self):
        with self.assertRaises(MultiSigApprovalError):
            MultiSigConfig(med_m_required=2, med_n_total=3, med_distinct_roles_required=3)

    def test_negative_timelock_is_rejected(self):
        with self.assertRaises(MultiSigApprovalError):
            MultiSigConfig(high_value_timelock_seconds=-1.0)

    def test_non_finite_threshold_is_rejected(self):
        with self.assertRaises(MultiSigApprovalError):
            MultiSigConfig(high_value_threshold_usd=float("nan"))


class TestRosterVisibility(MultiSigTestBase):

    def test_empty_roster_rejects_everything_and_says_so(self):
        bare = MultiSigApprovalEngine(self.config)
        req = make_request(amount_usd=250_000.0)
        approvals = self.bound_approvals(req, ["SIGNER_CFO", "SIGNER_RISK", "SIGNER_SEC"])
        report = bare.evaluate_transfer_approval(req, approvals, current_time=T0 + 100_000.0)

        self.assertEqual(report.status, "INSUFFICIENT_SIGNATURES")
        self.assertEqual(report.eligible_signer_count, 0)
        self.assertTrue(any("No eligible signers registered" in w for w in report.warnings))

    def test_roster_smaller_than_declared_n_is_warned(self):
        short = MultiSigApprovalEngine(self.config)
        for signer_id, role in self.ROSTER[:3]:
            short.register_signer(signer_id, role)
        req = make_request(amount_usd=250_000.0)
        report = short.evaluate_transfer_approval(req, [], current_time=T0)

        self.assertEqual(report.eligible_signer_count, 3)
        self.assertTrue(any("N is not actually available" in w for w in report.warnings))


if __name__ == "__main__":
    unittest.main()
