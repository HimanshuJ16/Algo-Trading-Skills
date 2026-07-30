import unittest
from finra_algo_trading_registration_requirements import (
    FinraAlgoRegistrationEngine, DeveloperCredentials, AlgoCodeCommitRequest, FinraRegistrationAuditReport
)

class TestFinraAlgoRegistrationEngine(unittest.TestCase):

    def setUp(self):
        self.engine = FinraAlgoRegistrationEngine()

        # Register personnel:
        # Dev A: Registered Series 57
        self.engine.register_personnel(DeveloperCredentials("DEV_A", "Alice Quant", "QUANT_DEV", is_series_57_active=True, is_sie_active=True, crd_number="123456"))
        # Sup A: Registered Series 57 Supervisor
        self.engine.register_personnel(DeveloperCredentials("SUP_A", "Bob Manager", "DESK_HEAD", is_series_57_active=True, is_sie_active=True, crd_number="654321"))
        # Dev B: Unregistered Engineer
        self.engine.register_personnel(DeveloperCredentials("DEV_B", "Charlie Unregistered", "SOFTWARE_ENG", is_series_57_active=False, is_sie_active=True))

    def test_registered_developer_commit_approved(self):
        req = AlgoCodeCommitRequest(
            commit_id="COMMIT_101", algorithm_id="VWAP_V2", algorithm_name="VWAP Router",
            author_id="DEV_A", approving_supervisor_id="SUP_A",
            is_significant_modification=True, modifies_order_routing_logic=True
        )
        report = self.engine.audit_code_commit(req)

        self.assertTrue(report.is_rule_1220b4_applicable)
        self.assertTrue(report.author_series_57_valid)
        self.assertEqual(report.cicd_gate_status, "COMPLIANCE_APPROVED")

    def test_unregistered_developer_commit_blocked(self):
        # Developer B lacks Series 57 -> REGISTRATION VIOLATION BLOCKED!
        req = AlgoCodeCommitRequest(
            commit_id="COMMIT_102", algorithm_id="HFT_MM_V1", algorithm_name="Market Maker Engine",
            author_id="DEV_B", approving_supervisor_id="SUP_A",
            is_significant_modification=True, modifies_order_routing_logic=True
        )
        report = self.engine.audit_code_commit(req)

        self.assertTrue(report.is_rule_1220b4_applicable)
        self.assertFalse(report.author_series_57_valid)
        self.assertEqual(report.cicd_gate_status, "REGISTRATION_VIOLATION_BLOCKED")
        self.assertIn("lacks active Series 57", report.audit_notes)

if __name__ == '__main__':
    unittest.main()
