"""Unit tests for the SEC Form 4 insider transaction filing signal engine.

Expected sentiment scores are derived by hand in the test bodies from the
role-weight schedule and the trade notionals, never by re-running the
engine's own arithmetic.
"""

import logging
import unittest
from datetime import date, datetime, timedelta, timezone

from insider_transaction_filing_signal_research import (
    DEFAULT_ROLE_WEIGHTS,
    RULE_10B5_1_CHECKBOX_MANDATORY_FROM,
    Form4TransactionRecord,
    InsiderFilingSignalEngine,
    InsiderFilingSignalError,
    PlanStatus,
    SignalClassification,
    TraderRegularity,
    classify_trader_regularity,
    parse_primary_transaction_code,
)

ET = timezone(timedelta(hours=-4))  # US/Eastern in daylight time.
AS_OF = datetime(2024, 6, 30, 20, 0, tzinfo=ET)


def make_record(
    filing_id="F1",
    insider_name="Jane Doe",
    transaction_code="P",
    shares=10_000,
    price=50.00,
    transaction_date=date(2024, 6, 3),
    filing_datetime=datetime(2024, 6, 5, 17, 30, tzinfo=ET),
    is_director=False,
    is_officer=True,
    is_ten_percent_owner=False,
    is_other=False,
    officer_title="Chief Executive Officer",
    plan_status=PlanStatus.NON_PLAN,
    is_open_market=True,
):
    """Build a Form 4 record; defaults are a $500k CEO open-market purchase."""
    return Form4TransactionRecord(
        filing_id=filing_id,
        insider_name=insider_name,
        transaction_code=transaction_code,
        shares=shares,
        price=price,
        transaction_date=transaction_date,
        filing_datetime=filing_datetime,
        is_director=is_director,
        is_officer=is_officer,
        is_ten_percent_owner=is_ten_percent_owner,
        is_other=is_other,
        officer_title=officer_title,
        plan_status=plan_status,
        is_open_market=is_open_market,
    )


class TestTransactionCodeParsing(unittest.TestCase):

    def test_plain_codes_round_trip(self):
        self.assertEqual(parse_primary_transaction_code("p"), "P")
        self.assertEqual(parse_primary_transaction_code("  S  "), "S")

    def test_combined_equity_swap_code_resolves_to_primary(self):
        # Form 4 General Instruction 8: equity-swap trades are reported "S/K"
        # or "P/K". Matching the raw string against "S" drops them silently.
        self.assertEqual(parse_primary_transaction_code("S/K"), "S")
        self.assertEqual(parse_primary_transaction_code("P/K"), "P")

    def test_unknown_and_empty_codes_rejected(self):
        with self.assertRaises(InsiderFilingSignalError):
            parse_primary_transaction_code("Q")
        with self.assertRaises(InsiderFilingSignalError):
            parse_primary_transaction_code("")


