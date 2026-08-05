import hashlib
import math
import unittest
from datetime import datetime, timedelta, timezone

from risk_config_approval_workflow import (
    Actor,
    ApprovalPolicy,
    AuthorizationError,
    ChangeState,
    InMemoryVersionedConfigStore,
    IntegrityError,
    RiskConfigApprovalWorkflow,
    StateTransitionError,
    ValidationError,
    VersionConflict,
)


NOW = datetime(2026, 8, 3, 9, 0, tzinfo=timezone.utc)
MAKER = Actor("quant-1", frozenset({"QUANT", "RISK_CONFIG_REQUESTER"}))
CHECKER = Actor("risk-1", frozenset({"RISK_MANAGER"}))
SECOND_CHECKER = Actor("risk-2", frozenset({"RISK_MANAGER"}))
DEPLOYER = Actor("deploy-service", frozenset({"RISK_CONFIG_DEPLOYER"}))


def sha256(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_risk_config(config):
    max_order_notional = config.get("max_order_notional")
    max_position_notional = config.get("max_position_notional")
    if not isinstance(max_order_notional, (int, float)) or max_order_notional <= 0:
        raise ValueError("max_order_notional must be positive")
    if not isinstance(max_position_notional, (int, float)) or max_position_notional <= 0:
        raise ValueError("max_position_notional must be positive")
    if max_order_notional > max_position_notional:
        raise ValueError("order limit cannot exceed position limit")


class RiskConfigApprovalWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryVersionedConfigStore(
            {
                "production": {
                    "max_order_notional": 100_000,
                    "max_position_notional": 1_000_000,
                }
            }
        )
        self.workflow = RiskConfigApprovalWorkflow(self.store, validate_risk_config)

    def submit(self, **overrides):
        values = {
            "request_id": "chg-001",
            "environment": "production",
            "base_version": 1,
            "proposed_config": {
                "max_order_notional": 75_000,
                "max_position_notional": 900_000,
            },
            "submitter": MAKER,
            "reason": "Reduce exposure before event risk",
            "ticket_id": "RISK-42",
            "now": NOW,
        }
        values.update(overrides)
        return self.workflow.submit_change(**values)

    def approve(self, request, actor=CHECKER, **overrides):
        return self.workflow.approve(
            request.request_id,
            approver=actor,
            expected_digest=request.config_digest,
            now=overrides.get("now", NOW + timedelta(minutes=5)),
        )

    def test_happy_path_applies_exact_approved_payload(self):
        request = self.submit()
        approved = self.approve(request)
        applied = self.workflow.apply_change(
            request.request_id,
            deployer=DEPLOYER,
            expected_digest=request.config_digest,
            now=NOW + timedelta(minutes=10),
        )

        self.assertEqual(approved.state, ChangeState.APPROVED)
        self.assertEqual(applied.state, ChangeState.APPLIED)
        self.assertEqual(applied.applied_version, 2)
        self.assertEqual(self.store.read("production").config, request.proposed_config)
        self.assertEqual(
            [event.event_type for event in self.workflow.audit_events()],
            ["CHANGE_SUBMITTED", "CHANGE_APPROVED", "CHANGE_APPLIED"],
        )

    def test_submission_snapshots_mutable_input_and_digest_is_canonical(self):
        proposed = {
            "max_position_notional": 900_000,
            "max_order_notional": 75_000,
        }
        first = self.submit(proposed_config=proposed)
        proposed["max_order_notional"] = 999_999
        retry = self.submit(
            proposed_config={
                "max_order_notional": 75_000,
                "max_position_notional": 900_000,
            }
        )

        self.assertEqual(first.config_digest, retry.config_digest)
        self.assertEqual(first.proposed_config["max_order_notional"], 75_000)

    def test_request_id_retry_is_idempotent_but_collision_fails(self):
        first = self.submit()
        self.assertEqual(self.submit(), first)
        with self.assertRaises(IntegrityError):
            self.submit(
                proposed_config={
                    "max_order_notional": 70_000,
                    "max_position_notional": 900_000,
                }
            )
        with self.assertRaises(IntegrityError):
            self.submit(reason="Different immutable review context")

    def test_maker_cannot_approve_and_role_is_enforced(self):
        request = self.submit()
        with self.assertRaises(AuthorizationError):
            self.approve(request, MAKER)
        with self.assertRaises(AuthorizationError):
            self.approve(request, Actor("ops-1", frozenset({"OPS"})))

    def test_submitter_role_is_enforced(self):
        with self.assertRaises(AuthorizationError):
            self.submit(submitter=Actor("quant-2", frozenset({"QUANT"})))

    def test_approval_is_idempotent_and_bound_to_digest(self):
        request = self.submit()
        approved = self.approve(request)
        duplicate = self.approve(request)
        self.assertEqual(len(duplicate.approvals), 1)
        self.assertEqual(len(self.workflow.audit_events()), 2)
        with self.assertRaises(IntegrityError):
            self.workflow.approve(
                request.request_id,
                approver=SECOND_CHECKER,
                expected_digest="0" * 64,
                now=NOW + timedelta(minutes=6),
            )
        self.assertEqual(approved.config_digest, approved.approvals[0].config_digest)

    def test_multi_checker_quorum(self):
        workflow = RiskConfigApprovalWorkflow(
            self.store,
            validate_risk_config,
            ApprovalPolicy(required_approvals=2),
        )
        self.workflow = workflow
        request = self.submit()
        pending = self.approve(request)
        approved = self.approve(request, SECOND_CHECKER, now=NOW + timedelta(minutes=6))
        self.assertEqual(pending.state, ChangeState.PENDING)
        self.assertEqual(approved.state, ChangeState.APPROVED)

    def test_expired_change_fails_closed(self):
        request = self.submit()
        with self.assertRaises(StateTransitionError):
            self.approve(request, now=NOW + timedelta(hours=24))
        self.assertEqual(self.workflow.get_request(request.request_id).state, ChangeState.EXPIRED)

    def test_rejection_and_cancellation_are_terminal(self):
        request = self.submit()
        rejected = self.workflow.reject(
            request.request_id,
            reviewer=CHECKER,
            expected_digest=request.config_digest,
            reason="Evidence is incomplete",
            now=NOW + timedelta(minutes=2),
        )
        self.assertEqual(rejected.state, ChangeState.REJECTED)
        with self.assertRaises(StateTransitionError):
            self.approve(request)

        second = self.submit(request_id="chg-002")
        cancelled = self.workflow.cancel(
            second.request_id,
            submitter=MAKER,
            expected_digest=second.config_digest,
            now=NOW + timedelta(minutes=1),
        )
        self.assertEqual(cancelled.state, ChangeState.CANCELLED)

    def test_apply_requires_approval_and_deployer_role(self):
        request = self.submit()
        with self.assertRaises(StateTransitionError):
            self.workflow.apply_change(
                request.request_id,
                deployer=DEPLOYER,
                expected_digest=request.config_digest,
                now=NOW + timedelta(minutes=1),
            )
        approved = self.approve(request)
        with self.assertRaises(AuthorizationError):
            self.workflow.apply_change(
                approved.request_id,
                deployer=CHECKER,
                expected_digest=approved.config_digest,
                now=NOW + timedelta(minutes=6),
            )

    def test_optional_independent_applier_policy(self):
        workflow = RiskConfigApprovalWorkflow(
            self.store,
            validate_risk_config,
            ApprovalPolicy(
                deployer_roles=frozenset({"RISK_MANAGER", "RISK_CONFIG_DEPLOYER"}),
                require_independent_applier=True,
            ),
        )
        self.workflow = workflow
        request = self.approve(self.submit())
        with self.assertRaises(AuthorizationError):
            workflow.apply_change(
                request.request_id,
                deployer=CHECKER,
                expected_digest=request.config_digest,
                now=NOW + timedelta(minutes=10),
            )

    def test_stale_base_version_blocks_submission_and_apply(self):
        request = self.approve(self.submit())
        external_config = '{"max_order_notional":80000,"max_position_notional":950000}'
        self.store.apply(
            request_id="external-change",
            environment="production",
            expected_version=1,
            config_json=external_config,
            request_digest="a" * 64,
            config_digest=sha256(external_config),
        )
        with self.assertRaises(VersionConflict):
            self.workflow.apply_change(
                request.request_id,
                deployer=DEPLOYER,
                expected_digest=request.config_digest,
                now=NOW + timedelta(minutes=10),
            )
        self.assertEqual(self.workflow.get_request(request.request_id).state, ChangeState.APPROVED)

        with self.assertRaises(VersionConflict):
            self.submit(request_id="chg-002", base_version=1)

    def test_apply_retry_and_lost_response_reconciliation(self):
        request = self.approve(self.submit())
        first = self.workflow.apply_change(
            request.request_id,
            deployer=DEPLOYER,
            expected_digest=request.config_digest,
            now=NOW + timedelta(minutes=10),
        )
        retry = self.workflow.apply_change(
            request.request_id,
            deployer=DEPLOYER,
            expected_digest=request.config_digest,
            now=NOW + timedelta(minutes=11),
        )
        self.assertEqual(first, retry)
        self.assertEqual(len(self.workflow.audit_events()), 3)
        self.assertEqual(self.submit(), first)

        other_store = InMemoryVersionedConfigStore(
            {"production": {"max_order_notional": 100_000, "max_position_notional": 1_000_000}}
        )
        other = RiskConfigApprovalWorkflow(other_store, validate_risk_config)
        pending = other.submit_change(
            request_id="chg-lost-response",
            environment="production",
            base_version=1,
            proposed_config={"max_order_notional": 50_000, "max_position_notional": 800_000},
            submitter=MAKER,
            reason="De-risk",
            ticket_id="RISK-43",
            now=NOW,
        )
        other.approve(
            pending.request_id,
            approver=CHECKER,
            expected_digest=pending.config_digest,
            now=NOW + timedelta(minutes=1),
        )
        applied_config = '{"max_order_notional":50000,"max_position_notional":800000}'
        other_store.apply(
            request_id=pending.request_id,
            environment="production",
            expected_version=1,
            config_json=applied_config,
            request_digest=pending.config_digest,
            config_digest=sha256(applied_config),
        )
        repaired = other.reconcile(pending.request_id, now=NOW + timedelta(minutes=2))
        self.assertEqual(repaired.state, ChangeState.APPLIED)
        self.assertEqual(repaired.applied_version, 2)

    def test_domain_json_secret_and_noop_validation(self):
        with self.assertRaises(ValidationError):
            self.submit(
                proposed_config={
                    "max_order_notional": 2_000_000,
                    "max_position_notional": 1_000_000,
                }
            )
        with self.assertRaises(ValidationError):
            self.submit(
                proposed_config={
                    "max_order_notional": math.nan,
                    "max_position_notional": 1_000_000,
                }
            )
        with self.assertRaises(ValidationError):
            self.submit(
                proposed_config={
                    "max_order_notional": 50_000,
                    "max_position_notional": 1_000_000,
                    "credentials": {"api_key": "must-not-be-here"},
                }
            )
        with self.assertRaises(ValidationError):
            self.submit(
                proposed_config={
                    "max_order_notional": 100_000,
                    "max_position_notional": 1_000_000,
                }
            )

    def test_timestamps_must_be_timezone_aware(self):
        with self.assertRaises(ValidationError):
            self.submit(now=datetime(2026, 8, 3, 9, 0))

    def test_audit_chain_contains_no_configuration_values(self):
        request = self.approve(self.submit())
        events = self.workflow.audit_events()
        self.assertEqual(events[1].previous_hash, events[0].event_hash)
        self.assertNotEqual(events[0].event_hash, events[1].event_hash)
        self.assertNotIn("max_order_notional", repr(events))
        self.assertIn(request.config_digest, repr(events))
        with self.assertRaises(TypeError):
            events[0].details["state"] = "TAMPERED"


if __name__ == "__main__":
    unittest.main()
