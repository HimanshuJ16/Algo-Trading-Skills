"""Tests for the FINRA Rule 1220(b)(4) Securities Trader registration gate.

Expected values are derived from the primary sources, not from the
implementation: FINRA Rule 1220(b)(4)(A)(iii) and (B), Rule 1220(a)(7), Rule
1240(a), and Regulatory Notice 16-21 (effective 30 Jan 2017), whose definitions
of "algorithmic trading strategy", "significant modification" and the covered
product set drive every scope test below.
"""

import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

from finra_algo_trading_registration_requirements import (
    ACTIVITY_DAY_TO_DAY_SUPERVISION,
    ACTIVITY_DESIGN,
    ACTIVITY_DEVELOPMENT,
    ACTIVITY_INFRASTRUCTURE_INTEGRATION,
    ACTIVITY_MINOR_MODIFICATION,
    ACTIVITY_PERFORMANCE_MONITORING,
    ACTIVITY_SIGNIFICANT_MODIFICATION,
    ACTIVITY_THIRD_PARTY_DIRECTION,
    BASIS_NOT_IDENTIFIED,
    BASIS_NOT_QUALIFIED,
    BASIS_SECURITIES_TRADER,
    BASIS_SECURITIES_TRADER_PRINCIPAL,
    GATE_APPROVED,
    GATE_BLOCKED,
    GATE_OUT_OF_SCOPE,
    SCOPE_APPLICABLE,
    SCOPE_OUT_ACTIVITY,
    SCOPE_OUT_NOT_ALGO_STRATEGY,
    SCOPE_OUT_NOT_FINRA_MEMBER,
    SCOPE_OUT_NOT_PRIMARILY_RESPONSIBLE,
    SCOPE_OUT_SECURITY_TYPE,
    SECURITY_CONVERTIBLE_DEBT,
    SECURITY_CRYPTO,
    SECURITY_EQUITY,
    SECURITY_EQUITY_OPTION,
    SECURITY_FUTURE,
    SECURITY_PREFERRED,
    SYSTEM_GENERATES_OR_ROUTES_ORDERS,
    SYSTEM_IDEA_GENERATION_ONLY,
    SYSTEM_SOLELY_ROUTES_ENTIRE_ORDERS,
    VIOLATION_AUTHOR_CE_INACTIVE,
    VIOLATION_AUTHOR_NO_SERIES_57,
    VIOLATION_AUTHOR_UNKNOWN,
    VIOLATION_SELF_APPROVAL,
    VIOLATION_SUPERVISOR_NO_SERIES_57,
    VIOLATION_SUPERVISOR_UNIDENTIFIED,
    AlgoCodeCommitRequest,
    DeveloperCredentials,
    FinraAlgoRegistrationEngine,
)

FROZEN_NOW = datetime(2026, 3, 2, 14, 30, 5, tzinfo=timezone.utc)


def frozen_clock():
    return FROZEN_NOW


class BaseEngineTest(unittest.TestCase):
    """Shared personnel fixtures."""

    def setUp(self):
        self.engine = FinraAlgoRegistrationEngine(clock=frozen_clock)

        # Registered Securities Trader (SIE by examination).
        self.engine.register_personnel(
            DeveloperCredentials(
                "DEV_A", "Alice Quant", "QUANT_DEV",
                is_series_57_active=True, is_sie_active=True, crd_number="123456",
            )
        )
        # Securities Trader Principal: Series 57 + General Securities Principal.
        self.engine.register_personnel(
            DeveloperCredentials(
                "SUP_A", "Bob Manager", "DESK_HEAD",
                is_series_57_active=True, is_sie_active=True, crd_number="654321",
                is_general_securities_principal=True,
            )
        )
        # Software engineer with no Securities Trader registration.
        self.engine.register_personnel(
            DeveloperCredentials(
                "DEV_B", "Charlie Unregistered", "SOFTWARE_ENG",
                is_series_57_active=False, is_sie_active=True,
            )
        )
        # Registered before 1 Oct 2018: no SIE exam record, deemed to have passed.
        self.engine.register_personnel(
            DeveloperCredentials(
                "DEV_LEGACY", "Dana Veteran", "SENIOR_TRADER",
                is_series_57_active=True, is_sie_active=False,
                is_sie_grandfathered=True, crd_number="222333",
            )
        )
        # Registered but CE inactive (missed the annual Regulatory Element).
        self.engine.register_personnel(
            DeveloperCredentials(
                "DEV_CE", "Erin Lapsed", "QUANT_DEV",
                is_series_57_active=True, is_sie_active=True, is_ce_inactive=True,
            )
        )
        # Plain Securities Trader supervisor (no Series 24).
        self.engine.register_personnel(
            DeveloperCredentials(
                "SUP_TRADER", "Frank Trader", "SENIOR_TRADER",
                is_series_57_active=True, is_sie_active=True,
            )
        )

    @staticmethod
    def covered_commit(**overrides):
        """A significant modification to an equity algo: squarely in scope."""
        params = dict(
            commit_id="COMMIT_101",
            algorithm_id="VWAP_V2",
            algorithm_name="VWAP Router",
            author_id="DEV_A",
            approving_supervisor_id="SUP_A",
            security_type=SECURITY_EQUITY,
            system_behavior=SYSTEM_GENERATES_OR_ROUTES_ORDERS,
            author_activity=ACTIVITY_SIGNIFICANT_MODIFICATION,
        )
        params.update(overrides)
        return AlgoCodeCommitRequest(**params)


