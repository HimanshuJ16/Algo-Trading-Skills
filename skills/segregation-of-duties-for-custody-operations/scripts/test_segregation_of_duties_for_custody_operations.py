import hashlib
import unittest

from segregation_of_duties_for_custody_operations import (
    DEFAULT_INCOMPATIBLE_ROLE_PAIRS,
    DIGEST_DOMAIN,
    GENESIS_HASH,
    STATUS_APPROVED,
    STATUS_EXECUTED,
    STATUS_PENDING,
    STATUS_REJECTED,
    STRICT_INCOMPATIBLE_ROLE_PAIRS,
    CustodyRole,
    CustodyTransferProposal,
    SegregationOfDutiesForCustodyOperationsConfig,
    SegregationOfDutiesForCustodyOperationsEngine,
    SoDConflictError,
    SoDViolationType,
    UserIdentity,
    compute_proposal_digest,
)


class _StubClock:
    """Monotonic injected clock so every audit hash is reproducible."""

    def __init__(self, start: float = 1_700_000_000.0, step: float = 1.0) -> None:
        self.now = start
        self.step = step

    def __call__(self) -> float:
        value = self.now
        self.now += self.step
        return value


class TestSegregationOfDutiesLegacy(unittest.TestCase):
    def test_execute_true(self):
        engine = SegregationOfDutiesForCustodyOperationsEngine(
            SegregationOfDutiesForCustodyOperationsConfig(enabled=True)
        )
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        engine = SegregationOfDutiesForCustodyOperationsEngine(
            SegregationOfDutiesForCustodyOperationsConfig(enabled=False)
        )
        self.assertFalse(engine.execute())


class _EngineFixture(unittest.TestCase):
    """Shared roster: one maker, two checkers on different desks, one admin."""

    def setUp(self):
        self.clock = _StubClock()
        self.engine = SegregationOfDutiesForCustodyOperationsEngine(
            large_transfer_threshold_usd=50000.0, clock=self.clock
        )
        self.maker = UserIdentity("USR_MAKER_1", "alice", "Trading", {CustodyRole.INITIATOR})
        self.checker1 = UserIdentity("USR_CHECKER_1", "bob", "Risk", {CustodyRole.APPROVER})
        self.checker2 = UserIdentity("USR_CHECKER_2", "charlie", "Compliance", {CustodyRole.APPROVER})
        self.admin = UserIdentity("USR_ADMIN_1", "dave", "Security", {CustodyRole.SECURITY_ADMIN})

        self.engine.register_user(self.maker)
        self.engine.register_user(self.checker1)
        self.engine.register_user(self.checker2)
        self.engine.register_user(self.admin)


class TestSegregationOfDutiesEngineAdvanced(_EngineFixture):
    def test_successful_dual_control_approval(self):
        # Proposal for $100,000 (requires 2 approvals)
        prop = self.engine.propose_transfer("PROP_001", "USR_MAKER_1", "0x123...", "BTC", 100000.0)
        self.assertEqual(prop.required_approvals, 2)
        self.assertEqual(prop.status, STATUS_PENDING)

        prop = self.engine.approve_transfer("PROP_001", "USR_CHECKER_1")
        self.assertEqual(len(prop.approvals), 1)
        self.assertEqual(prop.status, STATUS_PENDING)

        prop = self.engine.approve_transfer("PROP_001", "USR_CHECKER_2")
        self.assertEqual(len(prop.approvals), 2)
        self.assertEqual(prop.status, STATUS_APPROVED)

    def test_maker_checker_self_approval_blocked(self):
        prop = self.engine.propose_transfer("PROP_002", "USR_MAKER_1", "0x123...", "ETH", 10000.0)
        self.assertEqual(prop.required_approvals, 1)

        # Mutating the caller's own role set must not change the engine's view.
        self.maker.roles.add(CustodyRole.APPROVER)
        self.assertNotIn(CustodyRole.APPROVER, self.engine.users["USR_MAKER_1"].roles)

        with self.assertRaises(SoDConflictError) as ctx:
            self.engine.approve_transfer("PROP_002", "USR_MAKER_1")
        self.assertIn("cannot approve their own transfer", str(ctx.exception))
        # The refusal must name the maker-checker breach, not a role error.
        self.assertEqual(ctx.exception.violation_type, SoDViolationType.SELF_APPROVAL_ATTEMPT)

    def test_invalid_role_combination_on_registration(self):
        bad_user = UserIdentity(
            "USR_BAD", "eve", "Security", {CustodyRole.SECURITY_ADMIN, CustodyRole.INITIATOR}
        )
        with self.assertRaises(SoDConflictError) as ctx:
            self.engine.register_user(bad_user)
        self.assertEqual(
            ctx.exception.violation_type, SoDViolationType.ROLE_CONFLICT_ADMIN_MAKER
        )
        self.assertNotIn("USR_BAD", self.engine.users)