class TestRoleWeighting(unittest.TestCase):

    def setUp(self):
        self.engine = InsiderFilingSignalEngine()

    def test_officer_title_free_text_maps_to_ceo_tier(self):
        for title in ("Chief Executive Officer", "Chairman, CEO & Pres", "C.E.O."):
            record = make_record(officer_title=title)
            self.assertEqual(self.engine.resolve_role_tier(record), "CEO", title)

    def test_officer_title_maps_to_cfo_tier(self):
        record = make_record(officer_title="EVP and Chief Financial Officer")
        self.assertEqual(self.engine.resolve_role_tier(record), "CFO")

    def test_unmatched_officer_title_falls_to_other_officer(self):
        record = make_record(officer_title="General Counsel")
        self.assertEqual(self.engine.resolve_role_tier(record), "OTHER_OFFICER")
        self.assertEqual(self.engine.get_role_weight(record), 0.8)

    def test_overlapping_relationship_flags_take_highest_weight(self):
        # EDGAR relationship flags are independent booleans: a founder-CEO on the
        # board is officer AND director AND ten-percent owner at once. The old
        # single-string role model could only express one of the three.
        record = make_record(
            is_officer=True,
            is_director=True,
            is_ten_percent_owner=True,
            officer_title="Chief Executive Officer",
        )
        self.assertEqual(self.engine.resolve_role_tier(record), "CEO")
        self.assertEqual(self.engine.get_role_weight(record), 1.0)

    def test_director_only_and_ten_percent_only(self):
        director = make_record(is_officer=False, is_director=True, officer_title="")
        owner = make_record(
            is_officer=False, is_ten_percent_owner=True, officer_title=""
        )
        self.assertEqual(self.engine.get_role_weight(director), 0.6)
        self.assertEqual(self.engine.get_role_weight(owner), 0.3)

    def test_unclassifiable_role_gets_zero_not_a_silent_mid_weight(self):
        # Regression: the previous implementation returned 0.5 for any
        # unrecognised role string -- undocumented, and above the documented
        # ten-percent-owner weight of 0.3.
        record = make_record(
            is_officer=False, is_director=False, is_ten_percent_owner=False,
            is_other=True, officer_title="",
        )
        self.assertIsNone(self.engine.resolve_role_tier(record))
        self.assertEqual(self.engine.get_role_weight(record), 0.0)

    def test_role_weight_map_is_not_mutable_shared_state(self):
        engine = InsiderFilingSignalEngine(role_weights={"DIRECTOR": 0.9})
        self.assertEqual(engine.role_weights["DIRECTOR"], 0.9)
        self.assertEqual(DEFAULT_ROLE_WEIGHTS["DIRECTOR"], 0.6)
        self.assertEqual(InsiderFilingSignalEngine().role_weights["DIRECTOR"], 0.6)
        with self.assertRaises(TypeError):
            engine.role_weights["DIRECTOR"] = 0.1

    def test_negative_role_weight_rejected(self):
        # A negative weight would flip a purchase into a sale and push the
        # normalised score outside [-1, +1].
        with self.assertRaises(InsiderFilingSignalError):
            InsiderFilingSignalEngine(role_weights={"CEO": -1.0})


class TestPointInTimeIntegrity(unittest.TestCase):

    def setUp(self):
        self.engine = InsiderFilingSignalEngine()

    def test_filing_not_yet_disseminated_is_excluded(self):
        # Rule 16a-3(g)(1) allows two business days between execution and
        # filing. Scoring the trade on its transaction date is look-ahead bias.
        traded = make_record(
            transaction_date=date(2024, 6, 28),
            filing_datetime=datetime(2024, 7, 2, 16, 0, tzinfo=ET),
        )
        report = self.engine.analyze_form4_filings("AAPL", [traded], as_of=AS_OF)
        self.assertEqual(report.not_yet_public_excluded_count, 1)
        self.assertEqual(report.filings_in_scope, 0)
        self.assertEqual(report.opportunistic_buys_count, 0)
        self.assertEqual(
            report.signal_classification, SignalClassification.INSUFFICIENT_DATA
        )

    def test_same_filing_becomes_visible_once_as_of_advances(self):
        traded = make_record(
            transaction_date=date(2024, 6, 28),
            filing_datetime=datetime(2024, 7, 2, 16, 0, tzinfo=ET),
        )
        later = datetime(2024, 7, 2, 16, 0, tzinfo=ET)  # exactly at dissemination
        report = self.engine.analyze_form4_filings("AAPL", [traded], as_of=later)
        self.assertEqual(report.not_yet_public_excluded_count, 0)
        self.assertEqual(report.opportunistic_buys_count, 1)
        self.assertEqual(report.max_trade_to_file_lag_days, 4)

    def test_naive_as_of_rejected(self):
        with self.assertRaises(InsiderFilingSignalError):
            self.engine.analyze_form4_filings(
                "AAPL", [make_record()], as_of=datetime(2024, 6, 30, 20, 0)
            )

    def test_naive_filing_datetime_rejected(self):
        record = make_record(filing_datetime=datetime(2024, 6, 5, 17, 30))
        with self.assertRaises(InsiderFilingSignalError):
            self.engine.analyze_form4_filings("AAPL", [record], as_of=AS_OF)

    def test_filing_before_its_own_transaction_rejected(self):
        record = make_record(
            transaction_date=date(2024, 6, 10),
            filing_datetime=datetime(2024, 6, 5, 17, 30, tzinfo=ET),
        )
        with self.assertRaises(InsiderFilingSignalError):
            self.engine.analyze_form4_filings("AAPL", [record], as_of=AS_OF)

    def test_lookback_window_excludes_stale_filings(self):
        stale = make_record(
            filing_id="OLD",
            transaction_date=date(2024, 1, 8),
            filing_datetime=datetime(2024, 1, 10, 16, 0, tzinfo=ET),
        )
        fresh = make_record(filing_id="NEW")
        report = self.engine.analyze_form4_filings(
            "AAPL", [stale, fresh], as_of=AS_OF, lookback_days=90
        )
        self.assertEqual(report.outside_lookback_excluded_count, 1)
        self.assertEqual(report.opportunistic_buys_count, 1)

    def test_negative_lookback_days_rejected(self):
        with self.assertRaises(InsiderFilingSignalError):
            self.engine.analyze_form4_filings(
                "AAPL", [make_record()], as_of=AS_OF, lookback_days=-1
            )


