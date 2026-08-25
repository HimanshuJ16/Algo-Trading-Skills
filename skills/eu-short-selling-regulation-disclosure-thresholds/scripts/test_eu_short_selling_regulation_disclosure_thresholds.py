"""Unit tests for the EU SSR net short position disclosure engine.

Expected percentages are derived by hand from the share counts (e.g.
499,990 / 100,000,000 = 0.49999%), not by re-running the engine's own
arithmetic. Several tests are explicit regressions against defects fixed in
v2.0.0 and say so in the test name or a comment.
"""

import logging
import unittest
from datetime import date, datetime, timedelta, timezone

from eu_short_selling_regulation_disclosure_thresholds import (
    ACTION_NONE,
    ACTION_NOTIFY_NCA,
    ACTION_NOTIFY_NCA_AND_DISCLOSE_PUBLICLY,
    ART12_BREACH,
    ART12_COVERED,
    ART12_NOT_APPLICABLE,
    COVER_AGREEMENT_TO_BORROW,
    COVER_BORROWED,
    COVER_LOCATE_ARRANGEMENT,
    COVER_NONE,
    DEADLINE_NO_ACTION,
    DEADLINE_NO_CALENDAR,
    DEADLINE_NO_POSITION_DATE,
    DEADLINE_NO_TIMEZONE,
    DEADLINE_OK,
    INSTRUMENT_DEPOSITARY_RECEIPT,
    INSTRUMENT_DERIVATIVE,
    INSTRUMENT_ETF,
    INSTRUMENT_SHARE,
    STATUS_BELOW_THRESHOLDS,
    STATUS_OUT_OF_SCOPE,
    STATUS_PRIVATE_NCA,
    STATUS_PUBLIC_DISCLOSURE,
    EquityShortPositionState,
    EuShortSellingRegulationEngine,
    ShortSaleOrderIntent,
    next_weekday_excluding_holidays,
)

ISSUED = 100_000_000
POSITION_DATE = date(2026, 8, 24)  # a Monday


def _tzdata_available() -> bool:
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo("Europe/Berlin")
        return True
    except Exception:  # pragma: no cover - environment-dependent
        return False


HAS_TZDATA = _tzdata_available()


class SsrTestBase(unittest.TestCase):
    def setUp(self):
        self.engine = EuShortSellingRegulationEngine(
            next_trading_day=next_weekday_excluding_holidays
        )
        # Keep CRITICAL/WARNING log lines out of the test output.
        logging.getLogger(
            "eu_short_selling_regulation_disclosure_thresholds"
        ).setLevel(logging.CRITICAL + 1)

    def position(self, **overrides) -> EquityShortPositionState:
        base = dict(
            isin="DE0007100000",
            symbol="MBG",
            issued_share_capital_qty=ISSUED,
            long_shares_qty=0,
            short_shares_qty=0,
            has_valid_locate_agreement=True,
            position_date=POSITION_DATE,
            relevant_competent_authority="DE-BaFin",
            nca_timezone="Europe/Berlin",
        )
        base.update(overrides)
        return EquityShortPositionState(**base)

    def evaluate(self, **overrides):
        return self.engine.evaluate_short_position_disclosure(self.position(**overrides))