class TestRoleConflictMatrix(_EngineFixture):
    def test_admin_may_not_also_approve(self):
        with self.assertRaises(SoDConflictError) as ctx:
            self.engine.register_user(
                UserIdentity(
                    "USR_X", "frank", "Security",
                    {CustodyRole.SECURITY_ADMIN, CustodyRole.APPROVER},
                )
            )
        self.assertEqual(ctx.exception.violation_type, SoDViolationType.ROLE_CONFLICT)

    def test_auditor_may_not_initiate_or_approve(self):
        for conflicting in (CustodyRole.INITIATOR, CustodyRole.APPROVER, CustodyRole.SECURITY_ADMIN):
            with self.subTest(role=conflicting):
                with self.assertRaises(SoDConflictError):
                    self.engine.register_user(
                        UserIdentity(
                            f"USR_AUD_{conflicting.value}", "grace", "Audit",
                            {CustodyRole.AUDITOR, conflicting},
                        )
                    )

    def test_maker_checker_combination_allowed_by_default_blocked_under_strict(self):
        # Default matrix permits one person to be maker on one workflow and
        # checker on another; the per-proposal self-approval block still holds.
        self.engine.register_user(
            UserIdentity("USR_BOTH", "heidi", "Ops", {CustodyRole.INITIATOR, CustodyRole.APPROVER})
        )
        self.assertIn("USR_BOTH", self.engine.users)

        strict = SegregationOfDutiesForCustodyOperationsEngine(
            incompatible_role_pairs=STRICT_INCOMPATIBLE_ROLE_PAIRS, clock=_StubClock()
        )
        with self.assertRaises(SoDConflictError):
            strict.register_user(
                UserIdentity(
                    "USR_BOTH", "heidi", "Ops", {CustodyRole.INITIATOR, CustodyRole.APPROVER}
                )
            )

    def test_strict_matrix_is_a_superset_of_the_default(self):
        self.assertTrue(DEFAULT_INCOMPATIBLE_ROLE_PAIRS < STRICT_INCOMPATIBLE_ROLE_PAIRS)
        self.assertIn(
            frozenset({CustodyRole.INITIATOR, CustodyRole.APPROVER}),
            STRICT_INCOMPATIBLE_ROLE_PAIRS,
        )

    def test_roleless_user_rejected(self):
        with self.assertRaises(SoDConflictError):
            self.engine.register_user(UserIdentity("USR_EMPTY", "ivan", "Ops", set()))

    def test_reregistration_requires_explicit_replace(self):
        with self.assertRaises(SoDConflictError) as ctx:
            self.engine.register_user(
                UserIdentity("USR_MAKER_1", "alice", "Trading", {CustodyRole.APPROVER})
            )
        self.assertEqual(
            ctx.exception.violation_type, SoDViolationType.DUPLICATE_USER_REGISTRATION
        )
        self.assertEqual(self.engine.users["USR_MAKER_1"].roles, frozenset({CustodyRole.INITIATOR}))

        self.engine.register_user(
            UserIdentity("USR_MAKER_1", "alice", "Trading", {CustodyRole.APPROVER}), replace=True
        )
        self.assertEqual(self.engine.users["USR_MAKER_1"].roles, frozenset({CustodyRole.APPROVER}))