class TestPlanStatusHandling(unittest.TestCase):

    def setUp(self):
        self.engine = InsiderFilingSignalEngine()

    def test_disclosed_plan_sale_is_filtered_by_default(self):
        plan_sale = make_record(
            filing_id="F2", insider_name="John Smith", transaction_code="S",
            shares=5_000, is_officer=False, is_director=True, officer_title="",
            plan_status=PlanStatus.PLAN,
        )
        report = self.engine.analyze_form4_filings("AAPL", [plan_sale], as_of=AS_OF)
        self.assertEqual(report.routine_10b5_1_filtered_count, 1)
        self.assertEqual(report.opportunistic_sales_count, 0)
        self.assertEqual(
            report.signal_classification, SignalClassification.INSUFFICIENT_DATA
        )

    def test_plan_trades_can_be_retained(self):
        # SEC Release 33-11138 documents abnormal returns following plan sales;
        # discarding them must be a choice, not a hard-wired assumption.
        engine = InsiderFilingSignalEngine(exclude_plan_trades=False)
        plan_sale = make_record(transaction_code="S", plan_status=PlanStatus.PLAN)
        report = engine.analyze_form4_filings("AAPL", [plan_sale], as_of=AS_OF)
        self.assertEqual(report.routine_10b5_1_filtered_count, 0)
        self.assertEqual(report.opportunistic_sales_count, 1)
        self.assertEqual(report.weighted_net_sentiment_score, -1.0)

    def test_unknown_plan_status_is_counted_and_scored_by_default(self):
        # Pre-April-2023 filings carry no reliable designation; the count is the
        # only thing that makes that contamination visible in the report.
        record = make_record(plan_status=PlanStatus.UNKNOWN)
        report = self.engine.analyze_form4_filings("AAPL", [record], as_of=AS_OF)
        self.assertEqual(report.unknown_plan_status_count, 1)
        self.assertEqual(report.routine_10b5_1_filtered_count, 0)
        self.assertEqual(report.opportunistic_buys_count, 1)

    def test_unknown_plan_status_can_be_treated_conservatively(self):
        engine = InsiderFilingSignalEngine(treat_unknown_plan_status_as_plan=True)
        record = make_record(plan_status=PlanStatus.UNKNOWN)
        report = engine.analyze_form4_filings("AAPL", [record], as_of=AS_OF)
        self.assertEqual(report.unknown_plan_status_count, 1)
        self.assertEqual(report.routine_10b5_1_filtered_count, 1)
        self.assertEqual(report.opportunistic_buys_count, 0)

    def test_asserted_plan_status_on_pre_2023_filing_warns(self):
        old = make_record(
            transaction_date=date(2019, 5, 6),
            filing_datetime=datetime(2019, 5, 8, 16, 0, tzinfo=ET),
            plan_status=PlanStatus.NON_PLAN,
        )
        self.assertLess(
            old.filing_datetime.date(), RULE_10B5_1_CHECKBOX_MANDATORY_FROM
        )
        with self.assertLogs(
            "insider_transaction_filing_signal_research", level=logging.WARNING
        ) as captured:
            self.engine.analyze_form4_filings("AAPL", [old], as_of=AS_OF)
        self.assertTrue(
            any("voluntary" in line for line in captured.output), captured.output
        )