class TestCoveredActivityAndApproval(BaseEngineTest):

    def test_registered_developer_commit_approved(self):
        report = self.engine.audit_code_commit(self.covered_commit())

        self.assertTrue(report.is_rule_1220b4_applicable)
        self.assertEqual(report.scope_reason, SCOPE_APPLICABLE)
        self.assertTrue(report.author_series_57_valid)
        self.assertEqual(report.cicd_gate_status, GATE_APPROVED)
        self.assertFalse(report.blocks_deployment)
        self.assertEqual(report.violations, ())

    def test_unregistered_developer_commit_blocked(self):
        report = self.engine.audit_code_commit(
            self.covered_commit(commit_id="COMMIT_102", author_id="DEV_B")
        )

        self.assertTrue(report.is_rule_1220b4_applicable)
        self.assertFalse(report.author_series_57_valid)
        self.assertEqual(report.cicd_gate_status, GATE_BLOCKED)
        self.assertTrue(report.blocks_deployment)
        self.assertIn(VIOLATION_AUTHOR_NO_SERIES_57, report.violations)

    def test_initial_design_of_new_algorithm_is_in_scope(self):
        # Regression: the rule covers "design, development OR significant
        # modification". A brand-new algorithm is not a modification, so a gate
        # keyed on is_significant_modification alone would wrongly approve it.
        report = self.engine.audit_code_commit(
            self.covered_commit(
                commit_id="COMMIT_NEW",
                author_id="DEV_B",
                author_activity=ACTIVITY_DESIGN,
                is_significant_modification=False,
            )
        )

        self.assertTrue(report.is_rule_1220b4_applicable)
        self.assertEqual(report.cicd_gate_status, GATE_BLOCKED)
        self.assertIn(VIOLATION_AUTHOR_NO_SERIES_57, report.violations)

    def test_development_activity_is_in_scope(self):
        report = self.engine.audit_code_commit(
            self.covered_commit(author_id="DEV_B", author_activity=ACTIVITY_DEVELOPMENT)
        )
        self.assertEqual(report.cicd_gate_status, GATE_BLOCKED)

    def test_third_party_direction_is_in_scope(self):
        # Notice 16-21: the associated person directing a third party in the
        # design/development of an algorithm must be a Securities Trader.
        report = self.engine.audit_code_commit(
            self.covered_commit(author_id="DEV_B", author_activity=ACTIVITY_THIRD_PARTY_DIRECTION)
        )
        self.assertTrue(report.is_rule_1220b4_applicable)
        self.assertEqual(report.cicd_gate_status, GATE_BLOCKED)

    def test_performance_monitoring_is_in_scope(self):
        # Even for an unmodified off-the-shelf algorithm, the person responsible
        # for monitoring or reviewing its performance must be a Securities Trader.
        report = self.engine.audit_code_commit(
            self.covered_commit(author_id="DEV_B", author_activity=ACTIVITY_PERFORMANCE_MONITORING)
        )
        self.assertTrue(report.is_rule_1220b4_applicable)
        self.assertEqual(report.cicd_gate_status, GATE_BLOCKED)

    def test_day_to_day_supervision_is_in_scope(self):
        report = self.engine.audit_code_commit(
            self.covered_commit(author_id="DEV_B", author_activity=ACTIVITY_DAY_TO_DAY_SUPERVISION)
        )
        self.assertTrue(report.is_rule_1220b4_applicable)


