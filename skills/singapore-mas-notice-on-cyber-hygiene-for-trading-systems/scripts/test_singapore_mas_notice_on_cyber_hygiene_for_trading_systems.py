"""Behavioural tests for the MAS Notice on Cyber Hygiene audit engine.

The tests below assert what the Notice actually requires, not what the engine
happens to compute. In particular they pin the three defects that version 1.0.0
of this skill shipped:

  * a fabricated "30-day MAS patching SLA" -- there is no such figure;
  * multi-factor authentication applied universally rather than scoped to
    critical systems, with limb 4.6(b) missing altogether;
  * malware protection and security-standards conformance modelled as single
    unconditional booleans, ignoring the Notice's own carve-outs.
"""
import unittest

from singapore_mas_notice_on_cyber_hygiene_for_trading_systems import (
    CYBER_HYGIENE_NOTICE_BY_ENTITY_CLASS,
    MASCyberHygieneAuditReport,
    MASCyberHygieneRequirement,
    OpenVulnerability,
    PatchRemediationPolicy,
    SingaporeMASCyberHygieneEngine,
    STATUS_BREACH,
    STATUS_COMPLIANT,
    TradingSystemAsset,
)

# A firm's own risk-commensurate deadlines. Deliberately NOT 30 days across the
# board -- the whole point is that the firm sets these, not MAS.
FIRM_PATCH_POLICY = PatchRemediationPolicy(
    max_days_by_severity={"CRITICAL": 7, "HIGH": 14, "MEDIUM": 60, "LOW": 180}
)


def compliant_asset(**overrides) -> TradingSystemAsset:
    """A fully compliant critical order router, with fields overridable per test."""
    defaults = dict(
        system_id="SYS_ORDER_ROUTER_01",
        system_name="SGX FIX Order Router",
        asset_type="ORDER_ROUTER",
        is_critical_system=True,
        accesses_customer_information_over_internet=False,
        administrative_accounts_secured=True,
        open_vulnerabilities=(),
        has_written_security_standards=True,
        conforms_to_security_standards=True,
        network_perimeter_controls_implemented=True,
        malware_protection_implemented=True,
        mfa_on_administrative_accounts=True,
    )
    defaults.update(overrides)
    return TradingSystemAsset(**defaults)


