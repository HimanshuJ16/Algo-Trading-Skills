import unittest
from insider_trading_controls_for_alternative_data_usage import (
    AltDataInsiderTradingComplianceEngine, AltDataDatasetSpec, AltDataComplianceReport
)

class TestAltDataInsiderTradingComplianceEngine(unittest.TestCase):

    def setUp(self):
        self.engine = AltDataInsiderTradingComplianceEngine(
            min_panel_aggregation_count=50, earnings_blackout_window_hours=48.0
        )

    def test_low_risk_alt_data_approval(self):
        spec = AltDataDatasetSpec(
            dataset_name="Orbital_Satellite_Parking_Lot_V2",
            data_source_type="SATELLITE_IMAGERY",
            has_mnpi_risk=False,
            has_vendor_diligence_signoff=True,
            is_tos_compliant=True,
            is_pii_scrubbed=True,
            panel_aggregation_count=250,
            hours_to_earnings_release=72.0
        )
        report = self.engine.audit_alt_data_dataset(spec)

        self.assertEqual(report.risk_classification, "LOW_RISK_APPROVED")
        self.assertTrue(report.is_mnpi_cleared)
        self.assertTrue(report.is_vendor_diligence_cleared)
        self.assertTrue(report.is_pii_anonymization_cleared)
        self.assertTrue(report.is_blackout_window_cleared)

    def test_mnpi_risk_rejection(self):
        # dataset has_mnpi_risk = True -> REJECTED_MNPI_RISK!
        spec = AltDataDatasetSpec(
            dataset_name="Leaked_Executive_Emails",
            data_source_type="WEB_SCRAPED",
            has_mnpi_risk=True,
            has_vendor_diligence_signoff=False,
            is_tos_compliant=False,
            is_pii_scrubbed=False,
            panel_aggregation_count=1,
            hours_to_earnings_release=10.0
        )
        report = self.engine.audit_alt_data_dataset(spec)

        self.assertEqual(report.risk_classification, "REJECTED_MNPI_RISK")
        self.assertFalse(report.is_mnpi_cleared)

    def test_unaggregated_pii_rejection(self):
        # Panel count = 5 < min 50 -> REJECTED_UNAGGREGATED_PII!
        spec = AltDataDatasetSpec(
            dataset_name="Raw_Consumer_Geolocation",
            data_source_type="GEOLOCATION",
            has_mnpi_risk=False,
            has_vendor_diligence_signoff=True,
            is_tos_compliant=True,
            is_pii_scrubbed=True,
            panel_aggregation_count=5,
            hours_to_earnings_release=96.0
        )
        report = self.engine.audit_alt_data_dataset(spec)

        self.assertEqual(report.risk_classification, "REJECTED_UNAGGREGATED_PII")
        self.assertFalse(report.is_pii_anonymization_cleared)

if __name__ == '__main__':
    unittest.main()