class TestProductScope(BaseEngineTest):

    def test_futures_algorithm_is_out_of_scope(self):
        # Rule 1220(b)(4)(A)(iii) reaches equity, preferred and convertible debt
        # only. A futures algo by an unregistered author is NOT a FINRA
        # registration violation, and must not be reported as one.
        report = self.engine.audit_code_commit(
            self.covered_commit(author_id="DEV_B", security_type=SECURITY_FUTURE)
        )

        self.assertFalse(report.is_rule_1220b4_applicable)
        self.assertEqual(report.scope_reason, SCOPE_OUT_SECURITY_TYPE)
        self.assertEqual(report.cicd_gate_status, GATE_OUT_OF_SCOPE)
        self.assertFalse(report.blocks_deployment)
        self.assertEqual(report.violations, ())

    def test_crypto_algorithm_is_out_of_scope(self):
        report = self.engine.audit_code_commit(
            self.covered_commit(author_id="DEV_B", security_type=SECURITY_CRYPTO)
        )
        self.assertEqual(report.scope_reason, SCOPE_OUT_SECURITY_TYPE)

    def test_equity_option_is_covered(self):
        # Notice 16-21: covered systems act in "any equity security (including
        # options), preferred security or convertible debt security".
        report = self.engine.audit_code_commit(
            self.covered_commit(author_id="DEV_B", security_type=SECURITY_EQUITY_OPTION)
        )
        self.assertTrue(report.is_rule_1220b4_applicable)
        self.assertEqual(report.cicd_gate_status, GATE_BLOCKED)

    def test_preferred_and_convertible_debt_are_covered(self):
        for security_type in (SECURITY_PREFERRED, SECURITY_CONVERTIBLE_DEBT):
            with self.subTest(security_type=security_type):
                report = self.engine.audit_code_commit(
                    self.covered_commit(author_id="DEV_B", security_type=security_type)
                )
                self.assertTrue(report.is_rule_1220b4_applicable)

    def test_out_of_scope_status_is_not_approval(self):
        report = self.engine.audit_code_commit(
            self.covered_commit(security_type=SECURITY_FUTURE)
        )
        self.assertNotEqual(report.cicd_gate_status, GATE_APPROVED)

    def test_unmapped_security_type_raises_rather_than_exiting_scope(self):
        with self.assertRaises(ValueError):
            self.engine.audit_code_commit(self.covered_commit(security_type="WEATHER_DERIVATIVE"))

    def test_security_type_token_is_case_insensitive(self):
        report = self.engine.audit_code_commit(self.covered_commit(security_type="equity"))
        self.assertEqual(report.security_type, SECURITY_EQUITY)
        self.assertTrue(report.is_rule_1220b4_applicable)


class TestAlgorithmicTradingStrategyDefinition(BaseEngineTest):

    def test_pure_order_router_is_not_an_algorithmic_trading_strategy(self):
        # "a standard order router that routes retail orders in their entirety to
        # a particular market center ... is not covered".
        report = self.engine.audit_code_commit(
            self.covered_commit(
                author_id="DEV_B", system_behavior=SYSTEM_SOLELY_ROUTES_ENTIRE_ORDERS
            )
        )
        self.assertFalse(report.is_rule_1220b4_applicable)
        self.assertEqual(report.scope_reason, SCOPE_OUT_NOT_ALGO_STRATEGY)
        self.assertFalse(report.requires_change_management_review)

    def test_idea_generation_only_system_is_out_of_scope(self):
        # An algorithm that generates trading ideas but cannot emit orders or
        # order-related messages is not an algorithmic trading strategy.
        report = self.engine.audit_code_commit(
            self.covered_commit(author_id="DEV_B", system_behavior=SYSTEM_IDEA_GENERATION_ONLY)
        )
        self.assertEqual(report.scope_reason, SCOPE_OUT_NOT_ALGO_STRATEGY)
        self.assertEqual(report.cicd_gate_status, GATE_OUT_OF_SCOPE)