class TestEngineConstruction(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = SingaporeMASCyberHygieneEngine(FIRM_PATCH_POLICY)

    def test_capital_markets_firm_is_stamped_with_fsm_n22_not_fsm_n06(self) -> None:
        """A CMS licensee is bound by FSM-N22; FSM-N06 is the banks' notice."""
        report = self.engine.audit_trading_asset(compliant_asset())
        self.assertEqual(report.entity_notice, "FSM-N22")
        self.assertIn("FSM-N22", report.audit_notes)

    def test_bank_entity_class_is_stamped_with_fsm_n06(self) -> None:
        bank_engine = SingaporeMASCyberHygieneEngine(FIRM_PATCH_POLICY, entity_class="BANK")
        report = bank_engine.audit_trading_asset(compliant_asset())
        self.assertEqual(report.entity_notice, "FSM-N06")

    def test_notice_map_covers_both_documented_entity_classes(self) -> None:
        self.assertEqual(CYBER_HYGIENE_NOTICE_BY_ENTITY_CLASS["BANK"], "FSM-N06")
        self.assertEqual(CYBER_HYGIENE_NOTICE_BY_ENTITY_CLASS["CAPITAL_MARKETS"], "FSM-N22")

    def test_unknown_entity_class_raises(self) -> None:
        with self.assertRaises(ValueError):
            SingaporeMASCyberHygieneEngine(FIRM_PATCH_POLICY, entity_class="INSURER")

    def test_patch_policy_is_mandatory_and_typed(self) -> None:
        """No default deadline exists, because MAS publishes none."""
        with self.assertRaises(TypeError):
            SingaporeMASCyberHygieneEngine()  # type: ignore[call-arg]
        with self.assertRaises(TypeError):
            SingaporeMASCyberHygieneEngine({"CRITICAL": 7})  # type: ignore[arg-type]

    def test_empty_or_invalid_patch_policy_raises(self) -> None:
        with self.assertRaises(ValueError):
            PatchRemediationPolicy(max_days_by_severity={})
        with self.assertRaises(ValueError):
            PatchRemediationPolicy(max_days_by_severity={"SEVERE": 7})
        with self.assertRaises(ValueError):
            PatchRemediationPolicy(max_days_by_severity={"CRITICAL": -1})
        with self.assertRaises(ValueError):
            PatchRemediationPolicy(max_days_by_severity={"CRITICAL": True})

    def test_policy_deadlines_cannot_be_rewritten_after_validation(self) -> None:
        """A frozen policy holding a caller's live dict is not actually frozen."""
        supplied = {"CRITICAL": 7}
        policy = PatchRemediationPolicy(max_days_by_severity=supplied)
        supplied["CRITICAL"] = 9_999
        self.assertEqual(policy.max_days_for("CRITICAL"), 7)
        with self.assertRaises(TypeError):
            policy.max_days_by_severity["CRITICAL"] = 9_999  # type: ignore[index]

    def test_no_always_compliant_shortcut_is_exported(self) -> None:
        """v1.0.0 shipped a ComplianceChecker that returned is_compliant=True unconditionally."""
        import singapore_mas_notice_on_cyber_hygiene_for_trading_systems as module

        self.assertFalse(hasattr(module, "ComplianceChecker"))
        self.assertFalse(hasattr(module, "ComplianceRecord"))


class TestFullyCompliantAsset(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = SingaporeMASCyberHygieneEngine(FIRM_PATCH_POLICY)

    def test_compliant_asset_passes_cleanly(self) -> None:
        report = self.engine.audit_trading_asset(compliant_asset())
        self.assertIsInstance(report, MASCyberHygieneAuditReport)
        self.assertTrue(report.is_compliant)
        self.assertEqual(report.status, STATUS_COMPLIANT)
        self.assertEqual(report.breaches, ())
        self.assertEqual(report.failed_requirements, ())
        self.assertEqual(report.mandatory_remediations, ())
        self.assertEqual(report.remediation_progress_pct, 100.0)

    def test_report_collections_are_immutable_tuples(self) -> None:
        """An audit record a caller can mutate after the fact is not an audit record."""
        report = self.engine.audit_trading_asset(compliant_asset(administrative_accounts_secured=False))
        for collection in (
            report.breaches,
            report.failed_requirements,
            report.applicable_requirements,
            report.not_applicable_requirements,
            report.warnings,
            report.mandatory_remediations,
        ):
            self.assertIsInstance(collection, tuple)


class TestSecurityPatchesParagraph42(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = SingaporeMASCyberHygieneEngine(FIRM_PATCH_POLICY)

    def test_no_thirty_day_rule_is_applied(self) -> None:
        """A CRITICAL flaw open 20 days breaches a 7-day firm policy.

        Under the deleted "30-day MAS SLA" this asset was compliant. The Notice
        requires a timeframe commensurate with the risk, and this firm's own
        risk assessment set 7 days for CRITICAL.
        """
        asset = compliant_asset(
            open_vulnerabilities=(
                OpenVulnerability("CVE-2026-0001", "CRITICAL", days_since_patch_released=20),
            )
        )
        report = self.engine.audit_trading_asset(asset)
        self.assertFalse(report.is_compliant)
        self.assertIn(
            MASCyberHygieneRequirement.SECURITY_PATCH_MANAGEMENT, report.failed_requirements
        )
        self.assertEqual([b.notice_paragraph for b in report.breaches], ["4.2(a)"])

    def test_low_severity_open_ninety_days_is_within_a_180_day_policy(self) -> None:
        """The mirror image: 90 days on a LOW issue breached the old flat 30-day rule."""
        asset = compliant_asset(
            open_vulnerabilities=(
                OpenVulnerability("CVE-2026-0002", "LOW", days_since_patch_released=90),
            )
        )
        report = self.engine.audit_trading_asset(asset)
        self.assertTrue(report.is_compliant)

    def test_deadline_is_inclusive_at_exactly_the_policy_limit(self) -> None:
        """'Within 7 days' is met at exactly 7; day 8 is not."""
        on_deadline = compliant_asset(
            open_vulnerabilities=(
                OpenVulnerability("CVE-2026-0003", "CRITICAL", days_since_patch_released=7),
            )
        )
        self.assertTrue(self.engine.audit_trading_asset(on_deadline).is_compliant)

        past_deadline = compliant_asset(
            open_vulnerabilities=(
                OpenVulnerability("CVE-2026-0003", "CRITICAL", days_since_patch_released=8),
            )
        )
        self.assertFalse(self.engine.audit_trading_asset(past_deadline).is_compliant)

    def test_unpatchable_vulnerability_with_controls_is_compliant_under_42b(self) -> None:
        """Para 4.2(b): where no patch exists, instituted controls satisfy the Notice."""
        asset = compliant_asset(
            open_vulnerabilities=(
                OpenVulnerability(
                    "VENDOR-2026-11",
                    "HIGH",
                    days_since_patch_released=None,
                    compensating_controls_in_place=True,
                ),
            )
        )
        report = self.engine.audit_trading_asset(asset)
        self.assertTrue(report.is_compliant)
        self.assertTrue(any("4.2(b)" in w for w in report.warnings))

    def test_unpatchable_vulnerability_without_controls_breaches_42b(self) -> None:
        asset = compliant_asset(
            open_vulnerabilities=(
                OpenVulnerability("VENDOR-2026-12", "HIGH", days_since_patch_released=None),
            )
        )
        report = self.engine.audit_trading_asset(asset)
        self.assertFalse(report.is_compliant)
        self.assertEqual([b.notice_paragraph for b in report.breaches], ["4.2(b)"])

    def test_compensating_controls_do_not_excuse_an_overdue_available_patch(self) -> None:
        """4.2(b) is the answer to 'no patch exists', not to 'we did not apply one'."""
        asset = compliant_asset(
            open_vulnerabilities=(
                OpenVulnerability(
                    "CVE-2026-0004",
                    "CRITICAL",
                    days_since_patch_released=45,
                    compensating_controls_in_place=True,
                ),
            )
        )
        report = self.engine.audit_trading_asset(asset)
        self.assertFalse(report.is_compliant)
        self.assertEqual([b.notice_paragraph for b in report.breaches], ["4.2(a)"])
        self.assertTrue(any("remains a breach" in w for w in report.warnings))

    def test_both_patch_limbs_can_breach_at_once_as_one_failed_requirement(self) -> None:
        asset = compliant_asset(
            open_vulnerabilities=(
                OpenVulnerability("CVE-2026-0005", "CRITICAL", days_since_patch_released=45),
                OpenVulnerability("VENDOR-2026-13", "HIGH", days_since_patch_released=None),
            )
        )
        report = self.engine.audit_trading_asset(asset)
        self.assertEqual(
            sorted(b.notice_paragraph for b in report.breaches), ["4.2(a)", "4.2(b)"]
        )
        self.assertEqual(
            report.failed_requirements,
            (MASCyberHygieneRequirement.SECURITY_PATCH_MANAGEMENT,),
        )

    def test_severity_absent_from_the_policy_fails_closed(self) -> None:
        narrow_engine = SingaporeMASCyberHygieneEngine(
            PatchRemediationPolicy(max_days_by_severity={"CRITICAL": 7})
        )
        asset = compliant_asset(
            open_vulnerabilities=(
                OpenVulnerability("CVE-2026-0006", "MEDIUM", days_since_patch_released=1),
            )
        )
        with self.assertRaises(ValueError):
            narrow_engine.audit_trading_asset(asset)


class TestMultiFactorAuthenticationParagraph46(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = SingaporeMASCyberHygieneEngine(FIRM_PATCH_POLICY)

    def test_mfa_on_admin_accounts_required_only_for_critical_systems(self) -> None:
        """Limb 4.6(a) is scoped to critical systems, not to every host."""
        critical = compliant_asset(is_critical_system=True, mfa_on_administrative_accounts=False)
        report = self.engine.audit_trading_asset(critical)
        self.assertFalse(report.is_compliant)
        self.assertEqual([b.notice_paragraph for b in report.breaches], ["4.6(a)"])

    def test_non_critical_offline_system_is_out_of_mfa_scope(self) -> None:
        asset = compliant_asset(
            is_critical_system=False,
            accesses_customer_information_over_internet=False,
            mfa_on_administrative_accounts=False,
        )
        report = self.engine.audit_trading_asset(asset)
        self.assertTrue(report.is_compliant)
        self.assertEqual(
            report.not_applicable_requirements,
            (MASCyberHygieneRequirement.MULTI_FACTOR_AUTH,),
        )
        self.assertNotIn(
            MASCyberHygieneRequirement.MULTI_FACTOR_AUTH, report.applicable_requirements
        )

    def test_non_critical_internet_customer_information_system_is_in_scope(self) -> None:
        """Limb 4.6(b) reaches ALL accounts, critical system or not.

        v1.0.0 modelled only a single admin-MFA flag and would have passed this
        asset outright.
        """
        asset = compliant_asset(
            is_critical_system=False,
            accesses_customer_information_over_internet=True,
            mfa_on_administrative_accounts=True,
            mfa_on_customer_information_accounts=False,
        )
        report = self.engine.audit_trading_asset(asset)
        self.assertFalse(report.is_compliant)
        self.assertEqual([b.notice_paragraph for b in report.breaches], ["4.6(b)"])
        self.assertIn("not only administrative", report.breaches[0].remediation)

    def test_both_mfa_limbs_can_breach_at_once(self) -> None:
        asset = compliant_asset(
            is_critical_system=True,
            accesses_customer_information_over_internet=True,
            mfa_on_administrative_accounts=False,
            mfa_on_customer_information_accounts=False,
        )
        report = self.engine.audit_trading_asset(asset)
        self.assertEqual(
            [b.notice_paragraph for b in report.breaches], ["4.6(a)", "4.6(b)"]
        )
        self.assertEqual(
            report.failed_requirements, (MASCyberHygieneRequirement.MULTI_FACTOR_AUTH,)
        )

    def test_unknown_criticality_resolves_conservatively_to_in_scope(self) -> None:
        """An absent scope field must never make a breaching asset look compliant."""
        asset = compliant_asset(
            is_critical_system=None,
            accesses_customer_information_over_internet=None,
            mfa_on_administrative_accounts=False,
            mfa_on_customer_information_accounts=False,
        )
        report = self.engine.audit_trading_asset(asset)
        self.assertFalse(report.is_compliant)
        self.assertEqual(
            [b.notice_paragraph for b in report.breaches], ["4.6(a)", "4.6(b)"]
        )
        self.assertTrue(any("conservatively" in w for w in report.warnings))


class TestRemainingRequirements(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = SingaporeMASCyberHygieneEngine(FIRM_PATCH_POLICY)

    def test_unsecured_administrative_accounts_breach_41(self) -> None:
        report = self.engine.audit_trading_asset(
            compliant_asset(administrative_accounts_secured=False)
        )
        self.assertEqual([b.notice_paragraph for b in report.breaches], ["4.1"])

    def test_missing_written_standards_breach_43a_and_stop_there(self) -> None:
        """Conformance under 4.3(b) is unevaluable when no standards exist."""
        report = self.engine.audit_trading_asset(
            compliant_asset(
                has_written_security_standards=False, conforms_to_security_standards=False
            )
        )
        self.assertEqual([b.notice_paragraph for b in report.breaches], ["4.3(a)"])

    def test_written_standards_requirement_is_not_a_cis_benchmark_mandate(self) -> None:
        report = self.engine.audit_trading_asset(
            compliant_asset(has_written_security_standards=False)
        )
        remediation = report.breaches[0].remediation
        self.assertIn("not any particular benchmark", remediation)

    def test_nonconformity_with_instituted_controls_is_compliant_under_43c(self) -> None:
        report = self.engine.audit_trading_asset(
            compliant_asset(
                conforms_to_security_standards=False, nonconformity_controls_in_place=True
            )
        )
        self.assertTrue(report.is_compliant)
        self.assertTrue(any("4.3(c)" in w for w in report.warnings))

    def test_nonconformity_without_controls_breaches_43b(self) -> None:
        report = self.engine.audit_trading_asset(
            compliant_asset(
                conforms_to_security_standards=False, nonconformity_controls_in_place=False
            )
        )
        self.assertEqual([b.notice_paragraph for b in report.breaches], ["4.3(b)"])

    def test_missing_network_perimeter_controls_breach_44(self) -> None:
        report = self.engine.audit_trading_asset(
            compliant_asset(network_perimeter_controls_implemented=False)
        )
        self.assertEqual([b.notice_paragraph for b in report.breaches], ["4.4"])

    def test_absent_malware_protection_breaches_45_without_a_justification(self) -> None:
        report = self.engine.audit_trading_asset(
            compliant_asset(malware_protection_implemented=False)
        )
        self.assertEqual([b.notice_paragraph for b in report.breaches], ["4.5"])

    def test_malware_carve_out_applies_where_measures_cannot_be_implemented(self) -> None:
        """Para 4.5 is qualified 'where such measures are available and can be implemented'."""
        report = self.engine.audit_trading_asset(
            compliant_asset(
                malware_protection_implemented=False,
                malware_protection_unavailable_justification=(
                    "Vendor-sealed FPGA feed handler appliance; no agent can be installed."
                ),
            )
        )
        self.assertTrue(report.is_compliant)
        self.assertTrue(any("4.5" in w for w in report.warnings))

    def test_blank_justification_does_not_earn_the_carve_out(self) -> None:
        report = self.engine.audit_trading_asset(
            compliant_asset(
                malware_protection_implemented=False,
                malware_protection_unavailable_justification="   ",
            )
        )
        self.assertFalse(report.is_compliant)
        self.assertEqual([b.notice_paragraph for b in report.breaches], ["4.5"])


class TestReportSemantics(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = SingaporeMASCyberHygieneEngine(FIRM_PATCH_POLICY)

    def test_every_requirement_is_evaluated_nothing_short_circuits(self) -> None:
        asset = TradingSystemAsset(
            system_id="SYS_UNGOVERNED_01",
            system_name="Unmanaged Colo Host",
            asset_type="EXECUTION_ENGINE",
            is_critical_system=True,
            accesses_customer_information_over_internet=True,
            open_vulnerabilities=(
                OpenVulnerability("CVE-2026-9999", "CRITICAL", days_since_patch_released=400),
            ),
        )
        report = self.engine.audit_trading_asset(asset)
        self.assertFalse(report.is_compliant)
        self.assertEqual(report.status, STATUS_BREACH)
        self.assertEqual(len(report.failed_requirements), 6)
        self.assertEqual(report.remediation_progress_pct, 0.0)

    def test_progress_is_measured_over_applicable_requirements_only(self) -> None:
        """Dividing by a flat 6 misreports an asset the MFA requirement never reached."""
        asset = compliant_asset(
            is_critical_system=False,
            accesses_customer_information_over_internet=False,
            administrative_accounts_secured=False,
        )
        report = self.engine.audit_trading_asset(asset)
        self.assertEqual(len(report.applicable_requirements), 5)
        self.assertEqual(report.remediation_progress_pct, 80.0)

    def test_remediations_are_deduplicated_and_aligned_with_breaches(self) -> None:
        asset = compliant_asset(
            administrative_accounts_secured=False,
            network_perimeter_controls_implemented=False,
        )
        report = self.engine.audit_trading_asset(asset)
        self.assertEqual(len(report.mandatory_remediations), 2)
        self.assertEqual(
            set(report.mandatory_remediations), {b.remediation for b in report.breaches}
        )

    def test_estate_audit_returns_one_report_per_asset_in_order(self) -> None:
        assets = (
            compliant_asset(system_id="SYS_A"),
            compliant_asset(system_id="SYS_B", administrative_accounts_secured=False),
        )
        reports = self.engine.audit_estate(assets)
        self.assertEqual([r.system_id for r in reports], ["SYS_A", "SYS_B"])
        self.assertEqual([r.is_compliant for r in reports], [True, False])


class TestInputValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = SingaporeMASCyberHygieneEngine(FIRM_PATCH_POLICY)

    def test_blank_identifiers_raise(self) -> None:
        for blank_field in ("system_id", "system_name", "asset_type"):
            with self.subTest(field=blank_field):
                with self.assertRaises(ValueError):
                    compliant_asset(**{blank_field: "  "})

    def test_vulnerability_requires_a_known_severity_and_an_id(self) -> None:
        with self.assertRaises(ValueError):
            OpenVulnerability("", "CRITICAL", days_since_patch_released=1)
        with self.assertRaises(ValueError):
            OpenVulnerability("CVE-2026-0007", "SEVERE", days_since_patch_released=1)

    def test_negative_patch_age_raises_rather_than_passing_every_deadline(self) -> None:
        with self.assertRaises(ValueError):
            OpenVulnerability("CVE-2026-0008", "CRITICAL", days_since_patch_released=-1)

    def test_boolean_patch_age_is_rejected(self) -> None:
        """`True` is an int in Python and would silently read as 1 day."""
        with self.assertRaises(ValueError):
            OpenVulnerability("CVE-2026-0009", "CRITICAL", days_since_patch_released=True)

    def test_vulnerability_collection_must_be_a_tuple_of_vulnerabilities(self) -> None:
        with self.assertRaises(TypeError):
            compliant_asset(
                open_vulnerabilities=[
                    OpenVulnerability("CVE-2026-0010", "LOW", days_since_patch_released=1)
                ]
            )
        with self.assertRaises(TypeError):
            compliant_asset(open_vulnerabilities=("CVE-2026-0011",))

    def test_duplicate_vulnerability_ids_raise(self) -> None:
        with self.assertRaises(ValueError):
            compliant_asset(
                open_vulnerabilities=(
                    OpenVulnerability("CVE-2026-0012", "LOW", days_since_patch_released=1),
                    OpenVulnerability("CVE-2026-0012", "HIGH", days_since_patch_released=1),
                )
            )

    def test_auditing_a_non_asset_raises(self) -> None:
        with self.assertRaises(TypeError):
            self.engine.audit_trading_asset({"system_id": "SYS_X"})  # type: ignore[arg-type]

    def test_asset_defaults_fail_closed(self) -> None:
        """An asset populated with identifiers alone must not audit as compliant."""
        report = self.engine.audit_trading_asset(
            TradingSystemAsset(
                system_id="SYS_MINIMAL",
                system_name="Partially Onboarded Host",
                asset_type="TRADING_DB",
            )
        )
        self.assertFalse(report.is_compliant)


if __name__ == "__main__":
    unittest.main()