class TestThresholdClassification(SsrTestBase):
    def test_public_disclosure_at_0_60_percent(self):
        # 600,000 / 100,000,000 = 0.60%
        report = self.evaluate(short_shares_qty=600_000)
        self.assertEqual(report.net_short_percentage, 0.60)
        self.assertEqual(report.reporting_status, STATUS_PUBLIC_DISCLOSURE)
        self.assertEqual(
            report.disclosure_action, ACTION_NOTIFY_NCA_AND_DISCLOSE_PUBLICLY
        )
        self.assertEqual(report.current_threshold_pct, 0.60)

    def test_private_nca_notification_at_0_20_percent(self):
        # 200,000 / 100,000,000 = 0.20%
        report = self.evaluate(short_shares_qty=200_000)
        self.assertEqual(report.net_short_percentage, 0.20)
        self.assertEqual(report.reporting_status, STATUS_PRIVATE_NCA)
        self.assertEqual(report.disclosure_action, ACTION_NOTIFY_NCA)

    def test_exactly_at_private_threshold_is_reportable(self):
        # Art. 5(2): the threshold is "reached", so 0.10% exactly is in scope.
        report = self.evaluate(short_shares_qty=100_000)
        self.assertEqual(report.net_short_percentage, 0.10)
        self.assertEqual(report.reporting_status, STATUS_PRIVATE_NCA)

    def test_exactly_at_public_threshold_is_publicly_disclosable(self):
        report = self.evaluate(short_shares_qty=500_000)
        self.assertEqual(report.net_short_percentage, 0.50)
        self.assertEqual(report.reporting_status, STATUS_PUBLIC_DISCLOSURE)

    def test_below_all_thresholds(self):
        # 50,000 / 100,000,000 = 0.05%
        report = self.evaluate(short_shares_qty=50_000)
        self.assertEqual(report.net_short_percentage, 0.05)
        self.assertEqual(report.reporting_status, STATUS_BELOW_THRESHOLDS)
        self.assertEqual(report.disclosure_action, ACTION_NONE)
        self.assertIsNone(report.current_threshold_pct)

    def test_net_long_position_is_not_reportable(self):
        report = self.evaluate(short_shares_qty=100_000, long_shares_qty=900_000)
        self.assertEqual(report.net_short_shares_qty, -800_000)
        self.assertEqual(report.reporting_status, STATUS_BELOW_THRESHOLDS)
        self.assertEqual(report.disclosure_action, ACTION_NONE)

    def test_long_leg_is_netted_before_the_threshold_test(self):
        # (700,000 - 250,000) / 100,000,000 = 0.45%
        report = self.evaluate(short_shares_qty=700_000, long_shares_qty=250_000)
        self.assertEqual(report.net_short_percentage, 0.45)
        self.assertEqual(report.reporting_status, STATUS_PRIVATE_NCA)

    def test_delta_adjusted_fractional_quantities_are_accepted(self):
        # (250,000.5 - 50,000.25) / 100,000,000 = 0.20000025% -> 0.20%
        report = self.evaluate(short_shares_qty=250_000.5, long_shares_qty=50_000.25)
        self.assertAlmostEqual(report.net_short_shares_qty, 200_000.25, places=6)
        self.assertEqual(report.net_short_percentage, 0.20)


class TestEsmaTruncation(SsrTestBase):
    """ESMA Q&A A5.6: truncate to two decimals; test the threshold on that figure."""

    def test_esma_worked_example_0_3199_reports_as_0_31(self):
        # 319,900 / 100,000,000 = 0.3199% -> ESMA says report 0.31%.
        report = self.evaluate(short_shares_qty=319_900)
        self.assertEqual(report.net_short_percentage, 0.31)
        self.assertAlmostEqual(report.net_short_percentage_exact, 0.3199, places=10)
        self.assertEqual(report.current_threshold_pct, 0.30)

    def test_esma_worked_example_0_1987_reports_as_0_19(self):
        # 198,700 / 100,000,000 = 0.1987%. Under the pre-2022 0.2% threshold ESMA's
        # answer was "no notification"; under today's 0.1% threshold it is
        # notifiable, but the figure filed is still the truncated 0.19%.
        report = self.evaluate(short_shares_qty=198_700)
        self.assertEqual(report.net_short_percentage, 0.19)
        self.assertEqual(report.reporting_status, STATUS_PRIVATE_NCA)
        self.assertEqual(report.current_threshold_pct, 0.10)

    def test_regression_0_49999_is_not_promoted_to_public_disclosure(self):
        # 499,990 / 100,000,000 = 0.49999%. v1.0.0 used round(pct, 4), which
        # produced 0.5 and demanded a public disclosure that is not owed.
        report = self.evaluate(short_shares_qty=499_990)
        self.assertEqual(report.net_short_percentage, 0.49)
        self.assertEqual(report.reporting_status, STATUS_PRIVATE_NCA)
        self.assertEqual(report.disclosure_action, ACTION_NOTIFY_NCA)

    def test_regression_0_09999_does_not_trigger_a_notification(self):
        # 99,990 / 100,000,000 = 0.09999%. v1.0.0's round(pct, 4) gave 0.1 and
        # raised a notification that Art. 5(2) does not require.
        report = self.evaluate(short_shares_qty=99_990)
        self.assertEqual(report.net_short_percentage, 0.09)
        self.assertEqual(report.reporting_status, STATUS_BELOW_THRESHOLDS)
        self.assertEqual(report.disclosure_action, ACTION_NONE)

    def test_truncation_never_rounds_up_across_a_band(self):
        # 299,999 / 100,000,000 = 0.299999% -> 0.29%, band 0.20% not 0.30%.
        report = self.evaluate(short_shares_qty=299_999)
        self.assertEqual(report.net_short_percentage, 0.29)
        self.assertEqual(report.current_threshold_pct, 0.20)

    def test_issued_capital_not_a_round_number(self):
        # 1,234 / 987,654 = 0.1249366...% -> 0.12%
        report = self.evaluate(issued_share_capital_qty=987_654, short_shares_qty=1_234)
        self.assertEqual(report.net_short_percentage, 0.12)
        self.assertEqual(report.current_threshold_pct, 0.10)