class TestAmountValidationAndThreshold(_EngineFixture):
    def test_non_finite_and_non_positive_amounts_rejected(self):
        for amount in (float("nan"), float("inf"), float("-inf"), 0.0, -1.0):
            with self.subTest(amount=amount):
                with self.assertRaises(SoDConflictError) as ctx:
                    self.engine.propose_transfer("PROP_BAD", "USR_MAKER_1", "0xdead", "BTC", amount)
                self.assertEqual(ctx.exception.violation_type, SoDViolationType.INVALID_PAYLOAD)
                self.assertNotIn("PROP_BAD", self.engine.proposals)

    def test_nan_is_not_silently_treated_as_a_small_transfer(self):
        # Regression: `nan >= threshold` is False, so v1.0.0 routed NaN to the
        # single-approval tier instead of rejecting it.
        with self.assertRaises(SoDConflictError):
            self.engine.required_approvals_for(float("nan"))

    def test_threshold_boundary_is_inclusive(self):
        self.assertEqual(self.engine.required_approvals_for(49999.99), 1)
        self.assertEqual(self.engine.required_approvals_for(50000.0), 2)
        self.assertEqual(self.engine.required_approvals_for(50000.01), 2)

    def test_blank_identifiers_rejected(self):
        for kwargs in (
            {"proposal_id": "  "},
            {"destination_address": ""},
            {"asset_symbol": "   "},
        ):
            with self.subTest(**kwargs):
                args = {
                    "proposal_id": "PROP_BLANK",
                    "initiator_id": "USR_MAKER_1",
                    "destination_address": "0xdead",
                    "asset_symbol": "BTC",
                    "amount_usd": 1000.0,
                }
                args.update(kwargs)
                with self.assertRaises(SoDConflictError):
                    self.engine.propose_transfer(**args)


class TestProposalIdempotency(_EngineFixture):
    def test_identical_resubmission_is_idempotent(self):
        first = self.engine.propose_transfer("PROP_ID", "USR_MAKER_1", "0xdead", "BTC", 100000.0)
        self.engine.approve_transfer("PROP_ID", "USR_CHECKER_1")
        second = self.engine.propose_transfer("PROP_ID", "USR_MAKER_1", "0xdead", "BTC", 100000.0)
        self.assertIs(first, second)
        self.assertEqual(len(second.approvals), 1, "a retry must not discard collected approvals")

    def test_conflicting_reuse_of_proposal_id_raises(self):
        self.engine.propose_transfer("PROP_ID", "USR_MAKER_1", "0xdead", "BTC", 100000.0)
        self.engine.approve_transfer("PROP_ID", "USR_CHECKER_1")
        with self.assertRaises(SoDConflictError) as ctx:
            self.engine.propose_transfer("PROP_ID", "USR_MAKER_1", "0xATTACKER", "BTC", 100000.0)
        self.assertEqual(ctx.exception.violation_type, SoDViolationType.DUPLICATE_PROPOSAL_ID)
        self.assertEqual(self.engine.proposals["PROP_ID"].destination_address, "0xdead")
        self.assertEqual(len(self.engine.proposals["PROP_ID"].approvals), 1)


