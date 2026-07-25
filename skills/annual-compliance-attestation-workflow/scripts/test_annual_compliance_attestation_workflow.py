import unittest
from datetime import datetime
from annual_compliance_attestation_workflow import (
    AnnualComplianceChecklist,
    AnnualComplianceAttestationEngine
)

class TestAnnualComplianceAttestationWorkflow(unittest.TestCase):
    def setUp(self):
        self.engine = AnnualComplianceAttestationEngine()
        self.current_year = 2026
        
    def _get_valid_date(self):
        return datetime(self.current_year, 11, 15)

    def test_valid_hedge_fund(self):
        # RIA (Not a broker-dealer) - valid setup
        checklist = AnnualComplianceChecklist(
            reporting_year=self.current_year,
            is_broker_dealer=False,
            annual_policy_review_date=self._get_valid_date(),
            cco_report_submitted_date=self._get_valid_date(),
            algo_code_integrity_review_date=self._get_valid_date(),
            trade_surveillance_test_date=self._get_valid_date()
        )
        report = self.engine.evaluate(checklist)
        self.assertTrue(report.is_ready_for_attestation)
        self.assertEqual(len(report.missing_requirements), 0)

    def test_missing_sec_policy_review(self):
        # Missing SEC 206(4)-7 review
        checklist = AnnualComplianceChecklist(
            reporting_year=self.current_year,
            is_broker_dealer=False,
            annual_policy_review_date=None, # Missing
            cco_report_submitted_date=self._get_valid_date(),
            algo_code_integrity_review_date=self._get_valid_date(),
            trade_surveillance_test_date=self._get_valid_date()
        )
        report = self.engine.evaluate(checklist)
        self.assertFalse(report.is_ready_for_attestation)
        self.assertIn("SEC Rule 206(4)-7: Annual review", report.missing_requirements[0])

    def test_missing_quant_controls(self):
        # Missing algo code review
        checklist = AnnualComplianceChecklist(
            reporting_year=self.current_year,
            is_broker_dealer=False,
            annual_policy_review_date=self._get_valid_date(),
            cco_report_submitted_date=self._get_valid_date(),
            algo_code_integrity_review_date=None, # Missing
            trade_surveillance_test_date=self._get_valid_date()
        )
        report = self.engine.evaluate(checklist)
        self.assertFalse(report.is_ready_for_attestation)
        self.assertIn("Quant Control: Annual algorithmic code", report.missing_requirements[0])

    def test_broker_dealer_missing_ceo_meeting(self):
        # FINRA BD missing CEO-CCO meeting
        checklist = AnnualComplianceChecklist(
            reporting_year=self.current_year,
            is_broker_dealer=True,
            annual_policy_review_date=self._get_valid_date(),
            cco_report_submitted_date=self._get_valid_date(),
            algo_code_integrity_review_date=self._get_valid_date(),
            trade_surveillance_test_date=self._get_valid_date(),
            ceo_cco_meeting_date=None, # Missing FINRA requirement
            ceo_certification_signed=True
        )
        report = self.engine.evaluate(checklist)
        self.assertFalse(report.is_ready_for_attestation)
        self.assertIn("FINRA Rule 3130: Mandatory CEO-CCO compliance meeting", report.missing_requirements[0])

if __name__ == '__main__':
    unittest.main()