class TestOpenMarketAndCodeFiltering(unittest.TestCase):

    def setUp(self):
        self.engine = InsiderFilingSignalEngine()

    def test_non_open_market_transaction_excluded(self):
        private = make_record(is_open_market=False)
        report = self.engine.analyze_form4_filings("AAPL", [private], as_of=AS_OF)
        self.assertEqual(report.non_open_market_excluded_count, 1)
        self.assertEqual(report.opportunistic_buys_count, 0)

    def test_non_ps_codes_are_counted_not_silently_dropped(self):
        # Regression: the previous implementation dropped every code other than
        # bare P/S without recording it, so the report's counts could not be
        # reconciled against the filings supplied.
        grant = make_record(filing_id="A1", transaction_code="A")
        withholding = make_record(filing_id="F1x", transaction_code="F")
        gift = make_record(filing_id="G1", transaction_code="G")
        report = self.engine.analyze_form4_filings(
            "AAPL", [grant, withholding, gift], as_of=AS_OF
        )
        self.assertEqual(report.non_purchase_sale_code_count, 3)
        self.assertEqual(
            report.non_purchase_sale_codes_seen, {"A": 1, "F": 1, "G": 1}
        )

    def test_combined_swap_code_sale_is_scored(self):
        swap_sale = make_record(transaction_code="S/K")
        report = self.engine.analyze_form4_filings("AAPL", [swap_sale], as_of=AS_OF)
        self.assertEqual(report.opportunistic_sales_count, 1)
        self.assertEqual(report.non_purchase_sale_code_count, 0)

    def test_exclusion_counters_reconcile_with_filings_supplied(self):
        filings = [
            make_record(filing_id="1"),
            make_record(filing_id="2", transaction_code="S"),
            make_record(filing_id="3", plan_status=PlanStatus.PLAN),
            make_record(filing_id="4", is_open_market=False),
            make_record(filing_id="5", transaction_code="M"),
            make_record(
                filing_id="6",
                filing_datetime=datetime(2024, 8, 1, 16, 0, tzinfo=ET),
                transaction_date=date(2024, 7, 30),
            ),
        ]
        report = self.engine.analyze_form4_filings("AAPL", filings, as_of=AS_OF)
        partition = (
            report.not_yet_public_excluded_count
            + report.outside_lookback_excluded_count
            + report.routine_10b5_1_filtered_count
            + report.routine_trader_filtered_count
            + report.non_open_market_excluded_count
            + report.non_purchase_sale_code_count
            + report.opportunistic_buys_count
            + report.opportunistic_sales_count
        )
        self.assertEqual(partition, report.filings_supplied)
        self.assertEqual(report.filings_supplied, 6)


