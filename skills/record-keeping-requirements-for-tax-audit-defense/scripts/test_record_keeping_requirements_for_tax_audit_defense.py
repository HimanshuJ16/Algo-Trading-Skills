import datetime
import unittest

from record_keeping_requirements_for_tax_audit_defense import (
    AccountingMethod,
    HoldingPeriod,
    IssueType,
    Record,
    RecordKeepingError,
    RecordKeepingRequirementsForTaxAuditDefenseEngine,
    Severity,
    TaxAuditComplianceReport,
    TradeRecord,
    add_business_days,
    classify_holding_period,
    one_year_anniversary,
    parse_date,
)

# Fixed evaluation date so every assertion is reproducible.
AS_OF = datetime.date(2026, 8, 27)


def issue_types(report):
    return [i.issue_type for i in report.issues]


class TestHoldingPeriodClassification(unittest.TestCase):
    """IRC Sec. 1222 / IRS Pub. 550: long-term requires *more than* one year."""

    def test_irs_publication_550_example_is_short_term(self):
        # Pub. 550: bought 2012-02-06, sold 2013-02-06 -> NOT more than 1 year.
        # 2012 is a leap year, so 366 calendar days elapsed; a naive ">365 days"
        # rule would wrongly report long-term here.
        acquired = datetime.date(2012, 2, 6)
        disposed = datetime.date(2013, 2, 6)
        self.assertEqual((disposed - acquired).days, 366)
        self.assertEqual(
            classify_holding_period(acquired, disposed), HoldingPeriod.SHORT_TERM
        )

    def test_one_day_past_anniversary_is_long_term(self):
        self.assertEqual(
            classify_holding_period(
                datetime.date(2012, 2, 6), datetime.date(2013, 2, 7)
            ),
            HoldingPeriod.LONG_TERM,
        )

    def test_exact_anniversary_non_leap_span_is_short_term(self):
        acquired = datetime.date(2021, 3, 1)
        disposed = datetime.date(2022, 3, 1)
        self.assertEqual((disposed - acquired).days, 365)
        self.assertEqual(
            classify_holding_period(acquired, disposed), HoldingPeriod.SHORT_TERM
        )

    def test_leap_day_acquisition_anniversary(self):
        # Holding period starts 2024-03-01, so a full year runs through
        # 2025-02-28 and 2025-03-01 is the first long-term day.
        self.assertEqual(
            one_year_anniversary(datetime.date(2024, 2, 29)), datetime.date(2025, 2, 28)
        )
        self.assertEqual(
            classify_holding_period(
                datetime.date(2024, 2, 29), datetime.date(2025, 2, 28)
            ),
            HoldingPeriod.SHORT_TERM,
        )
        self.assertEqual(
            classify_holding_period(
                datetime.date(2024, 2, 29), datetime.date(2025, 3, 1)
            ),
            HoldingPeriod.LONG_TERM,
        )

    def test_day_count_fallback_boundaries(self):
        self.assertEqual(
            classify_holding_period(None, None, 365), HoldingPeriod.SHORT_TERM
        )
        self.assertEqual(
            classify_holding_period(None, None, 367), HoldingPeriod.LONG_TERM
        )
        # 366 days is exactly one year across a leap span but more than one year
        # otherwise, so the day count alone cannot decide.
        self.assertEqual(
            classify_holding_period(None, None, 366), HoldingPeriod.AMBIGUOUS
        )

    def test_dates_take_precedence_over_day_count(self):
        # Contradictory day count must not override the authoritative dates.
        self.assertEqual(
            classify_holding_period(
                datetime.date(2012, 2, 6), datetime.date(2013, 2, 6), 400
            ),
            HoldingPeriod.SHORT_TERM,
        )

    def test_unknown_without_any_input(self):
        self.assertEqual(classify_holding_period(None, None, None), HoldingPeriod.UNKNOWN)