class TestPayloadBinding(_EngineFixture):
    def _fully_approved(self):
        self.engine.propose_transfer("PROP_B", "USR_MAKER_1", "0xCOLD", "BTC", 100000.0)
        self.engine.approve_transfer("PROP_B", "USR_CHECKER_1")
        proposal = self.engine.approve_transfer("PROP_B", "USR_CHECKER_2")
        self.assertEqual(proposal.status, STATUS_APPROVED)
        return proposal

    def test_destination_change_after_approval_strands_the_approvals(self):
        proposal = self._fully_approved()
        proposal.destination_address = "0xATTACKER"
        self.assertEqual(self.engine.valid_approvals(proposal), [])
        self.assertEqual(self.engine.refresh_status("PROP_B").status, STATUS_PENDING)

    def test_amount_change_after_approval_strands_the_approvals(self):
        proposal = self._fully_approved()
        proposal.amount_usd = 9_000_000.0
        self.assertEqual(self.engine.refresh_status("PROP_B").status, STATUS_PENDING)

    def test_lowering_required_approvals_after_the_fact_strands_the_approvals(self):
        # The threshold is a term of the approval, so it is inside the digest.
        proposal = self._fully_approved()
        proposal.required_approvals = 1
        self.assertEqual(self.engine.refresh_status("PROP_B").status, STATUS_PENDING)

    def test_mutated_payload_cannot_be_executed(self):
        proposal = self._fully_approved()
        proposal.destination_address = "0xATTACKER"
        with self.assertRaises(SoDConflictError) as ctx:
            self.engine.mark_executed("PROP_B", "USR_ADMIN_1")
        self.assertEqual(ctx.exception.violation_type, SoDViolationType.THRESHOLD_NOT_MET)
        self.assertEqual(self.engine.proposals["PROP_B"].status, STATUS_PENDING)

    def test_reverting_the_payload_restores_the_original_approvals(self):
        proposal = self._fully_approved()
        original = proposal.destination_address
        proposal.destination_address = "0xATTACKER"
        self.assertEqual(self.engine.refresh_status("PROP_B").status, STATUS_PENDING)
        proposal.destination_address = original
        self.assertEqual(self.engine.refresh_status("PROP_B").status, STATUS_APPROVED)

    def test_digest_matches_an_independently_derived_value(self):
        proposal = CustodyTransferProposal(
            proposal_id="P1",
            initiator_id="U1",
            destination_address="0xabc",
            asset_symbol="BTC",
            amount_usd=1234.5,
            required_approvals=2,
        )
        fields = [
            DIGEST_DOMAIN, "P1", "U1", "0xabc", "BTC", (1234.5).hex(), "2",
        ]
        expected_bytes = b"".join(
            str(len(f.encode("utf-8"))).encode("ascii") + b":" + f.encode("utf-8") for f in fields
        )
        expected = hashlib.sha256(expected_bytes).hexdigest()
        self.assertEqual(compute_proposal_digest(proposal), expected)

    def test_field_boundary_shuffle_does_not_collide(self):
        left = CustodyTransferProposal("P", "U", "0xAB", "CD", 100.0, 2)
        right = CustodyTransferProposal("P", "U", "0xABC", "D", 100.0, 2)
        self.assertNotEqual(compute_proposal_digest(left), compute_proposal_digest(right))


class TestApprovalGuards(_EngineFixture):
    def test_duplicate_approval_by_same_checker_rejected(self):
        self.engine.propose_transfer("PROP_D", "USR_MAKER_1", "0xCOLD", "BTC", 100000.0)
        self.engine.approve_transfer("PROP_D", "USR_CHECKER_1")
        with self.assertRaises(SoDConflictError) as ctx:
            self.engine.approve_transfer("PROP_D", "USR_CHECKER_1")
        self.assertEqual(ctx.exception.violation_type, SoDViolationType.DUPLICATE_APPROVAL)
        self.assertEqual(len(self.engine.proposals["PROP_D"].approvals), 1)

    def test_admin_cannot_approve(self):
        self.engine.propose_transfer("PROP_D", "USR_MAKER_1", "0xCOLD", "BTC", 100000.0)
        with self.assertRaises(SoDConflictError) as ctx:
            self.engine.approve_transfer("PROP_D", "USR_ADMIN_1")
        self.assertEqual(ctx.exception.violation_type, SoDViolationType.UNAUTHORIZED_ROLE)

    def test_unregistered_approver_rejected(self):
        self.engine.propose_transfer("PROP_D", "USR_MAKER_1", "0xCOLD", "BTC", 100000.0)
        with self.assertRaises(SoDConflictError):
            self.engine.approve_transfer("PROP_D", "GHOST")

    def test_unknown_proposal_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.engine.approve_transfer("NOPE", "USR_CHECKER_1")

    def test_unregistered_initiator_rejected(self):
        with self.assertRaises(SoDConflictError):
            self.engine.propose_transfer("PROP_G", "GHOST", "0xCOLD", "BTC", 1000.0)

    def test_disabled_engine_refuses_both_maker_and_checker_steps(self):
        engine = SegregationOfDutiesForCustodyOperationsEngine(
            SegregationOfDutiesForCustodyOperationsConfig(enabled=True), clock=_StubClock()
        )
        engine.register_user(UserIdentity("M", "m", "Ops", {CustodyRole.INITIATOR}))
        engine.register_user(UserIdentity("C", "c", "Risk", {CustodyRole.APPROVER}))
        engine.propose_transfer("P", "M", "0xCOLD", "BTC", 1000.0)
        engine.config.enabled = False
        for call in (
            lambda: engine.propose_transfer("P2", "M", "0xCOLD", "BTC", 1000.0),
            lambda: engine.approve_transfer("P", "C"),
        ):
            with self.assertRaises(SoDConflictError) as ctx:
                call()
            self.assertEqual(ctx.exception.violation_type, SoDViolationType.ENGINE_DISABLED)