class TestSentimentScoring(unittest.TestCase):

    def setUp(self):
        self.engine = InsiderFilingSignalEngine()

    def test_single_ceo_purchase_saturates_at_plus_one(self):
        report = self.engine.analyze_form4_filings(
            "AAPL", [make_record()], as_of=AS_OF
        )
        self.assertEqual(report.weighted_net_sentiment_score, 1.0)
        self.assertEqual(report.total_opportunistic_buy_notional_usd, 500_000.0)
        self.assertEqual(
            report.signal_classification,
            SignalClassification.STRONG_BULLISH_OPPORTUNISTIC_BUY,
        )

    def test_role_weighting_changes_the_sign_of_the_score(self):
        # CEO buy: 1,000 x $100 = $100,000 at weight 1.0 -> weighted 100,000.
        # Ten-percent-owner sale: 4,000 x $100 = $400,000 at weight 0.3
        #   -> weighted 120,000.
        # Score = (100,000 - 120,000) / 220,000 = -0.090909... -> -0.0909.
        # Unweighted notionals would have given (100k - 400k)/500k = -0.60.
        ceo_buy = make_record(
            filing_id="B", insider_name="Jane Doe", shares=1_000, price=100.0
        )
        owner_sale = make_record(
            filing_id="S", insider_name="Big Fund LP", transaction_code="S",
            shares=4_000, price=100.0, is_officer=False,
            is_ten_percent_owner=True, officer_title="",
        )
        report = self.engine.analyze_form4_filings(
            "AAPL", [ceo_buy, owner_sale], as_of=AS_OF
        )
        self.assertAlmostEqual(report.weighted_net_sentiment_score, -0.0909, places=4)
        self.assertEqual(report.signal_classification, SignalClassification.NEUTRAL)
        self.assertEqual(report.distinct_insiders_count, 2)

    def test_offsetting_weighted_flows_score_zero(self):
        # CEO buy 2,000 x $50 = $100,000 at 1.0 -> 100,000.
        # Director sale 5,000 x $50 = $250,000 at 0.6 -> 150,000.
        # Directors' weight is 0.6, so equal weighted flow needs
        # 100,000 / 0.6 = 166,666.67 of director notional: 3,333.3333 shares.
        ceo_buy = make_record(
            filing_id="B", insider_name="Jane Doe", shares=2_000, price=50.0
        )
        director_sale = make_record(
            filing_id="S", insider_name="Ann Roe", transaction_code="S",
            shares=100_000 / 0.6 / 50.0, price=50.0, is_officer=False,
            is_director=True, officer_title="",
        )
        report = self.engine.analyze_form4_filings(
            "AAPL", [ceo_buy, director_sale], as_of=AS_OF
        )
        self.assertAlmostEqual(report.weighted_net_sentiment_score, 0.0, places=6)
        self.assertEqual(report.signal_classification, SignalClassification.NEUTRAL)

    def test_score_is_bounded_for_a_large_mixed_book(self):
        filings = [
            make_record(filing_id=f"B{i}", insider_name=f"Buyer {i}", shares=1_000)
            for i in range(20)
        ] + [
            make_record(
                filing_id=f"S{i}", insider_name=f"Seller {i}",
                transaction_code="S", shares=900,
            )
            for i in range(20)
        ]
        report = self.engine.analyze_form4_filings("AAPL", filings, as_of=AS_OF)
        self.assertGreaterEqual(report.weighted_net_sentiment_score, -1.0)
        self.assertLessEqual(report.weighted_net_sentiment_score, 1.0)

    def test_thresholds_are_inclusive_at_the_boundary(self):
        # Construct exactly +0.30: weighted buy B and sell S with
        # (B - S) / (B + S) = 0.3  =>  S = B * 0.7 / 1.3.
        # Both at weight 1.0 (CEO), buy notional 1,300,000 -> sell 700,000.
        ceo_buy = make_record(
            filing_id="B", insider_name="Jane Doe", shares=13_000, price=100.0
        )
        ceo_sale = make_record(
            filing_id="S", insider_name="Rob Poe", transaction_code="S",
            shares=7_000, price=100.0,
        )
        report = self.engine.analyze_form4_filings(
            "AAPL", [ceo_buy, ceo_sale], as_of=AS_OF
        )
        self.assertAlmostEqual(report.weighted_net_sentiment_score, 0.3, places=6)
        self.assertEqual(
            report.signal_classification,
            SignalClassification.STRONG_BULLISH_OPPORTUNISTIC_BUY,
        )

    def test_just_below_threshold_is_neutral(self):
        engine = InsiderFilingSignalEngine(bullish_threshold=0.3001)
        ceo_buy = make_record(
            filing_id="B", insider_name="Jane Doe", shares=13_000, price=100.0
        )
        ceo_sale = make_record(
            filing_id="S", insider_name="Rob Poe", transaction_code="S",
            shares=7_000, price=100.0,
        )
        report = engine.analyze_form4_filings("AAPL", [ceo_buy, ceo_sale], as_of=AS_OF)
        self.assertEqual(report.signal_classification, SignalClassification.NEUTRAL)

    def test_zero_weight_insider_alone_yields_insufficient_data(self):
        # An "other" filer at unknown_role_weight 0.0 contributes no weighted
        # flow; a directional call would be an artefact of an empty denominator.
        record = make_record(
            is_officer=False, is_director=False, is_ten_percent_owner=False,
            is_other=True, officer_title="",
        )
        with self.assertLogs(
            "insider_transaction_filing_signal_research", level=logging.WARNING
        ):
            report = self.engine.analyze_form4_filings("AAPL", [record], as_of=AS_OF)
        self.assertEqual(report.unclassified_role_count, 1)
        self.assertEqual(report.opportunistic_buys_count, 1)
        self.assertEqual(report.weighted_net_sentiment_score, 0.0)
        self.assertEqual(
            report.signal_classification, SignalClassification.INSUFFICIENT_DATA
        )


