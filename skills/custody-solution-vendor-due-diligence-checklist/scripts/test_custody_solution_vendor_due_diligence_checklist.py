import unittest
from custody_solution_vendor_due_diligence_checklist import (
    CustodyVendorDueDiligenceEngine, CustodyVendorProfile, CustodyVendorDueDiligenceReport
)

class TestCustodyVendorDueDiligenceEngine(unittest.TestCase):

    def setUp(self):
        self.engine = CustodyVendorDueDiligenceEngine(min_passing_score=80.0, min_insurance_usd=50_000_000.0)

    def test_tier1_qualified_custodian_approved(self):
        profile = CustodyVendorProfile(
            vendor_id="TIER1_TRUST",
            vendor_name="Tier 1 Institutional Trust Co",
            charter_type="STATE_CHARTERED_TRUST",
            is_sec_qualified_custodian=True,
            has_soc2_type2_unqualified=True,
            is_asset_bankruptcy_remote=True,
            crime_insurance_coverage_usd=100_000_000.0,
            fips_140_2_level=3,
            uptime_sla_pct=99.95,
            rto_hours=2.0,
            conducts_annual_pen_tests=True
        )
        report = self.engine.evaluate_custodian(profile)
        
        self.assertEqual(report.decision_status, "APPROVED")
        self.assertGreaterEqual(report.composite_due_diligence_score, 90.0)
        self.assertEqual(len(report.critical_red_flags), 0)

    def test_non_qualified_custodian_rejected_with_red_flags(self):
        profile = CustodyVendorProfile(
            vendor_id="UNLICENSED_CUSTODY",
            vendor_name="Unlicensed Crypto Custody Inc",
            charter_type="UNLICENSED",
            is_sec_qualified_custodian=False,
            has_soc2_type2_unqualified=False,
            is_asset_bankruptcy_remote=False, # Red flag!
            crime_insurance_coverage_usd=1_000_000.0,
            fips_140_2_level=1,
            uptime_sla_pct=95.0,
            rto_hours=24.0,
            conducts_annual_pen_tests=False
        )
        report = self.engine.evaluate_custodian(profile)
        
        self.assertEqual(report.decision_status, "REJECTED")
        self.assertGreater(len(report.critical_red_flags), 0)

if __name__ == '__main__':
    unittest.main()