class TestDateHelpers(unittest.TestCase):
    def test_parse_date_accepts_iso_string_and_date(self):
        self.assertEqual(parse_date("2024-01-15"), datetime.date(2024, 1, 15))
        self.assertEqual(parse_date(datetime.date(2024, 1, 15)), datetime.date(2024, 1, 15))
        self.assertEqual(
            parse_date(datetime.datetime(2024, 1, 15, 9, 30)), datetime.date(2024, 1, 15)
        )

    def test_parse_date_returns_none_for_malformed_input(self):
        for bad in ("not-a-date", "2024-13-01", "", "15/01/2024"):
            self.assertIsNone(parse_date(bad), bad)

    def test_add_business_days_skips_weekend(self):
        # 2026-08-28 is a Friday; T+1 lands on Monday 2026-08-31.
        friday = datetime.date(2026, 8, 28)
        self.assertEqual(friday.weekday(), 4)
        self.assertEqual(add_business_days(friday, 1), datetime.date(2026, 8, 31))


class TestEngineConfiguration(unittest.TestCase):
    def test_rejects_non_positive_retention(self):
        with self.assertRaises(RecordKeepingError):
            RecordKeepingRequirementsForTaxAuditDefenseEngine(retention_years=0)

    def test_rejects_empty_mandatory_field_list(self):
        with self.assertRaises(RecordKeepingError):
            RecordKeepingRequirementsForTaxAuditDefenseEngine(mandatory_fields=[])

    def test_custom_mandatory_fields_are_enforced(self):
        engine = RecordKeepingRequirementsForTaxAuditDefenseEngine(
            mandatory_fields=["trade_id", "proceeds_usd"]
        )
        engine.add_trade_record(TradeRecord(
            trade_id="T1", symbol="AAPL", side="SELL", quantity=10.0, price=100.0,
            trade_date="2024-01-15", wash_sale_flag=False, proceeds_usd=None,
        ))
        report = engine.audit_records(as_of=AS_OF)
        missing = [i for i in report.issues if i.issue_type == IssueType.MISSING_FIELD]
        self.assertEqual(len(missing), 1)
        self.assertIn("proceeds_usd", missing[0].detail)