class TestThresholdCrossing(SsrTestBase):
    """ESMA Q&A A5.7: reaching, exceeding or falling below a threshold."""

    def test_movement_inside_an_already_notified_band_requires_nothing(self):
        # Notified at 0.30%; position drifts to 0.3120% -> still the 0.30% band.
        report = self.evaluate(
            short_shares_qty=312_000, previously_notified_percentage=0.30
        )
        self.assertEqual(report.net_short_percentage, 0.31)
        self.assertEqual(report.disclosure_action, ACTION_NONE)
        self.assertEqual(report.reporting_status, STATUS_PRIVATE_NCA)
        self.assertIsNone(report.notification_deadline_local)
        self.assertEqual(report.notification_deadline_basis, DEADLINE_NO_ACTION)

    def test_crossing_up_a_band_requires_notification(self):
        report = self.evaluate(
            short_shares_qty=400_000, previously_notified_percentage=0.30
        )
        self.assertEqual(report.disclosure_action, ACTION_NOTIFY_NCA)
        self.assertEqual(report.previous_threshold_pct, 0.30)
        self.assertEqual(report.current_threshold_pct, 0.40)

    def test_falling_below_the_first_threshold_requires_notification(self):
        # 0.35% -> 0.05%: Art. 5(2) requires notification of the fall below 0.1%.
        report = self.evaluate(
            short_shares_qty=50_000, previously_notified_percentage=0.35
        )
        self.assertEqual(report.reporting_status, STATUS_BELOW_THRESHOLDS)
        self.assertEqual(report.disclosure_action, ACTION_NOTIFY_NCA)
        self.assertIsNone(report.current_threshold_pct)
        self.assertEqual(report.previous_threshold_pct, 0.30)

    def test_falling_out_of_the_publication_regime_updates_the_public_register(self):
        # 0.55% -> 0.45%: the public disclosure must be updated, not just the NCA.
        report = self.evaluate(
            short_shares_qty=450_000, previously_notified_percentage=0.55
        )
        self.assertEqual(report.reporting_status, STATUS_PRIVATE_NCA)
        self.assertEqual(
            report.disclosure_action, ACTION_NOTIFY_NCA_AND_DISCLOSE_PUBLICLY
        )

    def test_moving_between_bands_above_the_public_threshold_is_public(self):
        report = self.evaluate(
            short_shares_qty=700_000, previously_notified_percentage=0.60
        )
        self.assertEqual(
            report.disclosure_action, ACTION_NOTIFY_NCA_AND_DISCLOSE_PUBLICLY
        )

    def test_no_prior_notification_is_treated_conservatively_as_due(self):
        report = self.evaluate(short_shares_qty=300_000)
        self.assertIsNone(report.previous_threshold_pct)
        self.assertEqual(report.disclosure_action, ACTION_NOTIFY_NCA)

    def test_staying_below_the_threshold_with_no_history_requires_nothing(self):
        report = self.evaluate(short_shares_qty=90_000)
        self.assertEqual(report.disclosure_action, ACTION_NONE)

    def test_previously_notified_below_threshold_is_already_closed(self):
        # A prior notification recorded as 0.05% sits in no band, same as now.
        report = self.evaluate(
            short_shares_qty=60_000, previously_notified_percentage=0.05
        )
        self.assertEqual(report.disclosure_action, ACTION_NONE)


