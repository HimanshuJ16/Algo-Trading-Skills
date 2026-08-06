import unittest
import datetime
from third_party_custody_audit_report_review_cadence import (
    CustodyVendor,
    AuditReport,
    GapLetter,
    CUECCheck,
    CustodyAuditReviewEngine,
    ReportType,
    AuditOpinion,
    ComplianceStatus,
    RiskRating,
    CustodyAuditError,
)


class TestCustodyAuditReviewEngine(unittest.TestCase):
    def setUp(self):
        self.engine = CustodyAuditReviewEngine()
        self.vendor = CustodyVendor(
            vendor_id="VEND-001",
            name="Coinbase Custody Trust Co",
            asset_classes_held=["BTC", "ETH", "SOL"],
            total_aum_usd=25_000_000.0,
            review_cadence_days=365,
        )
        self.engine.register_vendor(self.vendor)

    def test_missing_audit_reports_triggers_critical_risk(self):
        eval_date = datetime.date(2026, 8, 1)
        res = self.engine.evaluate_vendor_compliance("VEND-001", eval_date)
        self.assertEqual(res.status, ComplianceStatus.NON_COMPLIANT)
        self.assertEqual(res.risk_rating, RiskRating.CRITICAL)
        self.assertIn("No audit reports on file.", res.findings)

    def test_clean_unqualified_soc_report(self):
        report = AuditReport(
            report_id="SOC1-2025",
            vendor_id="VEND-001",
            report_type=ReportType.SOC1_TYPE2,
            opinion=AuditOpinion.UNQUALIFIED,
            coverage_start=datetime.date(2025, 1, 1),
            coverage_end=datetime.date(2025, 12, 31),
            report_date=datetime.date(2026, 2, 15),
            deficiencies_found=0,
        )
        self.engine.submit_audit_report(report)

        eval_date = datetime.date(2026, 6, 1)  # Within 365 days of coverage_end
        res = self.engine.evaluate_vendor_compliance("VEND-001", eval_date)
        self.assertEqual(res.status, ComplianceStatus.COMPLIANT)
        self.assertEqual(res.risk_rating, RiskRating.LOW)
        self.assertEqual(len(res.findings), 0)

    def test_qualified_opinion_triggers_escalation(self):
        report = AuditReport(
            report_id="SOC1-2025-QUAL",
            vendor_id="VEND-001",
            report_type=ReportType.SOC1_TYPE2,
            opinion=AuditOpinion.QUALIFIED,
            coverage_start=datetime.date(2025, 1, 1),
            coverage_end=datetime.date(2025, 12, 31),
            report_date=datetime.date(2026, 2, 15),
            deficiencies_found=2,
        )
        self.engine.submit_audit_report(report)

        eval_date = datetime.date(2026, 6, 1)
        res = self.engine.evaluate_vendor_compliance("VEND-001", eval_date)
        self.assertEqual(res.status, ComplianceStatus.ESCALATED)
        self.assertEqual(res.risk_rating, RiskRating.CRITICAL)

    def test_expired_soc_report_without_gap_letter_is_overdue(self):
        report = AuditReport(
            report_id="SOC1-2024",
            vendor_id="VEND-001",
            report_type=ReportType.SOC1_TYPE2,
            opinion=AuditOpinion.UNQUALIFIED,
            coverage_start=datetime.date(2024, 1, 1),
            coverage_end=datetime.date(2024, 12, 31),
            report_date=datetime.date(2025, 2, 15),
        )
        self.engine.submit_audit_report(report)

        # Evaluated in August 2026 (>365 days since Dec 31, 2024 coverage end)
        eval_date = datetime.date(2026, 8, 1)
        res = self.engine.evaluate_vendor_compliance("VEND-001", eval_date)
        self.assertEqual(res.status, ComplianceStatus.OVERDUE)
        self.assertEqual(res.risk_rating, RiskRating.HIGH)

    def test_expired_soc_report_with_valid_gap_letter_passes(self):
        report = AuditReport(
            report_id="SOC1-2024",
            vendor_id="VEND-001",
            report_type=ReportType.SOC1_TYPE2,
            opinion=AuditOpinion.UNQUALIFIED,
            coverage_start=datetime.date(2024, 1, 1),
            coverage_end=datetime.date(2024, 12, 31),
            report_date=datetime.date(2025, 2, 15),
        )
        self.engine.submit_audit_report(report)

        # Submit Gap letter covering Jan 1, 2025 to June 30, 2025
        gap = GapLetter(
            letter_id="GAP-2025-Q2",
            vendor_id="VEND-001",
            report_id="SOC1-2024",
            period_start=datetime.date(2025, 1, 1),
            period_end=datetime.date(2025, 6, 30),
            no_material_changes_asserted=True,
        )
        self.engine.submit_gap_letter(gap)

        # Evaluated in August 2025 (Gap letter is valid within 180 days of June 30, 2025)
        eval_date = datetime.date(2025, 8, 1)
        res = self.engine.evaluate_vendor_compliance("VEND-001", eval_date)
        self.assertEqual(res.status, ComplianceStatus.COMPLIANT)
        self.assertEqual(res.risk_rating, RiskRating.LOW)

    def test_unimplemented_cuec_increases_risk_rating(self):
        report = AuditReport(
            report_id="SOC1-2025",
            vendor_id="VEND-001",
            report_type=ReportType.SOC1_TYPE2,
            opinion=AuditOpinion.UNQUALIFIED,
            coverage_start=datetime.date(2025, 1, 1),
            coverage_end=datetime.date(2025, 12, 31),
            report_date=datetime.date(2026, 2, 15),
        )
        self.engine.submit_audit_report(report)

        cuec_checks = [
            CUECCheck("CUEC-1", "Multi-user withdrawal signoff", True, "Configured in Portal"),
            CUECCheck("CUEC-2", "IP Whitelisting enforced", False, "Missing Evidence"),
        ]
        self.engine.update_cuec_checks("VEND-001", cuec_checks)

        eval_date = datetime.date(2026, 6, 1)
        res = self.engine.evaluate_vendor_compliance("VEND-001", eval_date)
        self.assertEqual(res.implemented_cuec_pct, 50.0)
        self.assertEqual(res.risk_rating, RiskRating.MEDIUM)
        self.assertIn("Unimplemented internal CUEC controls: CUEC-2", res.findings)


if __name__ == "__main__":
    unittest.main()