class TestPersonScope(BaseEngineTest):

    def test_minor_modification_is_out_of_scope_but_needs_change_management(self):
        # A data-feed change is not a "significant modification", yet Notice 15-09
        # change management still applies to the algorithmic strategy.
        report = self.engine.audit_code_commit(
            self.covered_commit(author_id="DEV_B", author_activity=ACTIVITY_MINOR_MODIFICATION)
        )
        self.assertFalse(report.is_rule_1220b4_applicable)
        self.assertEqual(report.scope_reason, SCOPE_OUT_ACTIVITY)
        self.assertTrue(report.requires_change_management_review)

    def test_infrastructure_integration_is_out_of_scope(self):
        # Notice 16-21 endnote 4: integrating the algorithm into the firm's
        # infrastructure and testing linkages need not be done by a Securities Trader.
        report = self.engine.audit_code_commit(
            self.covered_commit(
                author_id="DEV_B", author_activity=ACTIVITY_INFRASTRUCTURE_INTEGRATION
            )
        )
        self.assertEqual(report.scope_reason, SCOPE_OUT_ACTIVITY)

    def test_junior_contributor_not_primarily_responsible_is_out_of_scope(self):
        report = self.engine.audit_code_commit(
            self.covered_commit(author_id="DEV_B", author_primarily_responsible=False)
        )
        self.assertFalse(report.is_rule_1220b4_applicable)
        self.assertEqual(report.scope_reason, SCOPE_OUT_NOT_PRIMARILY_RESPONSIBLE)

    def test_non_finra_member_firm_is_entirely_out_of_scope(self):
        engine = FinraAlgoRegistrationEngine(is_finra_member=False, clock=frozen_clock)
        report = engine.audit_code_commit(self.covered_commit(author_id="DEV_B"))
        self.assertEqual(report.scope_reason, SCOPE_OUT_NOT_FINRA_MEMBER)
        self.assertFalse(report.blocks_deployment)

    def test_unmapped_activity_raises(self):
        with self.assertRaises(ValueError):
            self.engine.audit_code_commit(self.covered_commit(author_activity="REFACTORING"))


class TestQualificationEvaluation(BaseEngineTest):

    def test_ce_inactive_registration_does_not_qualify(self):
        # Rule 1240(a)(3): a CE inactive person must cease all activities
        # requiring registration, even with an otherwise active Series 57.
        report = self.engine.audit_code_commit(self.covered_commit(author_id="DEV_CE"))

        self.assertEqual(report.cicd_gate_status, GATE_BLOCKED)
        self.assertIn(VIOLATION_AUTHOR_CE_INACTIVE, report.violations)
        self.assertFalse(report.author_series_57_valid)

    def test_pre_2018_registrant_without_sie_exam_is_approved(self):
        # Rule 1220(b)(4)(B): a Securities Trader registered before 1 Oct 2018 who
        # maintained registration is considered to have passed the SIE. Blocking
        # such a person would be a false positive.
        report = self.engine.audit_code_commit(self.covered_commit(author_id="DEV_LEGACY"))

        self.assertEqual(report.cicd_gate_status, GATE_APPROVED)
        self.assertTrue(report.author_series_57_valid)

    def test_unknown_author_is_blocked_and_distinguishable(self):
        report = self.engine.audit_code_commit(self.covered_commit(author_id="GHOST_DEV"))

        self.assertEqual(report.cicd_gate_status, GATE_BLOCKED)
        self.assertIn(VIOLATION_AUTHOR_UNKNOWN, report.violations)
        self.assertNotIn(VIOLATION_AUTHOR_NO_SERIES_57, report.violations)

    def test_personnel_lookup_ignores_case_and_whitespace(self):
        report = self.engine.audit_code_commit(self.covered_commit(author_id="  dev_a  "))
        self.assertEqual(report.cicd_gate_status, GATE_APPROVED)

    def test_multiple_violations_are_all_reported(self):
        report = self.engine.audit_code_commit(
            self.covered_commit(author_id="DEV_B", approving_supervisor_id="DEV_B")
        )
        self.assertIn(VIOLATION_AUTHOR_NO_SERIES_57, report.violations)
        self.assertIn(VIOLATION_SUPERVISOR_NO_SERIES_57, report.violations)
        self.assertIn(VIOLATION_SELF_APPROVAL, report.violations)


