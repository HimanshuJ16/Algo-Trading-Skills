import unittest
from datetime import date, datetime

from australia_asic_drt_obligations import (
    AsicDrtReportingEngine,
    DrtComplianceRecord,
    OtcDerivativeTrade,
    _add_business_days,
)

# A structurally valid LEI (ISO 17442, MOD 97-10 checksum verified).
VALID_LEI = "5493006MHB84DD0ZWV18"
# Same length as a real LEI but with a failing MOD 97-10 checksum.
INVALID_LEI_BAD_CHECKSUM = "5493001KJTIIGC8YR607"
# Passes MOD 97-10 but positions 19-20 are letters, not the 2 numeric check
# digits ISO 17442 requires.
INVALID_LEI_ALPHA_CHECK_DIGITS = "5493006MHB84DD0ZWVAM"
# 32 uppercase alphanumeric chars: valid ISO 23897 UTI length/charset.
VALID_UTI = "1234567890ABCDEF1234567890ABCDEF"
# 12 chars, "QZ" prefix, consonants only (no A/E/I/O/U/Y): valid ISO 4914 shape.
VALID_UPI = "QZBCDFGHJKLM"

# Trade dates used throughout: 2026-07-20 is a Monday, 2026-07-24 a Friday.
MONDAY = date(2026, 7, 20)
FRIDAY = date(2026, 7, 24)


def _compliant_trade(**overrides) -> OtcDerivativeTrade:
    """Build a trade that passes every identifier check, then apply overrides."""
    fields = dict(
        internal_trade_id="TRD_BASE",
        trade_date=FRIDAY,
        counterparty_identifier=VALID_LEI,
        uti=VALID_UTI,
        upi=VALID_UPI,
    )
    fields.update(overrides)
    return OtcDerivativeTrade(**fields)