class TestSampleFloors(unittest.TestCase):

    def test_de_minimis_trade_below_notional_floor_is_insufficient(self):
        # $1,000 CEO purchase saturates the scale-free score at +1.00, reading
        # identically to a broad multi-million-dollar insider bid.
        engine = InsiderFilingSignalEngine(min_total_notional_usd=100_000.0)
        tiny = make_record(shares=10, price=100.0)
        report = engine.analyze_form4_filings("AAPL", [tiny], as_of=AS_OF)
        self.assertEqual(report.weighted_net_sentiment_score, 1.0)
        self.assertEqual(
            report.signal_classification, SignalClassification.INSUFFICIENT_DATA
        )

    def test_breadth_floor_requires_multiple_distinct_insiders(self):
        engine = InsiderFilingSignalEngine(min_distinct_insiders=2)
        one = make_record()
        report = engine.analyze_form4_filings("AAPL", [one], as_of=AS_OF)
        self.assertEqual(report.distinct_insiders_count, 1)
        self.assertEqual(
            report.signal_classification, SignalClassification.INSUFFICIENT_DATA
        )

        two = make_record(filing_id="F2", insider_name="Rob Poe")
        report = engine.analyze_form4_filings("AAPL", [one, two], as_of=AS_OF)
        self.assertEqual(report.distinct_insiders_count, 2)
        self.assertEqual(
            report.signal_classification,
            SignalClassification.STRONG_BULLISH_OPPORTUNISTIC_BUY,
        )

    def test_empty_filing_list_is_insufficient_data(self):
        report = InsiderFilingSignalEngine().analyze_form4_filings(
            "AAPL", [], as_of=AS_OF
        )
        self.assertEqual(report.filings_supplied, 0)
        self.assertEqual(report.weighted_net_sentiment_score, 0.0)
        self.assertIsNone(report.max_trade_to_file_lag_days)
        self.assertEqual(
            report.signal_classification, SignalClassification.INSUFFICIENT_DATA
        )


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = InsiderFilingSignalEngine()

    def test_non_positive_shares_and_price_rejected(self):
        for kwargs in ({"shares": 0}, {"shares": -100}, {"price": 0.0}, {"price": -5.0}):
            with self.subTest(**kwargs):
                with self.assertRaises(InsiderFilingSignalError):
                    self.engine.analyze_form4_filings(
                        "AAPL", [make_record(**kwargs)], as_of=AS_OF
                    )

    def test_nan_price_rejected_rather_than_propagated(self):
        # Regression: float("nan") previously flowed into the numerator and the
        # denominator, making every comparison false and silently returning
        # NEUTRAL for a book that may have been strongly directional.
        with self.assertRaises(InsiderFilingSignalError):
            self.engine.analyze_form4_filings(
                "AAPL", [make_record(price=float("nan"))], as_of=AS_OF
            )

    def test_infinite_shares_rejected(self):
        with self.assertRaises(InsiderFilingSignalError):
            self.engine.analyze_form4_filings(
                "AAPL", [make_record(shares=float("inf"))], as_of=AS_OF
            )

    def test_blank_symbol_rejected(self):
        with self.assertRaises(InsiderFilingSignalError):
            self.engine.analyze_form4_filings("   ", [make_record()], as_of=AS_OF)

    def test_inverted_thresholds_rejected(self):
        with self.assertRaises(InsiderFilingSignalError):
            InsiderFilingSignalEngine(bullish_threshold=-0.5, bearish_threshold=0.5)