class TestTerminalStates(_EngineFixture):
    def test_execute_once_then_refuse(self):
        self.engine.propose_transfer("PROP_E", "USR_MAKER_1", "0xCOLD", "BTC", 100000.0)
        self.engine.approve_transfer("PROP_E", "USR_CHECKER_1")
        self.engine.approve_transfer("PROP_E", "USR_CHECKER_2")
        executed = self.engine.mark_executed("PROP_E", "USR_ADMIN_1")
        self.assertEqual(executed.status, STATUS_EXECUTED)
        with self.assertRaises(SoDConflictError) as ctx:
            self.engine.mark_executed("PROP_E", "USR_ADMIN_1")
        self.assertEqual(ctx.exception.violation_type, SoDViolationType.PROPOSAL_NOT_PENDING)

    def test_cannot_execute_below_threshold(self):
        self.engine.propose_transfer("PROP_E", "USR_MAKER_1", "0xCOLD", "BTC", 100000.0)
        self.engine.approve_transfer("PROP_E", "USR_CHECKER_1")
        with self.assertRaises(SoDConflictError) as ctx:
            self.engine.mark_executed("PROP_E", "USR_ADMIN_1")
        self.assertEqual(ctx.exception.violation_type, SoDViolationType.THRESHOLD_NOT_MET)

    def test_executed_proposal_accepts_no_further_approvals(self):
        self.engine.propose_transfer("PROP_E", "USR_MAKER_1", "0xCOLD", "BTC", 10000.0)
        self.engine.approve_transfer("PROP_E", "USR_CHECKER_1")
        self.engine.mark_executed("PROP_E", "USR_ADMIN_1")
        with self.assertRaises(SoDConflictError) as ctx:
            self.engine.approve_transfer("PROP_E", "USR_CHECKER_2")
        self.assertEqual(ctx.exception.violation_type, SoDViolationType.PROPOSAL_NOT_PENDING)

    def test_checker_may_reject_after_approving(self):
        self.engine.propose_transfer("PROP_R", "USR_MAKER_1", "0xCOLD", "BTC", 100000.0)
        self.engine.approve_transfer("PROP_R", "USR_CHECKER_1")
        rejected = self.engine.reject_transfer("PROP_R", "USR_CHECKER_1", "destination unverified")
        self.assertEqual(rejected.status, STATUS_REJECTED)
        self.assertEqual(rejected.resolution_reason, "destination unverified")
        with self.assertRaises(SoDConflictError):
            self.engine.approve_transfer("PROP_R", "USR_CHECKER_2")

    def test_rejected_proposal_cannot_be_executed(self):
        self.engine.propose_transfer("PROP_R", "USR_MAKER_1", "0xCOLD", "BTC", 10000.0)
        self.engine.approve_transfer("PROP_R", "USR_CHECKER_1")
        self.engine.reject_transfer("PROP_R", "USR_ADMIN_1", "kill switch")
        with self.assertRaises(SoDConflictError):
            self.engine.mark_executed("PROP_R", "USR_ADMIN_1")

    def test_unauthorised_user_cannot_reject(self):
        self.engine.register_user(UserIdentity("USR_AUD", "judy", "Audit", {CustodyRole.AUDITOR}))
        self.engine.propose_transfer("PROP_R", "USR_MAKER_1", "0xCOLD", "BTC", 10000.0)
        with self.assertRaises(SoDConflictError) as ctx:
            self.engine.reject_transfer("PROP_R", "USR_AUD", "no")
        self.assertEqual(ctx.exception.violation_type, SoDViolationType.UNAUTHORIZED_ROLE)

    def test_rejection_requires_a_reason(self):
        self.engine.propose_transfer("PROP_R", "USR_MAKER_1", "0xCOLD", "BTC", 10000.0)
        with self.assertRaises(SoDConflictError):
            self.engine.reject_transfer("PROP_R", "USR_CHECKER_1", "   ")


