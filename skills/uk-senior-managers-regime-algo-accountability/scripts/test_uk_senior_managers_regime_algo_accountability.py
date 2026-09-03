import datetime
import unittest

from uk_senior_managers_regime_algo_accountability import (
    MAX_CERTIFICATE_VALIDITY_MONTHS,
    AlgoStrategyRegistration,
    CertificationStatus,
    CertifiedDeveloper,
    DeploymentSignOff,
    SMCRAlgoAccountabilityEngine,
    SMCRError,
    SMCRFirmTier,
    SeniorManager,
    SignOffStatus,
    SMFRole,
    _add_months,
)

# Fixed clock for every date-sensitive assertion, so the suite cannot start
# failing simply because real time passed the fixtures' certificate expiry.
AS_OF = datetime.date(2026, 6, 1)
GOOD_NOTES = (
    "Reviewed RTS 6 Article 15 pre-trade collars, Article 10 stress test results, "
    "and witnessed an Article 12 kill functionality drill on 2026-05-28."
)


def _algo(version: str = "1.2.0", **overrides) -> AlgoStrategyRegistration:
    params = dict(
        algo_id="ALGO-MM-01",
        name="Index Market Maker",
        version=version,
        responsible_smf_id="SMF-24-JOHN",
        certified_dev_ids=["DEV-ALICE"],
        pre_trade_risk_approved=True,
        kill_switch_tested=True,
        stress_tested=True,
    )
    params.update(overrides)
    return AlgoStrategyRegistration(**params)