class TestSupervisorEvaluation(BaseEngineTest):

    def test_securities_trader_principal_basis_reported(self):
        report = self.engine.audit_code_commit(self.covered_commit())
        self.assertEqual(
            report.supervisor_registration_basis, BASIS_SECURITIES_TRADER_PRINCIPAL
        )

    def test_plain_securities_trader_supervisor_is_accepted(self):
        # Notice 16-21 permits assignment to a Securities Trader OR a Securities
        # Trader Principal for Rule 3110(a)(5) purposes.
        report = self.engine.audit_code_commit(
            self.covered_commit(approving_supervisor_id="SUP_TRADER")
        )
        self.assertEqual(report.supervisor_registration_basis, BASIS_SECURITIES_TRADER)
        self.assertEqual(report.cicd_gate_status, GATE_APPROVED)

    def test_unqualified_supervisor_blocks(self):
        report = self.engine.audit_code_commit(
            self.covered_commit(approving_supervisor_id="DEV_B")
        )
        self.assertEqual(report.cicd_gate_status, GATE_BLOCKED)
        self.assertIn(VIOLATION_SUPERVISOR_NO_SERIES_57, report.violations)
        self.assertEqual(report.supervisor_registration_basis, BASIS_NOT_QUALIFIED)

    def test_missing_supervisor_blocks(self):
        report = self.engine.audit_code_commit(self.covered_commit(approving_supervisor_id=""))
        self.assertEqual(report.cicd_gate_status, GATE_BLOCKED)
        self.assertIn(VIOLATION_SUPERVISOR_UNIDENTIFIED, report.violations)
        self.assertEqual(report.supervisor_registration_basis, BASIS_NOT_IDENTIFIED)

    def test_supervisor_check_can_be_delegated_elsewhere(self):
        engine = FinraAlgoRegistrationEngine(
            require_supervisor_registration=False, clock=frozen_clock
        )
        for creds in self.engine.personnel_registry.values():
            engine.register_personnel(creds)
        report = engine.audit_code_commit(self.covered_commit(approving_supervisor_id="DEV_B"))
        self.assertEqual(report.cicd_gate_status, GATE_APPROVED)


class TestSelfApproval(BaseEngineTest):

    def test_self_approval_blocks_even_when_registered(self):
        report = self.engine.audit_code_commit(
            self.covered_commit(author_id="DEV_A", approving_supervisor_id="DEV_A")
        )
        self.assertEqual(report.cicd_gate_status, GATE_BLOCKED)
        self.assertEqual(report.violations, (VIOLATION_SELF_APPROVAL,))

    def test_self_approval_detection_ignores_case(self):
        report = self.engine.audit_code_commit(
            self.covered_commit(author_id="DEV_A", approving_supervisor_id="dev_a")
        )
        self.assertIn(VIOLATION_SELF_APPROVAL, report.violations)

    def test_self_approval_can_be_disabled_by_firm_policy(self):
        engine = FinraAlgoRegistrationEngine(block_self_approval=False, clock=frozen_clock)
        engine.register_personnel(self.engine.get_personnel("DEV_A"))
        report = engine.audit_code_commit(
            self.covered_commit(author_id="DEV_A", approving_supervisor_id="DEV_A")
        )
        self.assertEqual(report.cicd_gate_status, GATE_APPROVED)


class TestLegacyCompatibility(BaseEngineTest):

    def test_legacy_flags_still_classify_significant_routing_change_in_scope(self):
        req = AlgoCodeCommitRequest(
            commit_id="COMMIT_LEGACY_1",
            algorithm_id="HFT_MM_V1",
            algorithm_name="Market Maker Engine",
            author_id="DEV_B",
            approving_supervisor_id="SUP_A",
            is_significant_modification=True,
            modifies_order_routing_logic=True,
        )
        report = self.engine.audit_code_commit(req)

        self.assertTrue(report.is_rule_1220b4_applicable)
        self.assertEqual(report.cicd_gate_status, GATE_BLOCKED)
        self.assertEqual(report.author_activity, ACTIVITY_SIGNIFICANT_MODIFICATION)
        self.assertEqual(report.security_type, SECURITY_EQUITY)

    def test_legacy_minor_change_is_out_of_scope(self):
        req = AlgoCodeCommitRequest(
            commit_id="COMMIT_LEGACY_2",
            algorithm_id="VWAP_V2",
            algorithm_name="VWAP Router",
            author_id="DEV_B",
            approving_supervisor_id="SUP_A",
            is_significant_modification=False,
            modifies_order_routing_logic=True,
        )
        report = self.engine.audit_code_commit(req)
        self.assertEqual(report.cicd_gate_status, GATE_OUT_OF_SCOPE)
        self.assertEqual(report.author_activity, ACTIVITY_MINOR_MODIFICATION)