class TestIdentifierValidation(unittest.TestCase):
    def setUp(self):
        self.engine = AsicDrtReportingEngine()
        self.reporting_date = date(2026, 7, 25)  # Saturday

    def _validate(self, trade, reporting_date=None) -> DrtComplianceRecord:
        return self.engine.validate_report(
            trade, reporting_date or self.reporting_date
        )

    def test_compliant_trade(self):
        # Trade Friday 2026-07-24 -> T+2 deadline Tue 2026-07-28.
        record = self._validate(_compliant_trade(internal_trade_id="TRD123"))
        self.assertTrue(record.is_ready_for_reporting)
        self.assertFalse(record.is_late_submission)
        self.assertEqual(record.missing_fields, [])
        self.assertEqual(record.reporting_deadline, date(2026, 7, 28))
        self.assertFalse(record.repository_outage_relief_may_apply)

    def test_missing_upi_and_counterparty_identifier(self):
        record = self._validate(
            _compliant_trade(
                internal_trade_id="TRD124",
                trade_date=self.reporting_date,
                counterparty_identifier="INVALID",  # too short
                upi=None,
            )
        )
        self.assertFalse(record.is_ready_for_reporting)
        self.assertEqual(len(record.missing_fields), 2)
        self.assertTrue(any("Item 7" in f for f in record.missing_fields))
        self.assertTrue(any("Item 2" in f for f in record.missing_fields))

    def test_lei_checksum_rejected(self):
        # Length 20, uppercase alphanumeric, but MOD 97-10 checksum fails.
        record = self._validate(
            _compliant_trade(counterparty_identifier=INVALID_LEI_BAD_CHECKSUM)
        )
        self.assertFalse(record.is_ready_for_reporting)
        self.assertTrue(any("Item 7" in f for f in record.missing_fields))

    def test_lei_with_alphabetic_check_digits_rejected(self):
        # Regression: ISO 17442 requires the last 2 characters to be numeric
        # check digits. This value satisfies MOD 97-10 but ends in letters.
        self.assertTrue(
            self._is_mod_97_compliant(INVALID_LEI_ALPHA_CHECK_DIGITS),
            "fixture must pass MOD 97-10 so the test isolates the check-digit rule",
        )
        record = self._validate(
            _compliant_trade(counterparty_identifier=INVALID_LEI_ALPHA_CHECK_DIGITS)
        )
        self.assertFalse(record.is_ready_for_reporting)
        self.assertTrue(any("Item 7" in f for f in record.missing_fields))

    @staticmethod
    def _is_mod_97_compliant(value: str) -> bool:
        return int("".join(str(int(ch, 36)) for ch in value)) % 97 == 1

    def test_lei_lowercase_rejected(self):
        record = self._validate(
            _compliant_trade(counterparty_identifier=VALID_LEI.lower())
        )
        self.assertFalse(record.is_ready_for_reporting)
        self.assertTrue(any("Item 7" in f for f in record.missing_fields))

    def test_non_lei_counterparty_identifier_accepted(self):
        # Table S1.1(1) Items 7-8: a natural person not eligible for an LEI is
        # reported with a Client Code, and an anonymous CCP-cleared trade with
        # the anonymity identifier ANON. Neither is an LEI.
        for identifier in ("CLIENT0001", "ANON"):
            with self.subTest(identifier=identifier):
                record = self._validate(
                    _compliant_trade(
                        counterparty_identifier=identifier,
                        counterparty_identifier_is_lei=False,
                    )
                )
                self.assertTrue(record.is_ready_for_reporting, record.missing_fields)

    def test_non_lei_counterparty_identifier_length_boundary(self):
        # Item 7: "an alphanumeric code of not more than 72 characters".
        for length, expected in ((72, True), (73, False)):
            with self.subTest(length=length):
                record = self._validate(
                    _compliant_trade(
                        counterparty_identifier="A" * length,
                        counterparty_identifier_is_lei=False,
                    )
                )
                self.assertEqual(record.is_ready_for_reporting, expected)

    def test_non_lei_counterparty_identifier_rejects_non_ascii(self):
        # "١" is ARABIC-INDIC DIGIT ONE: str.isalnum() accepts it, the
        # Rules' ASCII alphanumeric format does not.
        record = self._validate(
            _compliant_trade(
                counterparty_identifier="CLIENT١",
                counterparty_identifier_is_lei=False,
            )
        )
        self.assertFalse(record.is_ready_for_reporting)

    def test_empty_non_lei_counterparty_identifier_rejected(self):
        record = self._validate(
            _compliant_trade(
                counterparty_identifier=None,
                counterparty_identifier_is_lei=False,
            )
        )
        self.assertFalse(record.is_ready_for_reporting)

    def test_uti_length_boundaries(self):
        for length, expected in ((20, True), (52, True), (19, False), (53, False)):
            with self.subTest(length=length):
                record = self._validate(_compliant_trade(uti="A" * length))
                self.assertEqual(record.is_ready_for_reporting, expected)

    def test_uti_lowercase_rejected(self):
        record = self._validate(_compliant_trade(uti="a" * 30))
        self.assertFalse(record.is_ready_for_reporting)
        self.assertTrue(any("Item 1" in f for f in record.missing_fields))

    def test_upi_prefix_and_alphabet(self):
        cases = (
            ("ZZBCDFGHJKLM", False),  # missing the fixed "QZ" prefix
            ("QZBCDEFGHJKL", False),  # contains the vowel E
            ("QZBCDFGHJKLY", False),  # contains Y, excluded by ISO 4914
            ("QZBCDFGHJK", False),    # wrong length
            (VALID_UPI, True),
        )
        for upi, expected in cases:
            with self.subTest(upi=upi):
                record = self._validate(_compliant_trade(upi=upi))
                self.assertEqual(record.is_ready_for_reporting, expected)

    def test_upi_not_required_for_termination_report(self):
        # Table S1.1(1) Item 2: "This data element is not required in a report
        # about the termination of an OTC Derivative."
        record = self._validate(
            _compliant_trade(upi=None, is_termination_report=True)
        )
        self.assertTrue(record.is_ready_for_reporting, record.missing_fields)

    def test_invalid_upi_still_rejected_on_termination_report(self):
        record = self._validate(
            _compliant_trade(upi="QZBAD", is_termination_report=True)
        )
        self.assertFalse(record.is_ready_for_reporting)
        self.assertTrue(any("Item 2" in f for f in record.missing_fields))

    def test_package_identifier_required_but_missing(self):
        record = self._validate(
            _compliant_trade(requires_package_identifier=True)
        )
        self.assertFalse(record.is_ready_for_reporting)
        self.assertTrue(any("Item 92" in f for f in record.missing_fields))

    def test_package_identifier_length_boundary(self):
        # Item 92: "An alphanumeric code of not more than 100 characters".
        for length, expected in ((100, True), (101, False)):
            with self.subTest(length=length):
                record = self._validate(
                    _compliant_trade(
                        requires_package_identifier=True,
                        package_identifier="P" * length,
                    )
                )
                self.assertEqual(record.is_ready_for_reporting, expected)

    def test_package_identifier_validated_even_when_not_required(self):
        record = self._validate(_compliant_trade(package_identifier="P" * 101))
        self.assertFalse(record.is_ready_for_reporting)
        self.assertTrue(any("Item 92" in f for f in record.missing_fields))


