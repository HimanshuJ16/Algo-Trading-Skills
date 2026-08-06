import unittest
import datetime
from uk_senior_managers_regime_algo_accountability import (
    SMFRole,
    CertificationStatus,
    SignOffStatus,
    SeniorManager,
    CertifiedDeveloper,
    AlgoStrategyRegistration,
    DeploymentSignOff,
    SMCRAlgoAccountabilityEngine,
    SMCRError,
)


class TestSMCRAlgoAccountabilityEngine(unittest.TestCase):
    def setUp(self):
        self.engine = SMCRAlgoAccountabilityEngine()

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

    def test_senior_manager_registration(self):
        self.assertEqual(len(self.engine.senior_managers), 2)
        self.assertIn("SMF-24-JOHN", self.engine.senior_managers)

    def test_developer_certification_fitness_and_propriety(self):
        self.assertEqual(len(self.engine.certified_developers), 1)
        self.assertEqual(self.dev_alice.status, CertificationStatus.FIT_AND_PROPER)

    def test_algo_strategy_registration(self):
        algo = AlgoStrategyRegistration(
            algo_id="ALGO-MM-01",
            name="Index Market Maker",
            version="1.2.0",
            responsible_smf_id="SMF-24-JOHN",
            certified_dev_ids=["DEV-ALICE"],
            pre_trade_risk_approved=True,
            kill_switch_tested=True,
            stress_tested=True,
        )
        self.engine.register_algo_strategy(algo)
        self.assertIn("ALGO-MM-01", self.engine.algo_registrations)

    def test_deployment_readiness_fails_without_sign_off(self):
        algo = AlgoStrategyRegistration(
            algo_id="ALGO-MM-01",
            name="Index Market Maker",
            version="1.2.0",
            responsible_smf_id="SMF-24-JOHN",
            certified_dev_ids=["DEV-ALICE"],
            pre_trade_risk_approved=True,
            kill_switch_tested=True,
            stress_tested=True,
        )
        self.engine.register_algo_strategy(algo)

        is_ready, issues = self.engine.verify_algo_deployment_readiness("ALGO-MM-01")
        self.assertFalse(is_ready)
        self.assertTrue(any("Formal SMF Deployment Sign-Off is missing" in i for i in issues))

    def test_deployment_readiness_passes_with_full_sign_off(self):
        algo = AlgoStrategyRegistration(
            algo_id="ALGO-MM-01",
            name="Index Market Maker",
            version="1.2.0",
            responsible_smf_id="SMF-24-JOHN",
            certified_dev_ids=["DEV-ALICE"],
            pre_trade_risk_approved=True,
            kill_switch_tested=True,
            stress_tested=True,
        )
        self.engine.register_algo_strategy(algo)

        sign_off = DeploymentSignOff(
            sign_off_id="SIG-001",
            algo_id="ALGO-MM-01",
            smf_id="SMF-24-JOHN",
            status=SignOffStatus.APPROVED,
            reasonable_steps_notes="Reviewed RTS 6 stress tests, kill switch latency < 1ms, pre-trade price collars verified.",
        )
        self.engine.execute_deployment_sign_off(sign_off)

        is_ready, issues = self.engine.verify_algo_deployment_readiness("ALGO-MM-01")
        self.assertTrue(is_ready)
        self.assertEqual(len(issues), 0)

    def test_invalid_reasonable_steps_raises_error(self):
        algo = AlgoStrategyRegistration(
            algo_id="ALGO-MM-01",
            name="Index Market Maker",
            version="1.2.0",
            responsible_smf_id="SMF-24-JOHN",
            certified_dev_ids=["DEV-ALICE"],
        )
        self.engine.register_algo_strategy(algo)

        bad_sign_off = DeploymentSignOff(
            sign_off_id="SIG-BAD",
            algo_id="ALGO-MM-01",
            smf_id="SMF-24-JOHN",
            status=SignOffStatus.APPROVED,
            reasonable_steps_notes="OK",  # Too short! < 10 chars
        )
        with self.assertRaises(SMCRError):
            self.engine.execute_deployment_sign_off(bad_sign_off)

    def test_generate_mrm_report(self):
        algo = AlgoStrategyRegistration(
            algo_id="ALGO-MM-01",
            name="Index Market Maker",
            version="1.2.0",
            responsible_smf_id="SMF-24-JOHN",
            certified_dev_ids=["DEV-ALICE"],
            pre_trade_risk_approved=True,
            kill_switch_tested=True,
            stress_tested=True,
        )
        self.engine.register_algo_strategy(algo)

        report = self.engine.generate_mrm_report()
        self.assertEqual(report.total_registered_algos, 1)
        self.assertEqual(report.compliant_algos_count, 0)  # Sign-off missing
        self.assertTrue(len(report.audit_trail) >= 2)


if __name__ == "__main__":
    unittest.main()

