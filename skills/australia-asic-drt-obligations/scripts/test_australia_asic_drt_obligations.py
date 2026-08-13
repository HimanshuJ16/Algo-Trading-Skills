import unittest
from datetime import date

from australia_asic_drt_obligations import (
    AsicDrtReportingEngine,
    OtcDerivativeTrade,
)

# A structurally valid LEI (ISO 17442, MOD 97-10 checksum verified).
VALID_LEI = "5493006MHB84DD0ZWV18"
# Same length as a real LEI but with a failing MOD 97-10 checksum.
INVALID_LEI_BAD_CHECKSUM = "5493001KJTIIGC8YR607"
# 32 uppercase alphanumeric chars: valid ISO 23897 UTI length/charset.
VALID_UTI = "1234567890ABCDEF1234567890ABCDEF"
# 12 chars, "QZ" prefix, consonants only (no A/E/I/O/U/Y): valid ISO 4914 shape.
VALID_UPI = "QZBCDFGHJKLM"


class TestAsicDrtReportingEngine(unittest.TestCase):
    def setUp(self):
        self.engine = AsicDrtReportingEngine()
        self.reporting_date = date(2026, 7, 25)  # Saturday

    # ---- Identifier validation ----------------------------------------------

    def test_compliant_trade(self):
        # Trade Friday 2026-07-24 -> T+2 deadline Tue 2026-07-28.
        trade = OtcDerivativeTrade(
            internal_trade_id="TRD123",
            trade_date=date(2026, 7, 24),
            lei_counterparty=VALID_LEI,
            uti=VALID_UTI,
            upi=VALID_UPI,
        )
        record = self.engine.validate_report(trade, self.reporting_date)
        self.assertTrue(record.is_ready_for_reporting)
        self.assertFalse(record.is_late_submission)
        self.assertEqual(record.missing_fields, [])
        self.assertEqual(record.reporting_deadline, date(2026, 7, 28))

    def test_missing_upi_and_lei(self):
        trade = OtcDerivativeTrade(
            internal_trade_id="TRD124",
            trade_date=self.reporting_date,
            lei_counterparty="INVALID",  # too short
            uti=VALID_UTI,
            upi=None,
        )
        record = self.engine.validate_report(trade, self.reporting_date)
        self.assertFalse(record.is_ready_for_reporting)
        self.assertEqual(len(record.missing_fields), 2)
        self.assertTrue(any("LEI" in f for f in record.missing_fields))
        self.assertTrue(any("UPI" in f for f in record.missing_fields))

    def test_lei_checksum_rejected(self):
        # Length 20, uppercase alphanumeric, but MOD 97-10 checksum fails.
        trade = OtcDerivativeTrade(
            internal_trade_id="TRD_LEI_CK",
            trade_date=date(2026, 7, 24),
            lei_counterparty=INVALID_LEI_BAD_CHECKSUM,
            uti=VALID_UTI,
            upi=VALID_UPI,
        )
        record = self.engine.validate_report(trade, self.reporting_date)
        self.assertFalse(record.is_ready_for_reporting)
        self.assertTrue(any("LEI" in f for f in record.missing_fields))

    def test_lei_lowercase_rejected(self):
        trade = OtcDerivativeTrade(
            internal_trade_id="TRD_LEI_LC",
            trade_date=date(2026, 7, 24),
            lei_counterparty=VALID_LEI.lower(),
            uti=VALID_UTI,
            upi=VALID_UPI,
        )
        record = self.engine.validate_report(trade, self.reporting_date)
        self.assertFalse(record.is_ready_for_reporting)
        self.assertTrue(any("LEI" in f for f in record.missing_fields))

    def test_uti_length_boundaries(self):
        base = OtcDerivativeTrade(
            internal_trade_id="TRD_UTI",
            trade_date=date(2026, 7, 24),
            lei_counterparty=VALID_LEI,
            upi=VALID_UPI,
        )
        # 20 chars (minimum): valid.
        base.uti = "A" * 20
        self.assertTrue(self.engine.validate_report(base, self.reporting_date).is_ready_for_reporting)
        # 52 chars (maximum): valid.
        base.uti = "A" * 52
        self.assertTrue(self.engine.validate_report(base, self.reporting_date).is_ready_for_reporting)
        # 19 chars: invalid.
        base.uti = "A" * 19
        self.assertFalse(self.engine.validate_report(base, self.reporting_date).is_ready_for_reporting)
        # 53 chars: invalid.
        base.uti = "A" * 53
        self.assertFalse(self.engine.validate_report(base, self.reporting_date).is_ready_for_reporting)

    def test_uti_lowercase_rejected(self):
        trade = OtcDerivativeTrade(
            internal_trade_id="TRD_UTI_LC",
            trade_date=date(2026, 7, 24),
            lei_counterparty=VALID_LEI,
            uti="a" * 30,
            upi=VALID_UPI,
        )
        record = self.engine.validate_report(trade, self.reporting_date)
        self.assertFalse(record.is_ready_for_reporting)
        self.assertTrue(any("UTI" in f for f in record.missing_fields))

    def test_upi_prefix_and_alphabet(self):
        base = OtcDerivativeTrade(
            internal_trade_id="TRD_UPI",
            trade_date=date(2026, 7, 24),
            lei_counterparty=VALID_LEI,
            uti=VALID_UTI,
        )
        # Missing "QZ" prefix: invalid.
        base.upi = "ZZBCDFGHJKLM"
        self.assertFalse(self.engine.validate_report(base, self.reporting_date).is_ready_for_reporting)
        # Contains a vowel (E): invalid.
        base.upi = "QZBCDEFGHJKL"
        self.assertFalse(self.engine.validate_report(base, self.reporting_date).is_ready_for_reporting)
        # Wrong length: invalid.
        base.upi = "QZBCDFGHJK"
        self.assertFalse(self.engine.validate_report(base, self.reporting_date).is_ready_for_reporting)
        # Valid shape: valid.
        base.upi = VALID_UPI
        self.assertTrue(self.engine.validate_report(base, self.reporting_date).is_ready_for_reporting)

    # ---- Deadline / business-day calendar -----------------------------------

    def test_late_submission(self):
        # Monday 2026-07-20 -> T+2 deadline Wed 2026-07-22; reporting Sat 07-25.
        trade = OtcDerivativeTrade(
            internal_trade_id="TRD125",
            trade_date=date(2026, 7, 20),
            lei_counterparty=VALID_LEI,
            uti=VALID_UTI,
            upi=VALID_UPI,
        )
        record = self.engine.validate_report(trade, self.reporting_date)
        self.assertTrue(record.is_ready_for_reporting)
        self.assertTrue(record.is_late_submission)
        self.assertEqual(record.reporting_deadline, date(2026, 7, 22))

    def test_deadline_on_boundary_not_late(self):
        # Reporting exactly on the T+2 deadline is compliant.
        trade = OtcDerivativeTrade(
            internal_trade_id="TRD_BOUND",
            trade_date=date(2026, 7, 20),  # Mon -> deadline Wed 2026-07-22
            lei_counterparty=VALID_LEI,
            uti=VALID_UTI,
            upi=VALID_UPI,
        )
        record = self.engine.validate_report(trade, date(2026, 7, 22))
        self.assertFalse(record.is_late_submission)

    def test_deadline_one_day_after_is_late(self):
        trade = OtcDerivativeTrade(
            internal_trade_id="TRD_BOUND_LATE",
            trade_date=date(2026, 7, 20),
            lei_counterparty=VALID_LEI,
            uti=VALID_UTI,
            upi=VALID_UPI,
        )
        record = self.engine.validate_report(trade, date(2026, 7, 23))
        self.assertTrue(record.is_late_submission)

    def test_friday_trade_skips_weekend(self):
        # Friday 2026-07-24 -> T+2 deadline Tue 2026-07-28 (skips Sat/Sun).
        trade = OtcDerivativeTrade(
            internal_trade_id="TRD_FRI",
            trade_date=date(2026, 7, 24),
            lei_counterparty=VALID_LEI,
            uti=VALID_UTI,
            upi=VALID_UPI,
        )
        record = self.engine.validate_report(trade, date(2026, 7, 27))
        self.assertEqual(record.reporting_deadline, date(2026, 7, 28))
        self.assertFalse(record.is_late_submission)

    def test_t4_extension_for_linking_identifier(self):
        # Friday 2026-07-24 -> T+4 deadline Thu 2026-07-30 (skips weekend).
        trade = OtcDerivativeTrade(
            internal_trade_id="TRD_T4",
            trade_date=date(2026, 7, 24),
            lei_counterparty=VALID_LEI,
            uti=VALID_UTI,
            upi=VALID_UPI,
            requires_linking_identifier=True,
        )
        record = self.engine.validate_report(trade, date(2026, 7, 29))
        self.assertEqual(record.reporting_deadline, date(2026, 7, 30))
        self.assertFalse(record.is_late_submission)
        # Reporting one day past the T+4 deadline is late.
        record_late = self.engine.validate_report(trade, date(2026, 7, 31))
        self.assertTrue(record_late.is_late_submission)

    def test_holiday_skips_count_as_non_business(self):
        # Monday 2026-07-20 -> normally Wed 2026-07-22; a Tue holiday pushes
        # the deadline to Thu 2026-07-23.
        trade = OtcDerivativeTrade(
            internal_trade_id="TRD_HOL",
            trade_date=date(2026, 7, 20),
            lei_counterparty=VALID_LEI,
            uti=VALID_UTI,
            upi=VALID_UPI,
        )
        holidays = {date(2026, 7, 21)}
        record = self.engine.validate_report(trade, date(2026, 7, 22), holidays)
        self.assertEqual(record.reporting_deadline, date(2026, 7, 23))
        self.assertFalse(record.is_late_submission)

    # ---- Batch ---------------------------------------------------------------

    def test_batch_validate(self):
        trades = [
            OtcDerivativeTrade(
                internal_trade_id="OK",
                trade_date=date(2026, 7, 24),
                lei_counterparty=VALID_LEI,
                uti=VALID_UTI,
                upi=VALID_UPI,
            ),
            OtcDerivativeTrade(
                internal_trade_id="BAD",
                trade_date=date(2026, 7, 24),
                lei_counterparty=None,
                uti=VALID_UTI,
                upi=VALID_UPI,
            ),
        ]
        records = self.engine.batch_validate(trades, date(2026, 7, 25))
        self.assertEqual(len(records), 2)
        self.assertTrue(records[0].is_ready_for_reporting)
        self.assertFalse(records[1].is_ready_for_reporting)


if __name__ == "__main__":
    unittest.main()