class TestReportingDeadline(unittest.TestCase):
    def setUp(self):
        self.engine = AsicDrtReportingEngine()

    def test_late_submission(self):
        # Monday 2026-07-20 -> T+2 deadline Wed 2026-07-22; reporting Sat 07-25.
        record = self.engine.validate_report(
            _compliant_trade(internal_trade_id="TRD125", trade_date=MONDAY),
            date(2026, 7, 25),
        )
        self.assertTrue(record.is_ready_for_reporting)
        self.assertTrue(record.is_late_submission)
        self.assertEqual(record.reporting_deadline, date(2026, 7, 22))

    def test_deadline_on_boundary_not_late(self):
        # Reporting exactly on the T+2 deadline is compliant.
        record = self.engine.validate_report(
            _compliant_trade(trade_date=MONDAY), date(2026, 7, 22)
        )
        self.assertFalse(record.is_late_submission)

    def test_deadline_one_day_after_is_late(self):
        record = self.engine.validate_report(
            _compliant_trade(trade_date=MONDAY), date(2026, 7, 23)
        )
        self.assertTrue(record.is_late_submission)

    def test_friday_trade_skips_weekend(self):
        # Friday 2026-07-24 -> T+2 deadline Tue 2026-07-28 (skips Sat/Sun).
        record = self.engine.validate_report(
            _compliant_trade(trade_date=FRIDAY), date(2026, 7, 27)
        )
        self.assertEqual(record.reporting_deadline, date(2026, 7, 28))
        self.assertFalse(record.is_late_submission)

    def test_t4_extension_for_package_identifier(self):
        # Rule 2.2.3(3): Friday 2026-07-24 -> T+4 deadline Thu 2026-07-30.
        trade = _compliant_trade(
            internal_trade_id="TRD_T4",
            trade_date=FRIDAY,
            requires_package_identifier=True,
            package_identifier="PKG001",
        )
        record = self.engine.validate_report(trade, date(2026, 7, 29))
        self.assertEqual(record.reporting_deadline, date(2026, 7, 30))
        self.assertFalse(record.is_late_submission)
        record_late = self.engine.validate_report(trade, date(2026, 7, 31))
        self.assertTrue(record_late.is_late_submission)

    def test_fx_swap_leg_requiring_package_identifier_keeps_t2(self):
        # Rule 2.2.3(3) excludes "a foreign exchange contract that is part of a
        # foreign exchange swap derivative transaction" from the T+4 extension,
        # even though Item 92(c) requires a package identifier for exactly that
        # case. Friday 2026-07-24 -> T+2 deadline Tue 2026-07-28.
        trade = _compliant_trade(
            internal_trade_id="TRD_FXSWAP",
            trade_date=FRIDAY,
            requires_package_identifier=True,
            package_identifier="PKG002",
            is_fx_swap_leg=True,
        )
        record = self.engine.validate_report(trade, date(2026, 7, 29))
        self.assertEqual(record.reporting_deadline, date(2026, 7, 28))
        self.assertTrue(record.is_late_submission)

    def test_holiday_skips_count_as_non_business(self):
        # Monday 2026-07-20 -> normally Wed 2026-07-22; a Tue holiday in the
        # Relevant Jurisdiction pushes the deadline to Thu 2026-07-23.
        record = self.engine.validate_report(
            _compliant_trade(trade_date=MONDAY),
            date(2026, 7, 22),
            {date(2026, 7, 21)},
        )
        self.assertEqual(record.reporting_deadline, date(2026, 7, 23))
        self.assertFalse(record.is_late_submission)

    def test_weekend_trade_date_starts_clock_on_next_business_day(self):
        # Saturday 2026-07-25 -> business days Mon 27 and Tue 28.
        record = self.engine.validate_report(
            _compliant_trade(trade_date=date(2026, 7, 25)), date(2026, 7, 28)
        )
        self.assertEqual(record.reporting_deadline, date(2026, 7, 28))
        self.assertFalse(record.is_late_submission)

    def test_repository_outage_relief_flagged_when_late(self):
        # Rule 2.2.3(2): repository unavailable at the deadline -> report as
        # soon as practicable; lateness is not an automatic breach.
        record = self.engine.validate_report(
            _compliant_trade(trade_date=MONDAY),
            date(2026, 7, 23),
            repository_unavailable_at_deadline=True,
        )
        self.assertTrue(record.is_late_submission)
        self.assertTrue(record.repository_outage_relief_may_apply)

    def test_repository_outage_relief_not_flagged_when_on_time(self):
        record = self.engine.validate_report(
            _compliant_trade(trade_date=MONDAY),
            date(2026, 7, 22),
            repository_unavailable_at_deadline=True,
        )
        self.assertFalse(record.is_late_submission)
        self.assertFalse(record.repository_outage_relief_may_apply)

    def test_add_business_days_rejects_negative_offset(self):
        with self.assertRaises(ValueError):
            _add_business_days(MONDAY, -1)


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.engine = AsicDrtReportingEngine()

    def test_reporting_date_before_trade_date_raises(self):
        with self.assertRaises(ValueError):
            self.engine.validate_report(
                _compliant_trade(trade_date=FRIDAY), date(2026, 7, 23)
            )

    def test_datetime_reporting_date_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.validate_report(
                _compliant_trade(trade_date=FRIDAY), datetime(2026, 7, 28, 9, 0)
            )

    def test_datetime_trade_date_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.validate_report(
                _compliant_trade(trade_date=datetime(2026, 7, 24, 9, 0)),
                date(2026, 7, 28),
            )

    def test_non_date_trade_date_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.validate_report(
                _compliant_trade(trade_date="2026-07-24"), date(2026, 7, 28)
            )

    def test_non_string_identifier_rejected(self):
        # A numeric Client Code or a Decimal reaching the gate is a boundary
        # conversion bug; coercing it would validate a different value from
        # the one that gets serialised into the report.
        for field in ("counterparty_identifier", "uti", "upi", "package_identifier"):
            with self.subTest(field=field):
                with self.assertRaises(TypeError):
                    self.engine.validate_report(
                        _compliant_trade(**{field: 1234567890}), date(2026, 7, 28)
                    )

    def test_datetime_in_holidays_rejected(self):
        # A datetime never compares equal to the date being tested, so it
        # would be silently ignored, overstating the deadline and suppressing
        # a genuine late flag.
        with self.assertRaises(TypeError):
            self.engine.validate_report(
                _compliant_trade(trade_date=MONDAY),
                date(2026, 7, 22),
                {datetime(2026, 7, 21)},
            )

    def test_non_date_in_holidays_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.validate_report(
                _compliant_trade(trade_date=MONDAY),
                date(2026, 7, 22),
                {"2026-07-21"},
            )

    def test_identifiers_are_whitespace_stripped(self):
        record = self.engine.validate_report(
            _compliant_trade(
                counterparty_identifier=f"  {VALID_LEI} ",
                uti=f" {VALID_UTI}  ",
                upi=f" {VALID_UPI} ",
            ),
            date(2026, 7, 28),
        )
        self.assertTrue(record.is_ready_for_reporting, record.missing_fields)


class TestBatchValidation(unittest.TestCase):
    def setUp(self):
        self.engine = AsicDrtReportingEngine()

    def test_batch_validate(self):
        trades = [
            _compliant_trade(internal_trade_id="OK"),
            _compliant_trade(internal_trade_id="BAD", counterparty_identifier=None),
        ]
        records = self.engine.batch_validate(trades, date(2026, 7, 25))
        self.assertEqual(len(records), 2)
        self.assertTrue(records[0].is_ready_for_reporting)
        self.assertFalse(records[1].is_ready_for_reporting)

    def test_batch_validate_propagates_holidays_and_outage_flag(self):
        trades = [_compliant_trade(internal_trade_id="HOL", trade_date=MONDAY)]
        records = self.engine.batch_validate(
            trades,
            date(2026, 7, 24),
            {date(2026, 7, 21)},
            repository_unavailable_at_deadline=True,
        )
        self.assertEqual(records[0].reporting_deadline, date(2026, 7, 23))
        self.assertTrue(records[0].is_late_submission)
        self.assertTrue(records[0].repository_outage_relief_may_apply)


if __name__ == "__main__":
    unittest.main()