class TestValidationAndImmutability(BaseEngineTest):

    def test_blank_identifiers_are_rejected(self):
        for field_name, value in (
            ("commit_id", "   "),
            ("algorithm_id", ""),
            ("algorithm_name", ""),
            ("author_id", ""),
        ):
            with self.subTest(field=field_name):
                with self.assertRaises(ValueError):
                    self.covered_commit(**{field_name: value})

    def test_blank_personnel_id_is_rejected(self):
        with self.assertRaises(ValueError):
            DeveloperCredentials("", "Nobody", "QUANT_DEV", True, True)

    def test_register_personnel_rejects_wrong_type(self):
        with self.assertRaises(TypeError):
            self.engine.register_personnel({"personnel_id": "DEV_X"})

    def test_audit_rejects_wrong_type(self):
        with self.assertRaises(TypeError):
            self.engine.audit_code_commit({"commit_id": "X"})

    def test_report_is_immutable(self):
        report = self.engine.audit_code_commit(self.covered_commit())
        with self.assertRaises(FrozenInstanceError):
            report.cicd_gate_status = GATE_APPROVED  # type: ignore[misc]


class TestAuditTrail(BaseEngineTest):

    def test_every_decision_is_recorded_in_order(self):
        self.engine.audit_code_commit(self.covered_commit(commit_id="C1"))
        self.engine.audit_code_commit(self.covered_commit(commit_id="C2", author_id="DEV_B"))
        self.engine.audit_code_commit(
            self.covered_commit(commit_id="C3", security_type=SECURITY_FUTURE)
        )

        trail = self.engine.audit_trail
        self.assertEqual([r.commit_id for r in trail], ["C1", "C2", "C3"])
        self.assertEqual(
            [r.cicd_gate_status for r in trail],
            [GATE_APPROVED, GATE_BLOCKED, GATE_OUT_OF_SCOPE],
        )

    def test_decision_timestamp_uses_injected_clock(self):
        report = self.engine.audit_code_commit(self.covered_commit())
        self.assertEqual(report.decision_timestamp_utc, FROZEN_NOW.isoformat())

    def test_naive_clock_is_treated_as_utc_not_local_time(self):
        naive = datetime(2026, 3, 2, 14, 30, 5)
        engine = FinraAlgoRegistrationEngine(clock=lambda: naive)
        engine.register_personnel(self.engine.get_personnel("DEV_A"))
        engine.register_personnel(self.engine.get_personnel("SUP_A"))
        report = engine.audit_code_commit(self.covered_commit())
        self.assertEqual(report.decision_timestamp_utc, FROZEN_NOW.isoformat())

    def test_audit_trail_is_a_snapshot(self):
        self.engine.audit_code_commit(self.covered_commit())
        trail = self.engine.audit_trail
        self.engine.audit_code_commit(self.covered_commit(commit_id="C_LATER"))
        self.assertEqual(len(trail), 1)


class TestAssessScope(BaseEngineTest):

    def test_assess_scope_returns_resolved_tokens(self):
        applicable, reason, security_type, behavior, activity = self.engine.assess_scope(
            self.covered_commit()
        )
        self.assertTrue(applicable)
        self.assertEqual(reason, SCOPE_APPLICABLE)
        self.assertEqual(security_type, SECURITY_EQUITY)
        self.assertEqual(behavior, SYSTEM_GENERATES_OR_ROUTES_ORDERS)
        self.assertEqual(activity, ACTIVITY_SIGNIFICANT_MODIFICATION)

    def test_assess_scope_does_not_record_a_decision(self):
        self.engine.assess_scope(self.covered_commit())
        self.assertEqual(self.engine.audit_trail, ())


if __name__ == "__main__":
    unittest.main()