class TestDepartmentalIndependence(unittest.TestCase):
    def _engine(self, **config_kwargs):
        engine = SegregationOfDutiesForCustodyOperationsEngine(
            SegregationOfDutiesForCustodyOperationsConfig(**config_kwargs),
            large_transfer_threshold_usd=50000.0,
            clock=_StubClock(),
        )
        engine.register_user(UserIdentity("M", "alice", "Trading", {CustodyRole.INITIATOR}))
        engine.register_user(UserIdentity("C1", "bob", "Trading", {CustodyRole.APPROVER}))
        engine.register_user(UserIdentity("C2", "carol", "trading", {CustodyRole.APPROVER}))
        engine.register_user(UserIdentity("C3", "dan", "Compliance", {CustodyRole.APPROVER}))
        return engine

    def test_two_approvals_from_one_desk_do_not_satisfy_a_two_department_quorum(self):
        engine = self._engine(
            approvals_below_threshold=2, min_distinct_approver_departments=2
        )
        engine.propose_transfer("P", "M", "0xCOLD", "BTC", 100000.0)
        engine.approve_transfer("P", "C1")
        # C2's department differs only by case; it must not count as distinct.
        proposal = engine.approve_transfer("P", "C2")
        self.assertEqual(len(proposal.approvals), 2)
        self.assertEqual(proposal.status, STATUS_PENDING)

    def test_a_cross_department_quorum_approves(self):
        engine = self._engine(
            approvals_below_threshold=2, min_distinct_approver_departments=2
        )
        engine.propose_transfer("P", "M", "0xCOLD", "BTC", 100000.0)
        engine.approve_transfer("P", "C1")
        proposal = engine.approve_transfer("P", "C3")
        self.assertEqual(proposal.status, STATUS_APPROVED)

    def test_department_check_is_off_by_default(self):
        engine = self._engine()
        engine.propose_transfer("P", "M", "0xCOLD", "BTC", 100000.0)
        engine.approve_transfer("P", "C1")
        self.assertEqual(engine.approve_transfer("P", "C2").status, STATUS_APPROVED)

    def test_approver_from_the_initiators_desk_can_be_forbidden(self):
        engine = self._engine(forbid_approver_from_initiator_department=True)
        engine.propose_transfer("P", "M", "0xCOLD", "BTC", 10000.0)
        with self.assertRaises(SoDConflictError) as ctx:
            engine.approve_transfer("P", "C1")
        self.assertEqual(
            ctx.exception.violation_type, SoDViolationType.INSUFFICIENT_DEPARTMENT_SEPARATION
        )
        self.assertEqual(engine.approve_transfer("P", "C3").status, STATUS_APPROVED)


class TestConfigValidation(unittest.TestCase):
    def test_zero_approvals_rejected(self):
        with self.assertRaises(SoDConflictError):
            SegregationOfDutiesForCustodyOperationsEngine(
                SegregationOfDutiesForCustodyOperationsConfig(approvals_below_threshold=0)
            )

    def test_unreachable_department_minimum_rejected(self):
        with self.assertRaises(SoDConflictError):
            SegregationOfDutiesForCustodyOperationsEngine(
                SegregationOfDutiesForCustodyOperationsConfig(
                    approvals_below_threshold=1,
                    approvals_at_or_above_threshold=2,
                    min_distinct_approver_departments=2,
                )
            )

    def test_negative_and_non_finite_threshold_rejected(self):
        for threshold in (-1.0, float("nan")):
            with self.subTest(threshold=threshold):
                with self.assertRaises(SoDConflictError):
                    SegregationOfDutiesForCustodyOperationsEngine(
                        large_transfer_threshold_usd=threshold
                    )

    def test_zero_threshold_means_every_transfer_is_large(self):
        engine = SegregationOfDutiesForCustodyOperationsEngine(
            large_transfer_threshold_usd=0.0, clock=_StubClock()
        )
        self.assertEqual(engine.required_approvals_for(0.01), 2)


