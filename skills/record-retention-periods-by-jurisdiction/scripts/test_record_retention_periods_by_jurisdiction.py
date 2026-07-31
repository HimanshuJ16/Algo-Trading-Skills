import unittest
from record_retention_periods_by_jurisdiction import (
    RecordRetentionPeriodsByJurisdictionEngine, ComplianceResult,
    RetentionRecord, RetentionComplianceReport
)

class TestRecordRetentionPeriodsByJurisdiction(unittest.TestCase):

    def setUp(self):
        self.engine = RecordRetentionPeriodsByJurisdictionEngine()

    def test_legacy_valid(self):
        res = self.engine.check({"valid": True})
        self.assertTrue(res.is_compliant)

    def test_legacy_invalid(self):
        res = self.engine.check({"valid": False})
        self.assertFalse(res.is_compliant)

    def test_legacy_edge(self):
        res = self.engine.check({})
        self.assertFalse(res.is_compliant)

    def test_us_record_non_compliant_short_retention(self):
        records = [
            RetentionRecord("REC_001", "TRADE", "US", "2020-01-01", current_retention_years=5.0)
        ]
        report = self.engine.validate_retention(records)
        self.assertEqual(report.overall_status, "ISSUES_FOUND")
        self.assertEqual(report.non_compliant_count, 1)
        self.assertEqual(report.results[0].status, "NON_COMPLIANT")
        self.assertEqual(report.results[0].years_surplus_or_deficit, -2.0)

    def test_uk_record_compliant_sufficient_retention(self):
        records = [
            RetentionRecord("REC_002", "TRADE", "UK", "2018-06-01", current_retention_years=6.0)
        ]
        report = self.engine.validate_retention(records)
        self.assertEqual(report.overall_status, "ALL_COMPLIANT")
        self.assertEqual(report.compliant_count, 1)
        self.assertEqual(report.results[0].status, "COMPLIANT")
        self.assertEqual(report.results[0].years_surplus_or_deficit, 1.0)

    def test_unknown_jurisdiction_flagged(self):
        records = [
            RetentionRecord("REC_003", "ORDER_AUDIT", "XX", "2021-01-01", current_retention_years=10.0)
        ]
        report = self.engine.validate_retention(records)
        self.assertEqual(report.overall_status, "ISSUES_FOUND")
        self.assertEqual(report.unknown_jurisdiction_count, 1)
        self.assertEqual(report.results[0].status, "UNKNOWN_JURISDICTION")

if __name__ == '__main__':
    unittest.main()
