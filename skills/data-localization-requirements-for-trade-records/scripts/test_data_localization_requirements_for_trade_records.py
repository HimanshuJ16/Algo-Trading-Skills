import unittest
from data_localization_requirements_for_trade_records import (
    DataLocalizationComplianceEngine, TradeRecordPayload, DataLocalizationAuditReport
)

class TestDataLocalizationComplianceEngine(unittest.TestCase):

    def setUp(self):
        self.engine = DataLocalizationComplianceEngine()

    def test_chinese_trade_record_egress_to_us_blocked(self):
        # Chinese trade record attempting to store in us-east-1 -> BLOCKED!
        rec = TradeRecordPayload("TR_CN_001", "CN", "us-east-1", "TRADE_EXECUTION", is_primary_store=True)
        report = self.engine.audit_trade_record_localization(rec)
        
        self.assertFalse(report.is_compliant)
        self.assertEqual(report.status, "LOCALIZATION_VIOLATION_BLOCKED")
        self.assertGreater(len(report.remediation_instructions), 0)

    def test_indian_trade_record_in_mumbai_approved(self):
        # Indian trade record stored in ap-south-1 (Mumbai) -> APPROVED!
        rec = TradeRecordPayload("TR_IN_001", "IN", "ap-south-1", "PAYMENT_LEDGER", is_primary_store=True)
        report = self.engine.audit_trade_record_localization(rec)
        
        self.assertTrue(report.is_compliant)
        self.assertEqual(report.status, "COMPLIANT")

if __name__ == '__main__':
    unittest.main()