class TestRecordKeepingRequirementsForTaxAuditDefenseEngine(unittest.TestCase):

    def setUp(self):
        self.engine = RecordKeepingRequirementsForTaxAuditDefenseEngine()

    # -- legacy API ------------------------------------------------------- #

    def test_legacy_initialization(self):
        self.assertEqual(len(self.engine.records), 0)

    def test_legacy_add_record(self):
        self.engine.add_record(Record("1", 10.0))
        self.assertEqual(len(self.engine.records), 1)

    def test_legacy_process(self):
        self.engine.add_record(Record("1", 10.0))
        self.engine.add_record(Record("2", 20.0))
        self.assertEqual(self.engine.process(), 30.0)

    # -- completeness ----------------------------------------------------- #

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

        report = self.engine.audit_records(as_of=AS_OF)
        self.assertEqual(report.status, "AUDIT_COMPLIANT")
        self.assertEqual(report.complete_records, 2)
        self.assertEqual(report.incomplete_records, 0)
        self.assertEqual(report.short_term_trades, 2)
        self.assertEqual(report.issues, [])

    def test_audit_issues_missing_cost_basis(self):
        self.engine.add_trade_record(TradeRecord(
            trade_id="T003", symbol="MSFT", side="BUY", quantity=50.0,
            price=300.0, trade_date="2023-06-01", cost_basis_usd=None,
            holding_period_days=400
        ))

        report = self.engine.audit_records(as_of=AS_OF)
        self.assertEqual(report.status, "AUDIT_ISSUES_FOUND")
        self.assertEqual(report.incomplete_records, 1)
        self.assertTrue(any(i.issue_type == "MISSING_FIELD" for i in report.issues))

    def test_duplicate_trade_id_is_reported_once(self):
        for _ in range(2):
            self.engine.add_trade_record(TradeRecord(
                trade_id="DUP", symbol="AAPL", side="BUY", quantity=1.0, price=10.0,
                trade_date="2024-01-15", cost_basis_usd=10.0,
            ))
        report = self.engine.audit_records(as_of=AS_OF)
        dupes = [i for i in report.issues if i.issue_type == IssueType.DUPLICATE_TRADE_ID]
        self.assertEqual(len(dupes), 1)
        self.assertEqual(report.status, "AUDIT_ISSUES_FOUND")

    # -- structural validation -------------------------------------------- #

    def test_malformed_trade_date_is_reported_not_raised(self):
        self.engine.add_trade_record(TradeRecord(
            trade_id="T1", symbol="AAPL", side="BUY", quantity=1.0, price=10.0,
            trade_date="15/01/2024", cost_basis_usd=10.0,
        ))
        report = self.engine.audit_records(as_of=AS_OF)
        self.assertTrue(any(
            i.issue_type == IssueType.INVALID_FIELD and "trade_date" in i.detail
            for i in report.issues
        ))

    def test_non_string_side_does_not_crash(self):
        # Regression: side.upper() previously raised AttributeError on None.
        self.engine.add_trade_record(TradeRecord(
            trade_id="T1", symbol="AAPL", side=None, quantity=1.0, price=10.0,
            trade_date="2024-01-15", cost_basis_usd=10.0,
        ))
        report = self.engine.audit_records(as_of=AS_OF)
        self.assertTrue(any(
            i.issue_type == IssueType.INVALID_FIELD and "side" in i.detail
            for i in report.issues
        ))

    def test_non_positive_quantity_and_negative_price_flagged(self):
        self.engine.add_trade_record(TradeRecord(
            trade_id="T1", symbol="AAPL", side="BUY", quantity=0.0, price=-1.0,
            trade_date="2024-01-15", cost_basis_usd=10.0,
        ))
        report = self.engine.audit_records(as_of=AS_OF)
        details = " | ".join(
            i.detail for i in report.issues if i.issue_type == IssueType.INVALID_FIELD)
        self.assertIn("quantity", details)
        self.assertIn("price", details)

    def test_non_finite_quantity_is_flagged(self):
        # NaN silently passes every comparison, so it must be rejected explicitly.
        self.engine.add_trade_record(TradeRecord(
            trade_id="T1", symbol="AAPL", side="BUY", quantity=float("nan"),
            price=float("inf"), trade_date="2024-01-15", cost_basis_usd=10.0,
        ))
        report = self.engine.audit_records(as_of=AS_OF)
        details = " | ".join(
            i.detail for i in report.issues if i.issue_type == IssueType.INVALID_FIELD)
        self.assertIn("quantity", details)
        self.assertIn("price", details)

    def test_unknown_lot_method_flagged(self):
        self.engine.add_trade_record(TradeRecord(
            trade_id="T1", symbol="AAPL", side="BUY", quantity=1.0, price=10.0,
            trade_date="2024-01-15", cost_basis_usd=10.0, lot_method="HIFO_MAGIC",
        ))
        report = self.engine.audit_records(as_of=AS_OF)
        self.assertTrue(any(
            i.issue_type == IssueType.INVALID_FIELD and "lot_method" in i.detail
            for i in report.issues
        ))

    def test_disposal_before_acquisition_flagged(self):
        self.engine.add_trade_record(TradeRecord(
            trade_id="T1", symbol="AAPL", side="SELL", quantity=1.0, price=10.0,
            trade_date="2024-01-15", cost_basis_usd=10.0, wash_sale_flag=False,
            acquisition_date="2024-06-01", disposal_date="2024-01-15",
        ))
        report = self.engine.audit_records(as_of=AS_OF)
        self.assertTrue(any(
            "precedes acquisition_date" in i.detail for i in report.issues
        ))

    # -- wash sale -------------------------------------------------------- #

    def test_wash_sale_flag_required_once_window_has_closed(self):
        self.engine.add_trade_record(TradeRecord(
            trade_id="T1", symbol="AAPL", side="SELL", quantity=1.0, price=10.0,
            trade_date="2024-01-15", cost_basis_usd=10.0, wash_sale_flag=None,
        ))
        report = self.engine.audit_records(as_of=AS_OF)
        self.assertIn(IssueType.WASH_SALE_UNSET, issue_types(report))
        self.assertEqual(report.status, "AUDIT_ISSUES_FOUND")

    def test_open_wash_sale_window_is_advisory_not_defect(self):
        # Sale 10 days before the evaluation date: the Sec. 1091 window still has
        # 20 days to run, so no final determination can be expected yet.
        sale_date = AS_OF - datetime.timedelta(days=10)
        self.engine.add_trade_record(TradeRecord(
            trade_id="T1", symbol="AAPL", side="SELL", quantity=1.0, price=10.0,
            trade_date=sale_date, cost_basis_usd=10.0, wash_sale_flag=None,
        ))
        report = self.engine.audit_records(as_of=AS_OF)
        self.assertIn(IssueType.WASH_SALE_WINDOW_OPEN, issue_types(report))
        self.assertNotIn(IssueType.WASH_SALE_UNSET, issue_types(report))
        self.assertEqual(report.defect_count, 0)
        self.assertEqual(report.status, "AUDIT_ADVISORY_ONLY")

    def test_wash_sale_window_closes_on_day_31(self):
        # Boundary: the window covers the 30 days after the sale, so an audit run
        # exactly 30 days later still sits inside it.
        sale_date = AS_OF - datetime.timedelta(days=30)
        self.engine.add_trade_record(TradeRecord(
            trade_id="T1", symbol="AAPL", side="SELL", quantity=1.0, price=10.0,
            trade_date=sale_date, cost_basis_usd=10.0, wash_sale_flag=None,
        ))
        report = self.engine.audit_records(as_of=AS_OF)
        self.assertIn(IssueType.WASH_SALE_UNSET, issue_types(report))

    def test_buy_records_never_require_a_wash_sale_flag(self):
        self.engine.add_trade_record(TradeRecord(
            trade_id="T1", symbol="AAPL", side="BUY", quantity=1.0, price=10.0,
            trade_date="2024-01-15", cost_basis_usd=10.0, wash_sale_flag=None,
        ))
        report = self.engine.audit_records(as_of=AS_OF)
        self.assertNotIn(IssueType.WASH_SALE_UNSET, issue_types(report))

    # -- specific identification ------------------------------------------ #

    def test_specific_id_without_identification_date_is_unsubstantiated(self):
        self.engine.add_trade_record(TradeRecord(
            trade_id="T1", symbol="AAPL", side="SELL", quantity=1.0, price=10.0,
            trade_date="2024-01-15", cost_basis_usd=10.0, wash_sale_flag=False,
            lot_method="SPECIFIC_ID",
        ))
        report = self.engine.audit_records(as_of=AS_OF)
        self.assertIn(IssueType.LOT_ID_UNSUBSTANTIATED, issue_types(report))

    def test_specific_id_after_settlement_deadline_is_unsubstantiated(self):
        # 2024-01-15 is a Monday, so the T+1 deadline is Tuesday 2024-01-16.
        self.assertEqual(datetime.date(2024, 1, 15).weekday(), 0)
        self.engine.add_trade_record(TradeRecord(
            trade_id="T1", symbol="AAPL", side="SELL", quantity=1.0, price=10.0,
            trade_date="2024-01-15", cost_basis_usd=10.0, wash_sale_flag=False,
            lot_method="SPECIFIC_ID", lot_identification_date="2024-01-17",
        ))
        report = self.engine.audit_records(as_of=AS_OF)
        self.assertIn(IssueType.LOT_ID_UNSUBSTANTIATED, issue_types(report))

    def test_specific_id_identified_on_settlement_date_is_accepted(self):
        self.engine.add_trade_record(TradeRecord(
            trade_id="T1", symbol="AAPL", side="SELL", quantity=1.0, price=10.0,
            trade_date="2024-01-15", cost_basis_usd=10.0, wash_sale_flag=False,
            lot_method="SPECIFIC_ID", lot_identification_date="2024-01-16",
        ))
        report = self.engine.audit_records(as_of=AS_OF)
        self.assertNotIn(IssueType.LOT_ID_UNSUBSTANTIATED, issue_types(report))
        self.assertEqual(report.status, "AUDIT_COMPLIANT")

    def test_fifo_sell_needs_no_identification_evidence(self):
        self.engine.add_trade_record(TradeRecord(
            trade_id="T1", symbol="AAPL", side="SELL", quantity=1.0, price=10.0,
            trade_date="2024-01-15", cost_basis_usd=10.0, wash_sale_flag=False,
            lot_method="FIFO",
        ))
        report = self.engine.audit_records(as_of=AS_OF)
        self.assertNotIn(IssueType.LOT_ID_UNSUBSTANTIATED, issue_types(report))

    # -- Sec. 475(f) mark-to-market --------------------------------------- #

    def test_mtm_election_suppresses_wash_sale_and_holding_period(self):
        # IRS Topic 429: the wash sale rules and the capital short/long
        # distinction do not apply to a Sec. 475(f) mark-to-market trader.
        engine = RecordKeepingRequirementsForTaxAuditDefenseEngine(
            accounting_method=AccountingMethod.MTM_475F
        )
        engine.add_trade_record(TradeRecord(
            trade_id="T1", symbol="AAPL", side="SELL", quantity=1.0, price=10.0,
            trade_date="2024-01-15", cost_basis_usd=10.0, wash_sale_flag=None,
            holding_period_days=400,
        ))
        report = engine.audit_records(as_of=AS_OF)
        self.assertNotIn(IssueType.WASH_SALE_UNSET, issue_types(report))
        self.assertEqual(report.long_term_trades, 0)
        self.assertEqual(report.short_term_trades, 0)
        self.assertEqual(report.mtm_trades, 1)
        self.assertEqual(report.status, "AUDIT_COMPLIANT")

    def test_mtm_investment_security_requires_same_day_identification(self):
        engine = RecordKeepingRequirementsForTaxAuditDefenseEngine(
            accounting_method=AccountingMethod.MTM_475F
        )
        engine.add_trade_record(TradeRecord(
            trade_id="T1", symbol="AAPL", side="BUY", quantity=1.0, price=10.0,
            trade_date="2024-01-15", cost_basis_usd=10.0,
            held_for_investment=True, investment_identification_date=None,
        ))
        report = engine.audit_records(as_of=AS_OF)
        self.assertIn(IssueType.MTM_IDENTIFICATION_MISSING, issue_types(report))

    def test_mtm_investment_security_identified_late_is_flagged(self):
        engine = RecordKeepingRequirementsForTaxAuditDefenseEngine(
            accounting_method=AccountingMethod.MTM_475F
        )
        engine.add_trade_record(TradeRecord(
            trade_id="T1", symbol="AAPL", side="BUY", quantity=1.0, price=10.0,
            trade_date="2024-01-15", cost_basis_usd=10.0,
            held_for_investment=True, investment_identification_date="2024-01-16",
        ))
        report = engine.audit_records(as_of=AS_OF)
        self.assertIn(IssueType.MTM_IDENTIFICATION_MISSING, issue_types(report))

    def test_investment_security_stays_in_the_capital_account_under_mtm(self):
        # held_for_investment securities remain subject to Sec. 1091 even where
        # the trading business has a Sec. 475(f) election in force.
        engine = RecordKeepingRequirementsForTaxAuditDefenseEngine(
            accounting_method=AccountingMethod.MTM_475F
        )
        engine.add_trade_record(TradeRecord(
            trade_id="T1", symbol="AAPL", side="SELL", quantity=1.0, price=10.0,
            trade_date="2024-01-15", cost_basis_usd=10.0, wash_sale_flag=None,
            held_for_investment=True, investment_identification_date="2024-01-15",
        ))
        report = engine.audit_records(as_of=AS_OF)
        self.assertIn(IssueType.WASH_SALE_UNSET, issue_types(report))
        self.assertEqual(report.mtm_trades, 0)

    # -- retention -------------------------------------------------------- #

    def test_open_position_has_no_purge_date_and_is_not_purge_eligible(self):
        self.engine.add_trade_record(TradeRecord(
            trade_id="T1", symbol="AAPL", side="BUY", quantity=1.0, price=10.0,
            trade_date="2005-01-15", cost_basis_usd=10.0,
        ))
        report = self.engine.audit_records(as_of=AS_OF)
        assessment = report.retention[0]
        # 21 years old, but the lot is still open: the clock starts at disposal.
        self.assertIsNone(assessment.earliest_purge_date)
        self.assertFalse(assessment.purge_eligible)
        self.assertEqual(report.purge_eligible_records, 0)
        self.assertEqual(report.retention_indeterminate_records, 1)

    def test_purge_of_an_open_position_record_is_a_defect(self):
        self.engine.add_trade_record(TradeRecord(
            trade_id="T1", symbol="AAPL", side="BUY", quantity=1.0, price=10.0,
            trade_date="2005-01-15", cost_basis_usd=10.0, purge_pending=True,
        ))
        report = self.engine.audit_records(as_of=AS_OF)
        self.assertIn(IssueType.RETENTION_INDETERMINATE, issue_types(report))
        self.assertEqual(report.status, "AUDIT_ISSUES_FOUND")

    def test_retention_clock_runs_from_disposal_date(self):
        # Disposed 2018-08-27; a 7-year policy runs to 2025-08-27, which has
        # elapsed as of 2026-08-27.
        self.engine.add_trade_record(TradeRecord(
            trade_id="T1", symbol="AAPL", side="SELL", quantity=1.0, price=10.0,
            trade_date="2018-08-27", cost_basis_usd=10.0, wash_sale_flag=False,
        ))
        report = self.engine.audit_records(as_of=AS_OF)
        assessment = report.retention[0]
        self.assertEqual(assessment.earliest_purge_date, datetime.date(2025, 8, 27))
        self.assertTrue(assessment.purge_eligible)
        self.assertEqual(report.purge_eligible_records, 1)

    def test_purge_before_retention_elapses_is_flagged_at_risk(self):
        self.engine.add_trade_record(TradeRecord(
            trade_id="T1", symbol="AAPL", side="SELL", quantity=1.0, price=10.0,
            trade_date="2023-01-15", cost_basis_usd=10.0, wash_sale_flag=False,
            purge_pending=True,
        ))
        report = self.engine.audit_records(as_of=AS_OF)
        self.assertIn(IssueType.RETENTION_AT_RISK, issue_types(report))
        self.assertEqual(report.status, "AUDIT_ISSUES_FOUND")

    def test_retention_boundary_is_inclusive_on_the_purge_date(self):
        self.engine.add_trade_record(TradeRecord(
            trade_id="T1", symbol="AAPL", side="SELL", quantity=1.0, price=10.0,
            trade_date="2019-08-27", cost_basis_usd=10.0, wash_sale_flag=False,
            purge_pending=True,
        ))
        # Exactly seven years after disposal -> eligible, no issue raised.
        report = self.engine.audit_records(as_of=datetime.date(2026, 8, 27))
        self.assertTrue(report.retention[0].purge_eligible)
        self.assertNotIn(IssueType.RETENTION_AT_RISK, issue_types(report))
        # One day earlier -> not yet eligible.
        report = self.engine.audit_records(as_of=datetime.date(2026, 8, 26))
        self.assertFalse(report.retention[0].purge_eligible)
        self.assertIn(IssueType.RETENTION_AT_RISK, issue_types(report))

    def test_legal_hold_blocks_purge_regardless_of_age(self):
        self.engine.add_trade_record(TradeRecord(
            trade_id="T1", symbol="AAPL", side="SELL", quantity=1.0, price=10.0,
            trade_date="2005-01-15", cost_basis_usd=10.0, wash_sale_flag=False,
            legal_hold=True, purge_pending=True,
        ))
        report = self.engine.audit_records(as_of=AS_OF)
        assessment = report.retention[0]
        self.assertFalse(assessment.purge_eligible)
        self.assertIn("hold", assessment.rationale)
        self.assertEqual(report.status, "AUDIT_ISSUES_FOUND")

    def test_custom_retention_period_is_honoured(self):
        engine = RecordKeepingRequirementsForTaxAuditDefenseEngine(retention_years=3)
        engine.add_trade_record(TradeRecord(
            trade_id="T1", symbol="AAPL", side="SELL", quantity=1.0, price=10.0,
            trade_date="2022-08-27", cost_basis_usd=10.0, wash_sale_flag=False,
        ))
        report = engine.audit_records(as_of=AS_OF)
        self.assertEqual(
            report.retention[0].earliest_purge_date, datetime.date(2025, 8, 27)
        )

    def test_leap_day_disposal_retention_date(self):
        engine = RecordKeepingRequirementsForTaxAuditDefenseEngine(retention_years=7)
        engine.add_trade_record(TradeRecord(
            trade_id="T1", symbol="AAPL", side="SELL", quantity=1.0, price=10.0,
            trade_date="2024-02-29", cost_basis_usd=10.0, wash_sale_flag=False,
        ))
        report = engine.audit_records(as_of=AS_OF)
        # 2031 is not a leap year; the date falls back to 28 February.
        self.assertEqual(
            report.retention[0].earliest_purge_date, datetime.date(2031, 2, 28)
        )

    # -- report shape ----------------------------------------------------- #

    def test_empty_record_set_is_compliant_and_deterministic(self):
        report = self.engine.audit_records(as_of=AS_OF)
        self.assertIsInstance(report, TaxAuditComplianceReport)
        self.assertEqual(report.total_records, 0)
        self.assertEqual(report.status, "AUDIT_COMPLIANT")
        self.assertEqual(report.as_of, AS_OF)

    def test_audit_is_reproducible_for_a_fixed_as_of(self):
        self.engine.add_trade_record(TradeRecord(
            trade_id="T1", symbol="AAPL", side="SELL", quantity=1.0, price=10.0,
            trade_date="2024-01-15", cost_basis_usd=10.0, wash_sale_flag=False,
        ))
        first = self.engine.audit_records(as_of=AS_OF)
        second = self.engine.audit_records(as_of=AS_OF)
        self.assertEqual(first.audit_notes, second.audit_notes)

    def test_to_dict_exports_every_field(self):
        trade = TradeRecord(
            trade_id="T1", symbol="AAPL", side="SELL", quantity=1.0, price=10.0,
            trade_date=datetime.date(2024, 1, 15), cost_basis_usd=10.0,
            proceeds_usd=12.0, wash_sale_flag=True, acquisition_date="2023-01-15",
        )
        exported = trade.to_dict()
        self.assertEqual(exported["trade_date"], "2024-01-15")
        self.assertEqual(exported["acquisition_date"], "2023-01-15")
        self.assertEqual(exported["proceeds_usd"], 12.0)
        self.assertTrue(exported["wash_sale_flag"])

    def test_issue_types_compare_equal_to_plain_strings(self):
        # Backward compatibility for callers matching on raw strings.
        self.assertEqual(IssueType.MISSING_FIELD, "MISSING_FIELD")
        self.assertEqual(Severity.DEFECT, "DEFECT")

    def test_issue_types_render_as_their_bare_value(self):
        # A compliance report built by interpolation must not emit
        # "IssueType.MISSING_FIELD".
        self.assertEqual(f"{IssueType.MISSING_FIELD}", "MISSING_FIELD")
        self.assertEqual(str(Severity.ADVISORY), "ADVISORY")
        self.assertEqual(f"{AccountingMethod.MTM_475F:>10}", "  MTM_475F")


if __name__ == '__main__':
    unittest.main()