class TestArticle12Independence(SsrTestBase):
    """Art. 12 must never suppress the Arts. 5/6 evaluation (v1.0.0 regression)."""

    def test_uncovered_position_still_produces_the_public_disclosure(self):
        report = self.evaluate(
            short_shares_qty=800_000, has_valid_locate_agreement=False
        )
        self.assertEqual(report.reporting_status, STATUS_PUBLIC_DISCLOSURE)
        self.assertEqual(
            report.disclosure_action, ACTION_NOTIFY_NCA_AND_DISCLOSE_PUBLICLY
        )
        self.assertEqual(report.net_short_percentage, 0.80)
        # ...and the coverage gap is still surfaced and still blocks execution.
        self.assertEqual(report.art12_status, ART12_BREACH)
        self.assertFalse(report.is_short_execution_allowed)

    def test_covered_position_allows_execution(self):
        report = self.evaluate(short_shares_qty=200_000)
        self.assertEqual(report.art12_status, ART12_COVERED)
        self.assertTrue(report.is_short_execution_allowed)

    def test_net_long_without_locate_is_not_a_coverage_breach(self):
        report = self.evaluate(
            short_shares_qty=10_000,
            long_shares_qty=50_000,
            has_valid_locate_agreement=False,
        )
        self.assertEqual(report.art12_status, ART12_NOT_APPLICABLE)
        self.assertTrue(report.is_short_execution_allowed)