class TestCohenMalloyPomorskiClassifier(unittest.TestCase):
    """CMP, *Decoding Inside Information*, JF 67(3) 2012, Section II."""

    @staticmethod
    def trade(name, year, month):
        return make_record(
            filing_id=f"{name}-{year}-{month}",
            insider_name=name,
            transaction_date=date(year, month, 10),
            filing_datetime=datetime(year, month, 12, 16, 0, tzinfo=ET),
        )

    def test_same_month_three_consecutive_years_is_routine(self):
        history = [self.trade("Bill", y, 3) for y in (2021, 2022, 2023)]
        labels = classify_trader_regularity(history, classification_year=2024)
        self.assertEqual(labels["Bill"], TraderRegularity.ROUTINE)

    def test_traded_all_three_years_but_never_the_same_month_is_opportunistic(self):
        history = [
            self.trade("Ann", 2021, 2),
            self.trade("Ann", 2022, 7),
            self.trade("Ann", 2023, 11),
        ]
        labels = classify_trader_regularity(history, classification_year=2024)
        self.assertEqual(labels["Ann"], TraderRegularity.OPPORTUNISTIC)

    def test_missing_a_year_leaves_the_insider_unclassified(self):
        # CMP "require an insider to make at least one trade in each of the
        # three preceding years in order to define her as either an
        # opportunistic or a routine trader".
        history = [self.trade("Sam", 2022, 4), self.trade("Sam", 2023, 4)]
        labels = classify_trader_regularity(history, classification_year=2024)
        self.assertEqual(labels["Sam"], TraderRegularity.UNCLASSIFIED)

    def test_classification_ignores_the_classification_year_itself(self):
        # Using the current year's trades to label the current year is
        # look-ahead: the label would depend on trades not yet made.
        history = [self.trade("Bill", y, 3) for y in (2022, 2023, 2024)]
        labels = classify_trader_regularity(history, classification_year=2024)
        self.assertEqual(labels.get("Bill"), TraderRegularity.UNCLASSIFIED)

    def test_lookback_years_must_be_positive(self):
        with self.assertRaises(InsiderFilingSignalError):
            classify_trader_regularity([], classification_year=2024, lookback_years=0)

    def test_engine_excludes_routine_traders_when_labels_supplied(self):
        history = [self.trade("Bill", y, 3) for y in (2021, 2022, 2023)]
        labels = classify_trader_regularity(history, classification_year=2024)

        routine_buy = make_record(filing_id="R", insider_name="Bill")
        opportunistic_buy = make_record(filing_id="O", insider_name="Ann")
        report = InsiderFilingSignalEngine().analyze_form4_filings(
            "AAPL", [routine_buy, opportunistic_buy],
            as_of=AS_OF, trader_regularity=labels,
        )
        self.assertEqual(report.routine_trader_filtered_count, 1)
        self.assertEqual(report.opportunistic_buys_count, 1)
        self.assertEqual(report.distinct_insiders_count, 1)


class TestAmendmentHandling(unittest.TestCase):

    def test_repeated_filing_id_warns_about_double_counting(self):
        # Form 4/A restates lines; a feed carrying both the original and the
        # amendment double-counts the same economic trade.
        original = make_record(filing_id="0001234567-24-000001")
        duplicate = make_record(filing_id="0001234567-24-000001")
        with self.assertLogs(
            "insider_transaction_filing_signal_research", level=logging.WARNING
        ) as captured:
            report = InsiderFilingSignalEngine().analyze_form4_filings(
                "AAPL", [original, duplicate], as_of=AS_OF
            )
        self.assertTrue(
            any("more than once" in line for line in captured.output), captured.output
        )
        self.assertEqual(report.opportunistic_buys_count, 2)


if __name__ == "__main__":
    unittest.main()