class TestSMCRAlgoAccountabilityEngine(unittest.TestCase):
    def setUp(self):
        self.engine = SMCRAlgoAccountabilityEngine(firm_tier=SMCRFirmTier.ENHANCED)

        self.smf24_coo = SeniorManager(
            smf_id="SMF-24-JOHN",
            name="John Doe",
            role=SMFRole.SMF24_CHIEF_OPERATIONS,
            fca_irn="JXD12345",
            email="john.doe@firm.co.uk",
        )
        self.smf4_cro = SeniorManager(
            smf_id="SMF-4-SARAH",
            name="Sarah Smith",
            role=SMFRole.SMF4_CHIEF_RISK,
            fca_irn="SXS67890",
            email="sarah.smith@firm.co.uk",
        )

        self.engine.register_senior_manager(self.smf24_coo)
        self.engine.register_senior_manager(self.smf4_cro)

        self.dev_alice = CertifiedDeveloper(
            dev_id="DEV-ALICE",
            name="Alice Quant",
            role_title="Lead HFT Quant Developer",
            status=CertificationStatus.FIT_AND_PROPER,
            last_assessment_date=datetime.date(2026, 1, 15),
            accredited_by_smf_id="SMF-24-JOHN",
        )
        self.engine.certify_developer(self.dev_alice)

    def _approve(self, version=None, status=SignOffStatus.APPROVED, notes=GOOD_NOTES):
        return self.engine.execute_deployment_sign_off(
            DeploymentSignOff(
                sign_off_id="SIG-001",
                algo_id="ALGO-MM-01",
                smf_id="SMF-24-JOHN",
                status=status,
                reasonable_steps_notes=notes,
                algo_version=version,
            )
        )

    # --- Registration -------------------------------------------------

    def test_senior_manager_registration(self):
        self.assertEqual(len(self.engine.senior_managers), 2)
        self.assertIn("SMF-24-JOHN", self.engine.senior_managers)

    def test_developer_certification_fitness_and_propriety(self):
        self.assertEqual(len(self.engine.certified_developers), 1)
        self.assertEqual(self.dev_alice.status, CertificationStatus.FIT_AND_PROPER)

    def test_algo_strategy_registration(self):
        self.engine.register_algo_strategy(_algo())
        self.assertIn("ALGO-MM-01", self.engine.algo_registrations)

    def test_senior_manager_requires_irn(self):
        with self.assertRaises(SMCRError):
            self.engine.register_senior_manager(
                SeniorManager(
                    smf_id="SMF-16-NOIRN",
                    name="No Irn",
                    role=SMFRole.SMF16_COMPLIANCE_OVERSIGHT,
                    fca_irn="   ",
                    email="no.irn@firm.co.uk",
                )
            )

    def test_unregistered_developer_rejected_at_registration(self):
        with self.assertRaises(SMCRError):
            self.engine.register_algo_strategy(_algo(certified_dev_ids=["DEV-GHOST"]))

    # --- Firm tier scoping (SUP 10C / SYSC 25) ------------------------

    def test_core_firm_cannot_appoint_enhanced_only_smf(self):
        """SMF24 and SMF4 exist only for enhanced scope and dual-regulated firms."""
        core_engine = SMCRAlgoAccountabilityEngine(firm_tier=SMCRFirmTier.CORE)
        with self.assertRaises(SMCRError) as ctx:
            core_engine.register_senior_manager(self.smf24_coo)
        self.assertIn("enhanced", str(ctx.exception).lower())

    def test_core_firm_may_appoint_compliance_oversight(self):
        core_engine = SMCRAlgoAccountabilityEngine(firm_tier=SMCRFirmTier.CORE)
        core_engine.register_senior_manager(
            SeniorManager(
                smf_id="SMF-16-MEG",
                name="Meg Compliance",
                role=SMFRole.SMF16_COMPLIANCE_OVERSIGHT,
                fca_irn="MXC11111",
                email="meg@firm.co.uk",
            )
        )
        self.assertIn("SMF-16-MEG", core_engine.senior_managers)

    def test_mrm_required_only_for_in_scope_tiers(self):
        self.assertTrue(SMCRAlgoAccountabilityEngine(SMCRFirmTier.ENHANCED).mrm_required)
        self.assertTrue(SMCRAlgoAccountabilityEngine(SMCRFirmTier.BANKING).mrm_required)
        self.assertFalse(SMCRAlgoAccountabilityEngine(SMCRFirmTier.CORE).mrm_required)
        self.assertFalse(SMCRAlgoAccountabilityEngine(SMCRFirmTier.LIMITED_SCOPE).mrm_required)

    # --- Deployment readiness -----------------------------------------

    def test_deployment_readiness_fails_without_sign_off(self):
        self.engine.register_algo_strategy(_algo())
        is_ready, issues = self.engine.verify_algo_deployment_readiness("ALGO-MM-01", as_of=AS_OF)
        self.assertFalse(is_ready)
        self.assertTrue(any("No SMF deployment sign-off recorded" in i for i in issues))

    def test_deployment_readiness_passes_with_full_sign_off(self):
        self.engine.register_algo_strategy(_algo())
        self._approve()
        is_ready, issues = self.engine.verify_algo_deployment_readiness("ALGO-MM-01", as_of=AS_OF)
        self.assertTrue(is_ready)
        self.assertEqual(issues, [])

    def test_rejected_sign_off_blocks_deployment(self):
        """A recorded but REJECTED sign-off must not read as approval."""
        self.engine.register_algo_strategy(_algo())
        self._approve(status=SignOffStatus.REJECTED)
        is_ready, issues = self.engine.verify_algo_deployment_readiness("ALGO-MM-01", as_of=AS_OF)
        self.assertFalse(is_ready)
        self.assertTrue(any("is REJECTED, not APPROVED" in i for i in issues))

    def test_missing_rts6_evidence_cites_correct_articles(self):
        self.engine.register_algo_strategy(
            _algo(pre_trade_risk_approved=False, kill_switch_tested=False, stress_tested=False)
        )
        _, issues = self.engine.verify_algo_deployment_readiness("ALGO-MM-01", as_of=AS_OF)
        joined = " | ".join(issues)
        self.assertIn("Pre-trade controls on order entry have not been approved (RTS 6 Article 15)", joined)
        self.assertIn("Kill functionality has not been tested (RTS 6 Article 12)", joined)
        self.assertIn("Stress testing has not been completed (RTS 6 Article 10)", joined)

    def test_unregistered_algo_is_not_ready(self):
        is_ready, issues = self.engine.verify_algo_deployment_readiness("ALGO-NOPE", as_of=AS_OF)
        self.assertFalse(is_ready)
        self.assertEqual(len(issues), 1)

    # --- Certificate expiry (FSMA s.63F / SYSC 27.2) ------------------

    def test_certificate_expires_twelve_months_after_assessment(self):
        self.assertEqual(self.dev_alice.certificate_expiry_date, datetime.date(2027, 1, 15))

    def test_expired_certificate_blocks_deployment(self):
        """Regression: F&P expiry was documented but never enforced."""
        self.engine.register_algo_strategy(_algo())
        self._approve()
        is_ready, issues = self.engine.verify_algo_deployment_readiness(
            "ALGO-MM-01", as_of=datetime.date(2027, 6, 1)
        )
        self.assertFalse(is_ready)
        self.assertTrue(any("certificate expired on 2027-01-15" in i for i in issues))

    def test_certificate_validity_boundary_is_exclusive_on_expiry_day(self):
        """Valid on the last day of the window; not valid on the expiry date itself."""
        self.assertTrue(self.dev_alice.is_certificate_current(datetime.date(2027, 1, 14)))
        self.assertFalse(self.dev_alice.is_certificate_current(datetime.date(2027, 1, 15)))

    def test_shorter_certificate_window_is_honoured(self):
        dev = CertifiedDeveloper(
            dev_id="DEV-BOB",
            name="Bob Quant",
            role_title="Algo Approver",
            status=CertificationStatus.FIT_AND_PROPER,
            last_assessment_date=datetime.date(2026, 1, 15),
            accredited_by_smf_id="SMF-24-JOHN",
            certificate_validity_months=6,
        )
        self.assertEqual(dev.certificate_expiry_date, datetime.date(2026, 7, 15))

    def test_certificate_window_cannot_exceed_twelve_months(self):
        with self.assertRaises(SMCRError):
            CertifiedDeveloper(
                dev_id="DEV-TOOLONG",
                name="Too Long",
                role_title="Algo Approver",
                status=CertificationStatus.FIT_AND_PROPER,
                last_assessment_date=datetime.date(2026, 1, 15),
                accredited_by_smf_id="SMF-24-JOHN",
                certificate_validity_months=MAX_CERTIFICATE_VALIDITY_MONTHS + 1,
            )

    def test_leap_day_assessment_clamps_to_end_of_february(self):
        self.assertEqual(_add_months(datetime.date(2024, 2, 29), 12), datetime.date(2025, 2, 28))
        self.assertEqual(_add_months(datetime.date(2026, 1, 31), 1), datetime.date(2026, 2, 28))
        self.assertEqual(_add_months(datetime.date(2026, 12, 15), 12), datetime.date(2027, 12, 15))

    def test_suspended_developer_blocks_deployment(self):
        self.dev_alice.status = CertificationStatus.SUSPENDED
        self.engine.certify_developer(self.dev_alice)
        self.engine.register_algo_strategy(_algo())
        self._approve()
        is_ready, issues = self.engine.verify_algo_deployment_readiness("ALGO-MM-01", as_of=AS_OF)
        self.assertFalse(is_ready)
        self.assertTrue(any("lacks active Fit & Proper Certification" in i for i in issues))

    # --- Version-bound sign-off ---------------------------------------

    def test_sign_off_does_not_carry_over_to_amended_algo(self):
        """Regression: a v1.2.0 approval used to authorise an amended v1.3.0."""
        self.engine.register_algo_strategy(_algo(version="1.2.0"))
        self._approve()
        self.assertTrue(self.engine.verify_algo_deployment_readiness("ALGO-MM-01", as_of=AS_OF)[0])

        self.engine.register_algo_strategy(_algo(version="1.3.0"))
        is_ready, issues = self.engine.verify_algo_deployment_readiness("ALGO-MM-01", as_of=AS_OF)
        self.assertFalse(is_ready)
        self.assertTrue(any("v1.3.0" in i for i in issues))
        self.assertIsNotNone(self.engine.get_sign_off("ALGO-MM-01", "1.2.0"))

    def test_sign_off_for_unregistered_version_rejected(self):
        self.engine.register_algo_strategy(_algo(version="1.2.0"))
        with self.assertRaises(SMCRError) as ctx:
            self._approve(version="9.9.9")
        self.assertIn("registered version", str(ctx.exception))

    def test_recorded_sign_off_immune_to_caller_mutation(self):
        """The register keeps a copy, so flipping the submitted object's status
        afterwards cannot rewrite the recorded decision."""
        self.engine.register_algo_strategy(_algo())
        submitted = DeploymentSignOff(
            sign_off_id="SIG-REJ",
            algo_id="ALGO-MM-01",
            smf_id="SMF-24-JOHN",
            status=SignOffStatus.REJECTED,
            reasonable_steps_notes=GOOD_NOTES,
        )
        self.engine.execute_deployment_sign_off(submitted)
        submitted.status = SignOffStatus.APPROVED

        self.assertEqual(
            self.engine.get_sign_off("ALGO-MM-01", "1.2.0").status, SignOffStatus.REJECTED
        )
        self.assertFalse(self.engine.verify_algo_deployment_readiness("ALGO-MM-01", as_of=AS_OF)[0])

    def test_sign_off_records_resolved_version(self):
        self.engine.register_algo_strategy(_algo(version="1.2.0"))
        recorded = self._approve()  # algo_version left as None
        self.assertEqual(recorded.algo_version, "1.2.0")

    def test_sign_off_timestamp_is_timezone_aware_utc(self):
        self.engine.register_algo_strategy(_algo())
        recorded = self._approve()
        self.assertIsNotNone(recorded.sign_off_timestamp.tzinfo)
        self.assertEqual(recorded.sign_off_timestamp.utcoffset(), datetime.timedelta(0))

    # --- Reasonable-steps notes ---------------------------------------

    def test_invalid_reasonable_steps_raises_error(self):
        self.engine.register_algo_strategy(_algo())
        with self.assertRaises(SMCRError):
            self._approve(notes="OK")

    def test_whitespace_only_notes_rejected(self):
        self.engine.register_algo_strategy(_algo())
        with self.assertRaises(SMCRError):
            self._approve(notes="              ")

    def test_boilerplate_notes_rejected(self):
        """'signed off' is exactly 10 chars, so it clears the length guard while
        recording nothing about the checks actually performed."""
        self.engine.register_algo_strategy(_algo())
        self.assertEqual(len("signed off"), self.engine.min_reasonable_steps_chars)
        with self.assertRaises(SMCRError) as ctx:
            self._approve(notes="  Signed Off  ")
        self.assertIn("content-free", str(ctx.exception))

    def test_sign_off_by_unregistered_smf_rejected(self):
        self.engine.register_algo_strategy(_algo())
        with self.assertRaises(SMCRError):
            self.engine.execute_deployment_sign_off(
                DeploymentSignOff(
                    sign_off_id="SIG-X",
                    algo_id="ALGO-MM-01",
                    smf_id="SMF-99-GHOST",
                    status=SignOffStatus.APPROVED,
                    reasonable_steps_notes=GOOD_NOTES,
                )
            )

    # --- MRM report ----------------------------------------------------

    def test_generate_mrm_report(self):
        self.engine.register_algo_strategy(_algo())
        report = self.engine.generate_mrm_report(as_of=AS_OF)
        self.assertEqual(report.total_registered_algos, 1)
        self.assertEqual(report.compliant_algos_count, 0)  # Sign-off missing
        self.assertTrue(len(report.audit_trail) >= 2)

    def test_mrm_report_counts_compliant_algo(self):
        self.engine.register_algo_strategy(_algo())
        self._approve()
        report = self.engine.generate_mrm_report(as_of=AS_OF)
        self.assertEqual(report.compliant_algos_count, 1)
        self.assertEqual(report.uncertified_dev_algos, [])
        self.assertTrue(any("[COMPLIANT]" in line for line in report.audit_trail))

    def test_mrm_report_flags_uncertified_dev_algos(self):
        """Regression: uncertified_dev_algos was declared but never populated."""
        self.engine.register_algo_strategy(_algo())
        self._approve()
        report = self.engine.generate_mrm_report(as_of=datetime.date(2027, 6, 1))
        self.assertEqual(report.uncertified_dev_algos, ["ALGO-MM-01"])
        self.assertEqual(report.compliant_algos_count, 0)

    def test_mrm_report_records_firm_tier_scope(self):
        report = self.engine.generate_mrm_report(as_of=AS_OF)
        self.assertEqual(report.firm_tier, SMCRFirmTier.ENHANCED)
        self.assertTrue(report.mrm_required)
        self.assertIsNotNone(report.generated_at.tzinfo)

    def test_mrm_report_marks_core_firm_out_of_sysc25_scope(self):
        core_engine = SMCRAlgoAccountabilityEngine(firm_tier=SMCRFirmTier.CORE)
        report = core_engine.generate_mrm_report(as_of=AS_OF)
        self.assertFalse(report.mrm_required)
        self.assertIn("MRM required=False", report.audit_trail[0])


if __name__ == "__main__":
    unittest.main()
