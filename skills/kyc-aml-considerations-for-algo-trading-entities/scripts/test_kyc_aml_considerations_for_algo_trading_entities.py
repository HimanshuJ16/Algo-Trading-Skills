import unittest
from kyc_aml_considerations_for_algo_trading_entities import (
    KycAmlEntityComplianceEngine, EntityKycAmlPayload, UboRecord, KycAmlAuditReport
)

class TestKycAmlEntityComplianceEngine(unittest.TestCase):

    def setUp(self):
        self.engine = KycAmlEntityComplianceEngine(ubo_ownership_threshold_pct=25.0)

    def test_verified_institutional_fund_approval(self):
        ubos = [
            UboRecord(name="Alice Smith", ownership_pct=60.0, is_pep=False, is_sanctioned=False, is_identity_verified=True),
            UboRecord(name="Bob Jones", ownership_pct=40.0, is_pep=False, is_sanctioned=False, is_identity_verified=True)
        ]
        payload = EntityKycAmlPayload(
            entity_name="Alpha Quant LP", incorporation_country="USA", banking_country="USA", ubos=ubos
        )
        report = self.engine.audit_entity_compliance(payload)

        self.assertEqual(report.status, "KYC_AML_APPROVED")
        self.assertEqual(report.total_ubo_ownership_accounted_pct, 100.0)
        self.assertFalse(report.has_sanctions_hit)
        self.assertFalse(report.is_fatf_blacklisted)

    def test_unverified_ubo_rejection(self):
        # 40% owner unverified -> REJECTED_UNVERIFIED_UBO!
        ubos = [
            UboRecord(name="Alice Smith", ownership_pct=60.0, is_pep=False, is_sanctioned=False, is_identity_verified=True),
            UboRecord(name="Mystery Owner", ownership_pct=40.0, is_pep=False, is_sanctioned=False, is_identity_verified=False)
        ]
        payload = EntityKycAmlPayload(
            entity_name="Opaque Fund LP", incorporation_country="CAYMAN_ISLANDS", banking_country="UK", ubos=ubos
        )
        report = self.engine.audit_entity_compliance(payload)

        self.assertEqual(report.status, "REJECTED_UNVERIFIED_UBO")
        self.assertEqual(report.unverified_ubos_count, 1)

    def test_sanctions_match_rejection(self):
        # Sanctioned UBO -> REJECTED_SANCTIONS_MATCH!
        ubos = [
            UboRecord(name="Sanctioned Individual", ownership_pct=50.0, is_pep=True, is_sanctioned=True, is_identity_verified=True)
        ]
        payload = EntityKycAmlPayload(
            entity_name="Rogue Trading Co", incorporation_country="UK", banking_country="UK", ubos=ubos
        )
        report = self.engine.audit_entity_compliance(payload)

        self.assertEqual(report.status, "REJECTED_SANCTIONS_MATCH")
        self.assertTrue(report.has_sanctions_hit)

if __name__ == '__main__':
    unittest.main()