class TestArticle12OrderGate(SsrTestBase):
    def order(self, **overrides) -> ShortSaleOrderIntent:
        base = dict(isin="DE0007100000", symbol="MBG", order_qty=10_000)
        base.update(overrides)
        return ShortSaleOrderIntent(**base)

    def test_share_sale_without_any_arrangement_is_blocked(self):
        decision = self.engine.evaluate_short_sale_order(
            self.order(covering_arrangement=COVER_NONE)
        )
        self.assertFalse(decision.is_execution_allowed)
        self.assertEqual(decision.art12_status, ART12_BREACH)
        self.assertEqual(decision.rejection_reason, "NO_ART12_COVERING_ARRANGEMENT")

    def test_borrowed_share_with_evidence_is_allowed(self):
        decision = self.engine.evaluate_short_sale_order(
            self.order(
                covering_arrangement=COVER_BORROWED,
                locate_evidence_reference="SBL-2026-08-24-001",
            )
        )
        self.assertTrue(decision.is_execution_allowed)
        self.assertEqual(decision.art12_status, ART12_COVERED)

    def test_agreement_to_borrow_with_evidence_is_allowed(self):
        decision = self.engine.evaluate_short_sale_order(
            self.order(
                covering_arrangement=COVER_AGREEMENT_TO_BORROW,
                locate_evidence_reference="AGR-77",
            )
        )
        self.assertTrue(decision.is_execution_allowed)

    def test_locate_arrangement_without_durable_medium_evidence_is_blocked(self):
        # ITS 827/2012 Art. 7 requires the confirmation in a durable medium.
        decision = self.engine.evaluate_short_sale_order(
            self.order(covering_arrangement=COVER_LOCATE_ARRANGEMENT)
        )
        self.assertFalse(decision.is_execution_allowed)
        self.assertEqual(decision.rejection_reason, "NO_DURABLE_MEDIUM_EVIDENCE")

    def test_etf_short_sale_is_outside_article_12(self):
        # ESMA Q&A A4.7: ETFs are not shares for Art. 12 purposes.
        decision = self.engine.evaluate_short_sale_order(
            self.order(instrument_type=INSTRUMENT_ETF, covering_arrangement=COVER_NONE)
        )
        self.assertTrue(decision.is_execution_allowed)
        self.assertEqual(decision.art12_status, ART12_NOT_APPLICABLE)

    def test_depositary_receipt_short_sale_is_outside_article_12(self):
        # ESMA Q&A A4.6: ADRs/GDRs are not shares for Art. 12 purposes.
        decision = self.engine.evaluate_short_sale_order(
            self.order(
                instrument_type=INSTRUMENT_DEPOSITARY_RECEIPT,
                covering_arrangement=COVER_NONE,
            )
        )
        self.assertTrue(decision.is_execution_allowed)

    def test_derivative_is_not_a_short_sale_of_a_share(self):
        decision = self.engine.evaluate_short_sale_order(
            self.order(
                instrument_type=INSTRUMENT_DERIVATIVE, covering_arrangement=COVER_NONE
            )
        )
        self.assertTrue(decision.is_execution_allowed)

    def test_article_16_exempt_share_is_outside_article_12(self):
        decision = self.engine.evaluate_short_sale_order(
            self.order(is_exempt_principal_venue_outside_union=True)
        )
        self.assertTrue(decision.is_execution_allowed)
        self.assertEqual(decision.art12_status, ART12_NOT_APPLICABLE)

    def test_article_17_market_maker_is_outside_article_12(self):
        decision = self.engine.evaluate_short_sale_order(
            self.order(is_market_making_exempt=True)
        )
        self.assertTrue(decision.is_execution_allowed)

    def test_unknown_covering_arrangement_is_rejected_not_treated_as_covered(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate_short_sale_order(
                self.order(covering_arrangement="PROBABLY_FINE")
            )

    def test_non_positive_order_quantity_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate_short_sale_order(self.order(order_qty=0))


class TestExemptions(SsrTestBase):
    def test_article_16_share_is_out_of_scope_for_arts_5_and_6(self):
        report = self.evaluate(
            short_shares_qty=900_000, is_exempt_principal_venue_outside_union=True
        )
        self.assertEqual(report.reporting_status, STATUS_OUT_OF_SCOPE)
        self.assertEqual(report.disclosure_action, ACTION_NONE)
        self.assertEqual(report.net_short_percentage, 0.90)

    def test_article_17_market_maker_is_out_of_scope_for_arts_5_and_6(self):
        report = self.evaluate(short_shares_qty=900_000, is_market_making_exempt=True)
        self.assertEqual(report.reporting_status, STATUS_OUT_OF_SCOPE)
        self.assertEqual(report.disclosure_action, ACTION_NONE)

    def test_exempt_position_without_locate_does_not_block_execution(self):
        report = self.evaluate(
            short_shares_qty=900_000,
            has_valid_locate_agreement=False,
            is_exempt_principal_venue_outside_union=True,
        )
        self.assertTrue(report.is_short_execution_allowed)
        self.assertEqual(report.art12_status, ART12_NOT_APPLICABLE)


@unittest.skipUnless(HAS_TZDATA, "IANA tz database unavailable (install tzdata)")
class TestFilingDeadline(SsrTestBase):
    def test_deadline_is_1530_local_time_on_the_next_trading_day(self):
        from zoneinfo import ZoneInfo

        report = self.evaluate(short_shares_qty=600_000)
        self.assertEqual(report.notification_deadline_basis, DEADLINE_OK)
        self.assertEqual(
            report.notification_deadline_local,
            datetime(2026, 8, 25, 15, 30, tzinfo=ZoneInfo("Europe/Berlin")),
        )

    def test_regression_deadline_is_not_cet_for_an_eastern_european_nca(self):
        # v1.0.0 hard-coded "T+1 15:30 CET". 15:30 in Helsinki is 12:30 UTC while
        # 15:30 in Berlin is 13:30 UTC, so a firm filing on the CET clock to a
        # Finnish NCA files a full hour late.
        helsinki = self.evaluate(
            short_shares_qty=600_000, nca_timezone="Europe/Helsinki"
        )
        berlin = self.evaluate(short_shares_qty=600_000, nca_timezone="Europe/Berlin")
        delta = berlin.notification_deadline_local.astimezone(
            timezone.utc
        ) - helsinki.notification_deadline_local.astimezone(timezone.utc)
        self.assertEqual(delta, timedelta(hours=1))

    def test_friday_position_is_due_on_monday_with_the_weekday_calendar(self):
        report = self.evaluate(
            short_shares_qty=600_000, position_date=date(2026, 8, 21)  # Friday
        )
        self.assertEqual(report.notification_deadline_local.date(), date(2026, 8, 24))

    def test_missing_timezone_fails_closed_rather_than_defaulting_to_cet(self):
        report = self.evaluate(short_shares_qty=600_000, nca_timezone=None)
        self.assertIsNone(report.notification_deadline_local)
        self.assertEqual(report.notification_deadline_basis, DEADLINE_NO_TIMEZONE)
        # The description must restate the actual rule, not offer a CET fallback.
        self.assertIn("local time", report.reporting_deadline_description)
        self.assertIn("not computed", report.reporting_deadline_description)

    def test_missing_trading_calendar_fails_closed(self):
        engine = EuShortSellingRegulationEngine()  # no next_trading_day supplied
        report = engine.evaluate_short_position_disclosure(
            self.position(short_shares_qty=600_000)
        )
        self.assertIsNone(report.notification_deadline_local)
        self.assertEqual(report.notification_deadline_basis, DEADLINE_NO_CALENDAR)

    def test_missing_position_date_fails_closed(self):
        report = self.evaluate(short_shares_qty=600_000, position_date=None)
        self.assertEqual(report.notification_deadline_basis, DEADLINE_NO_POSITION_DATE)

    def test_unknown_timezone_is_rejected(self):
        with self.assertRaises(ValueError):
            self.evaluate(short_shares_qty=600_000, nca_timezone="Europe/Atlantis")

    def test_calendar_returning_a_non_advancing_day_is_rejected(self):
        engine = EuShortSellingRegulationEngine(next_trading_day=lambda d: d)
        with self.assertRaises(ValueError):
            engine.evaluate_short_position_disclosure(
                self.position(short_shares_qty=600_000)
            )

    def test_calendar_returning_a_datetime_is_rejected(self):
        engine = EuShortSellingRegulationEngine(
            next_trading_day=lambda d: datetime(2026, 8, 25, 9, 0)
        )
        with self.assertRaises(TypeError):
            engine.evaluate_short_position_disclosure(
                self.position(short_shares_qty=600_000)
            )


class TestWeekdayCalendarHelper(unittest.TestCase):
    def test_weekday_rolls_to_the_next_day(self):
        self.assertEqual(
            next_weekday_excluding_holidays(date(2026, 8, 24)), date(2026, 8, 25)
        )

    def test_friday_rolls_to_monday(self):
        self.assertEqual(
            next_weekday_excluding_holidays(date(2026, 8, 21)), date(2026, 8, 24)
        )

    def test_saturday_rolls_to_monday(self):
        self.assertEqual(
            next_weekday_excluding_holidays(date(2026, 8, 22)), date(2026, 8, 24)
        )


class TestInputValidation(SsrTestBase):
    def test_zero_issued_share_capital_is_rejected(self):
        with self.assertRaises(ValueError):
            self.evaluate(issued_share_capital_qty=0, short_shares_qty=1)

    def test_negative_issued_share_capital_is_rejected(self):
        with self.assertRaises(ValueError):
            self.evaluate(issued_share_capital_qty=-1, short_shares_qty=1)

    def test_non_integer_issued_share_capital_is_rejected(self):
        with self.assertRaises(TypeError):
            self.evaluate(issued_share_capital_qty=1e8, short_shares_qty=1)

    def test_negative_short_quantity_is_rejected(self):
        with self.assertRaises(ValueError):
            self.evaluate(short_shares_qty=-100)

    def test_negative_long_quantity_is_rejected(self):
        with self.assertRaises(ValueError):
            self.evaluate(long_shares_qty=-100)

    def test_nan_quantity_is_rejected_rather_than_propagated(self):
        with self.assertRaises(ValueError):
            self.evaluate(short_shares_qty=float("nan"))

    def test_infinite_quantity_is_rejected(self):
        with self.assertRaises(ValueError):
            self.evaluate(short_shares_qty=float("inf"))

    def test_empty_isin_is_rejected(self):
        with self.assertRaises(ValueError):
            self.evaluate(isin="   ", short_shares_qty=1)

    def test_non_bool_locate_flag_is_rejected(self):
        with self.assertRaises(TypeError):
            self.evaluate(short_shares_qty=1, has_valid_locate_agreement="yes")

    def test_datetime_as_position_date_is_rejected(self):
        with self.assertRaises(TypeError):
            self.evaluate(short_shares_qty=1, position_date=datetime(2026, 8, 24))

    def test_negative_previously_notified_percentage_is_rejected(self):
        with self.assertRaises(ValueError):
            self.evaluate(short_shares_qty=1, previously_notified_percentage=-0.3)


class TestEngineConfiguration(unittest.TestCase):
    def test_public_threshold_below_private_threshold_is_rejected(self):
        with self.assertRaises(ValueError):
            EuShortSellingRegulationEngine(
                private_nca_threshold_pct=0.50, public_disclosure_threshold_pct=0.10
            )

    def test_non_positive_threshold_is_rejected(self):
        with self.assertRaises(ValueError):
            EuShortSellingRegulationEngine(private_nca_threshold_pct=0.0)

    def test_threshold_finer_than_one_hundredth_of_a_percent_is_rejected(self):
        with self.assertRaises(ValueError):
            EuShortSellingRegulationEngine(private_nca_threshold_pct=0.105)

    def test_defaults_match_the_thresholds_in_force_since_31_january_2022(self):
        engine = EuShortSellingRegulationEngine()
        self.assertEqual(engine.private_nca_threshold_pct, 0.10)
        self.assertEqual(engine.public_disclosure_threshold_pct, 0.50)
        self.assertEqual(engine.threshold_step_pct, 0.10)

    def test_emergency_threshold_override_is_honoured(self):
        # E.g. an Art. 18-23 emergency measure lowering the notification threshold.
        engine = EuShortSellingRegulationEngine(private_nca_threshold_pct=0.05)
        report = engine.evaluate_short_position_disclosure(
            EquityShortPositionState(
                isin="FR0000120271",
                symbol="TTE",
                issued_share_capital_qty=ISSUED,
                long_shares_qty=0,
                short_shares_qty=60_000,  # 0.06%
                has_valid_locate_agreement=True,
            )
        )
        self.assertEqual(report.reporting_status, STATUS_PRIVATE_NCA)
        self.assertEqual(report.current_threshold_pct, 0.05)


class TestReportContract(SsrTestBase):
    def test_report_echoes_the_instrument_identifiers(self):
        report = self.evaluate(short_shares_qty=600_000)
        self.assertEqual(report.isin, "DE0007100000")
        self.assertEqual(report.symbol, "MBG")

    def test_audit_notes_record_the_band_and_the_deadline_rule(self):
        report = self.evaluate(short_shares_qty=600_000)
        self.assertIn("0.60%", report.audit_notes)
        self.assertIn("Art. 9(2)", report.audit_notes)
        self.assertIn("local time", report.audit_notes)

    def test_report_is_immutable(self):
        report = self.evaluate(short_shares_qty=600_000)
        with self.assertRaises(Exception):
            report.reporting_status = STATUS_BELOW_THRESHOLDS

    def test_default_instrument_type_for_an_order_is_a_share(self):
        order = ShortSaleOrderIntent(isin="X", symbol="Y", order_qty=1)
        self.assertEqual(order.instrument_type, INSTRUMENT_SHARE)
        self.assertEqual(order.covering_arrangement, COVER_NONE)


if __name__ == "__main__":
    unittest.main()
