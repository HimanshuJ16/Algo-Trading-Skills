import unittest
from datetime import date
from australia_asic_drt_obligations import AsicDrtReportingEngine, OtcDerivativeTrade

class TestAsicDrtReportingEngine(unittest.TestCase):
    def setUp(self):
        self.engine = AsicDrtReportingEngine()
        self.today = date(2026, 7, 25)

    def test_compliant_trade(self):
        trade = OtcDerivativeTrade(
            internal_trade_id="TRD123",
            trade_date=date(2026, 7, 24), # T+1, within deadline
            lei_counterparty="5493006MHB84DD0ZWV18", # 20 chars
            uti="1234567890ABCDEF1234567890ABCDEF",
            upi="QWERTYUIOPAS" # 12 chars
        )
        record = self.engine.validate_report(trade, self.today)
        self.assertTrue(record.is_ready_for_reporting)
        self.assertFalse(record.is_late_submission)
        self.assertEqual(len(record.missing_fields), 0)

    def test_missing_upi_and_lei(self):
        trade = OtcDerivativeTrade(
            internal_trade_id="TRD124",
            trade_date=self.today,
            lei_counterparty="INVALID", # Too short
            uti="1234567890ABCDEF1234567890ABCDEF",
            upi=None
        )
        record = self.engine.validate_report(trade, self.today)
        self.assertFalse(record.is_ready_for_reporting)
        self.assertEqual(len(record.missing_fields), 2)
        self.assertTrue(any("LEI" in f for f in record.missing_fields))
        self.assertTrue(any("UPI" in f for f in record.missing_fields))

    def test_late_submission(self):
        trade = OtcDerivativeTrade(
            internal_trade_id="TRD125",
            trade_date=date(2026, 7, 20), # T+5, clearly late
            lei_counterparty="5493006MHB84DD0ZWV18",
            uti="1234567890ABCDEF1234567890ABCDEF",
            upi="QWERTYUIOPAS"
        )
        record = self.engine.validate_report(trade, self.today)
        # It has all fields, so it's ready to report, but it will be flagged as late.
        self.assertTrue(record.is_ready_for_reporting)
        self.assertTrue(record.is_late_submission)

if __name__ == '__main__':
    unittest.main()