class TestAuditChain(_EngineFixture):
    def test_chain_starts_at_genesis_and_verifies(self):
        engine = SegregationOfDutiesForCustodyOperationsEngine(clock=_StubClock())
        self.assertEqual(engine.chain_head_hash, GENESIS_HASH)
        self.assertEqual(engine.verify_audit_chain(), (True, None))

    def test_lifecycle_events_are_all_chained(self):
        self.engine.propose_transfer("PROP_A", "USR_MAKER_1", "0xCOLD", "BTC", 100000.0)
        self.engine.approve_transfer("PROP_A", "USR_CHECKER_1")
        self.engine.approve_transfer("PROP_A", "USR_CHECKER_2")
        self.engine.mark_executed("PROP_A", "USR_ADMIN_1")
        events = [entry.event_type for entry in self.engine.audit_trail()]
        self.assertEqual(
            events,
            ["USER_REGISTERED"] * 4
            + ["PROPOSAL_CREATED", "APPROVAL_RECORDED", "APPROVAL_RECORDED", "PROPOSAL_EXECUTED"],
        )
        ok, reason = self.engine.verify_audit_chain()
        self.assertTrue(ok, reason)

    def test_approval_hashes_are_distinct_and_match_the_chain(self):
        self.engine.propose_transfer("PROP_A", "USR_MAKER_1", "0xCOLD", "BTC", 100000.0)
        self.engine.approve_transfer("PROP_A", "USR_CHECKER_1")
        proposal = self.engine.approve_transfer("PROP_A", "USR_CHECKER_2")
        hashes = [record.signature_hash for record in proposal.approvals]
        self.assertEqual(len(set(hashes)), 2)
        chain_hashes = {entry.entry_hash for entry in self.engine.audit_trail()}
        self.assertTrue(set(hashes) <= chain_hashes)

    def test_edited_entry_is_detected(self):
        self.engine.propose_transfer("PROP_A", "USR_MAKER_1", "0xCOLD", "BTC", 100000.0)
        self.engine.approve_transfer("PROP_A", "USR_CHECKER_1")
        target = self.engine.audit_trail()[-1]
        target.actor_id = "USR_CHECKER_9"
        ok, reason = self.engine.verify_audit_chain()
        self.assertFalse(ok)
        self.assertIn(str(target.sequence), reason)

    def test_deleted_entry_is_detected(self):
        self.engine.propose_transfer("PROP_A", "USR_MAKER_1", "0xCOLD", "BTC", 100000.0)
        self.engine.approve_transfer("PROP_A", "USR_CHECKER_1")
        del self.engine._audit_log[2]
        self.assertFalse(self.engine.verify_audit_chain()[0])

    def test_rejected_registration_does_not_advance_the_chain(self):
        head_before = self.engine.chain_head_hash
        with self.assertRaises(SoDConflictError):
            self.engine.register_user(
                UserIdentity(
                    "USR_BAD", "eve", "Sec", {CustodyRole.SECURITY_ADMIN, CustodyRole.INITIATOR}
                )
            )
        self.assertEqual(self.engine.chain_head_hash, head_before)

    def test_injected_clock_makes_the_chain_reproducible(self):
        def build():
            engine = SegregationOfDutiesForCustodyOperationsEngine(clock=_StubClock())
            engine.register_user(UserIdentity("M", "m", "Ops", {CustodyRole.INITIATOR}))
            engine.register_user(UserIdentity("C", "c", "Risk", {CustodyRole.APPROVER}))
            engine.propose_transfer("P", "M", "0xCOLD", "BTC", 1000.0)
            engine.approve_transfer("P", "C")
            return engine.chain_head_hash

        self.assertEqual(build(), build())


if __name__ == "__main__":
    unittest.main()
