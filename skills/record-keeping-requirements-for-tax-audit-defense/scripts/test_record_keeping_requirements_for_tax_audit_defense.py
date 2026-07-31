import unittest
from record_keeping_requirements_for_tax_audit_defense import (
    RecordKeepingRequirementsForTaxAuditDefenseEngine, Record,
    TradeRecord, TaxAuditComplianceReport
)

class TestRecordKeepingRequirementsForTaxAuditDefenseEngine(unittest.TestCase):

    def setUp(self):
        self.engine = RecordKeepingRequirementsForTaxAuditDefenseEngine()

    def test_legacy_initialization(self):
        self.assertEqual(len(self.engine.records), 0)

    def test_legacy_add_record(self):
        self.engine.add_record(Record("1", 10.0))
        self.assertEqual(len(self.engine.records), 1)

    def test_legacy_process(self):
        self.engine.add_record(Record("1", 10.0))
        self.engine.add_record(Record("2", 20.0))
        self.assertEqual(self.engine.process(), 30.0)

    def test_audit_compliant_all_fields_present(self):
        self.engine.add_trade_record(TradeRecord(
            trade_id="T001", symbol="AAPL", side="BUY", quantity=100.0,
            price=150.0, trade_date="2024-01-15", cost_basis_usd=15000.0,
            holding_period_days=200
        ))
        self.engine.add_trade_record(TradeRecord(
            trade_id="T002", symbol="AAPL", side="SELL", quantity=100.0,
            price=160.0, trade_date="2024-08-03", cost_basis_usd=15000.0,
            proceeds_usd=16000.0, holding_period_days=200, wash_sale_flag=False
        ))

        report = self.engine.audit_records()
        self.assertEqual(report.status, "AUDIT_COMPLIANT")
        self.assertEqual(report.complete_records, 2)
        self.assertEqual(report.incomplete_records, 0)
        self.assertEqual(report.short_term_trades, 2)

    def test_audit_issues_missing_cost_basis(self):
        self.engine.add_trade_record(TradeRecord(
            trade_id="T003", symbol="MSFT", side="BUY", quantity=50.0,
            price=300.0, trade_date="2023-06-01", cost_basis_usd=None,
            holding_period_days=400
        ))

        report = self.engine.audit_records()
        self.assertEqual(report.status, "AUDIT_ISSUES_FOUND")
        self.assertEqual(report.incomplete_records, 1)
        self.assertTrue(any(i.issue_type == "MISSING_FIELD" for i in report.issues))

if __name__ == '__main__':
    unittest.main()
