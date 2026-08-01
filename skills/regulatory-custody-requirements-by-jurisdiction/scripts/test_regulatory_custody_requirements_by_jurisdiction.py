import unittest
from regulatory_custody_requirements_by_jurisdiction import (
    Analyzer, Result, RegulatoryCustodyRequirementsByJurisdictionEngine,
    CustodySetup, JurisdictionalCustodyAuditReport
)

class TestAnalyzer(unittest.TestCase):
    def test_legacy_success(self):
        analyzer = Analyzer({"key": "value"})
        res = analyzer.execute()
        self.assertTrue(res.success)

    def test_legacy_failure(self):
        analyzer = Analyzer({})
        res = analyzer.execute()
        self.assertFalse(res.success)

class TestRegulatoryCustodyRequirementsByJurisdictionEngine(unittest.TestCase):

    def setUp(self):
        self.engine = RegulatoryCustodyRequirementsByJurisdictionEngine()

    def test_us_compliant_custody_setup(self):
        setup = CustodySetup(
            jurisdiction="US",
            custodian_name="BNY Mellon",
            custody_type="QUALIFIED_CUSTODIAN",
            is_asset_segregated=True,
            has_annual_audit=True,
            has_insurance_coverage=False
        )
        report = self.engine.audit_custody_setup(setup)
        self.assertEqual(report.status, "CUSTODY_COMPLIANT")
        self.assertTrue(report.is_compliant)
        self.assertEqual(len(report.violations), 0)

    def test_eu_missing_insurance_violation(self):
        setup = CustodySetup(
            jurisdiction="EU",
            custodian_name="CustodyTrust EU",
            custody_type="QUALIFIED_CUSTODIAN",
            is_asset_segregated=True,
            has_annual_audit=True,
            has_insurance_coverage=False
        )
        report = self.engine.audit_custody_setup(setup)
        self.assertEqual(report.status, "CUSTODY_VIOLATION")
        self.assertFalse(report.is_compliant)
        self.assertTrue(any(v.violation_type == "MISSING_INSURANCE" for v in report.violations))

    def test_self_custody_violation(self):
        setup = CustodySetup(
            jurisdiction="UK",
            custodian_name="Internal Cold Wallet",
            custody_type="SELF_CUSTODY",
            is_asset_segregated=True,
            has_annual_audit=True
        )
        report = self.engine.audit_custody_setup(setup)
        self.assertEqual(report.status, "CUSTODY_VIOLATION")
        self.assertTrue(any(v.violation_type == "UNQUALIFIED_CUSTODIAN" for v in report.violations))

if __name__ == '__main__':
    unittest.main()
