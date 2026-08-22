import datetime
import unittest
from decimal import Decimal
from zoneinfo import ZoneInfo

from capital_gains_vs_business_income_classification import (
    AssetClass,
    ClosedTrade,
    Jurisdiction,
    TaxCategory,
    TaxClassificationEngine,
    TaxClassificationError,
    TaxElections,
    _add_months,
    _held_longer_than_months,
)

IST = ZoneInfo("Asia/Kolkata")
ET = ZoneInfo("America/New_York")


def make_trade(**overrides):
    """A delivery-based listed equity trade, overridable field by field."""
    defaults = dict(
        trade_id="T1",
        symbol="ACME",
        asset_class=AssetClass.EQUITY,
        open_time=datetime.datetime(2025, 1, 1, 10, 0),
        close_time=datetime.datetime(2025, 1, 10, 10, 0),
        net_pnl=Decimal("1000"),
        settled_without_delivery=False,
    )
    defaults.update(overrides)
    return ClosedTrade(**defaults)


class TestHoldingPeriodArithmetic(unittest.TestCase):
    """The holding-period test is calendar-based and strictly 'more than'."""

    def test_add_months_clamps_to_shorter_month(self):
        self.assertEqual(
            _add_months(datetime.date(2024, 2, 29), 12), datetime.date(2025, 2, 28))
        self.assertEqual(
            _add_months(datetime.date(2024, 1, 31), 1), datetime.date(2024, 2, 29))
        self.assertEqual(
            _add_months(datetime.date(2024, 12, 15), 12), datetime.date(2025, 12, 15))

    def test_anniversary_date_is_not_yet_long_term(self):
        # IRS Topic 409: held "more than one year". Bought 1 Jan, sold 1 Jan the
        # next year is exactly one year -- short-term. One day later it flips.
        bought = datetime.date(2024, 1, 1)
        self.assertFalse(
            _held_longer_than_months(bought, datetime.date(2025, 1, 1), 12))
        self.assertTrue(
            _held_longer_than_months(bought, datetime.date(2025, 1, 2), 12))

    def test_365_days_across_a_leap_year_is_not_twelve_months(self):
        # Regression: a fixed `holding_days >= 365` test called this long-term.
        # 1 Jan 2024 -> 31 Dec 2024 is 365 days but less than 12 calendar months.
        bought = datetime.date(2024, 1, 1)
        sold = datetime.date(2024, 12, 31)
        self.assertEqual((sold - bought).days, 365)
        self.assertFalse(_held_longer_than_months(bought, sold, 12))


class TestIndia(unittest.TestCase):

    def setUp(self):
        self.engine = TaxClassificationEngine(Jurisdiction.INDIA)

    def test_exchange_traded_derivative_is_non_speculative(self):
        trade = make_trade(asset_class=AssetClass.DERIVATIVE, is_listed=True)
        self.assertEqual(
            self.engine.classify_trade(trade), TaxCategory.NON_SPECULATIVE_BUSINESS)

    def test_off_exchange_derivative_is_speculative(self):
        # s.43(5) proviso (d) only carves out trading on a recognised stock
        # exchange; an OTC derivative stays inside the main limb.
        trade = make_trade(asset_class=AssetClass.DERIVATIVE, is_listed=False)
        with self.assertLogs(
            "capital_gains_vs_business_income_classification", level="WARNING"
        ) as logs:
            category = self.engine.classify_trade(trade)
        self.assertEqual(category, TaxCategory.SPECULATIVE_BUSINESS)
        self.assertIn("recognised stock exchange", logs.output[0])

    def test_equity_settled_without_delivery_is_speculative(self):
        trade = make_trade(settled_without_delivery=True)
        self.assertEqual(
            self.engine.classify_trade(trade), TaxCategory.SPECULATIVE_BUSINESS)

    def test_delivery_flag_overrides_the_same_day_proxy(self):
        # A same-session round trip that *did* settle by delivery is not
        # speculative, whatever the timestamps say.
        trade = make_trade(
            open_time=datetime.datetime(2025, 1, 1, 10, 0),
            close_time=datetime.datetime(2025, 1, 1, 14, 0),
            settled_without_delivery=False,
        )
        self.assertEqual(
            self.engine.classify_trade(trade), TaxCategory.SHORT_TERM_CAPITAL_GAINS)

    def test_same_day_proxy_used_and_warned_when_flag_absent(self):
        trade = make_trade(
            open_time=datetime.datetime(2025, 1, 1, 10, 0),
            close_time=datetime.datetime(2025, 1, 1, 14, 0),
            settled_without_delivery=None,
        )
        with self.assertLogs(
            "capital_gains_vs_business_income_classification", level="WARNING"
        ) as logs:
            category = self.engine.classify_trade(trade)
        self.assertEqual(category, TaxCategory.SPECULATIVE_BUSINESS)
        self.assertIn("settled_without_delivery", logs.output[0])

    def test_listed_equity_long_term_boundary(self):
        # More than 12 months, s.2(42A).
        base = dict(open_time=datetime.datetime(2024, 1, 1, 10, 0), is_listed=True)
        exactly_twelve = make_trade(
            close_time=datetime.datetime(2025, 1, 1, 10, 0), **base)
        one_day_more = make_trade(
            close_time=datetime.datetime(2025, 1, 2, 10, 0), **base)
        self.assertEqual(
            self.engine.classify_trade(exactly_twelve),
            TaxCategory.SHORT_TERM_CAPITAL_GAINS)
        self.assertEqual(
            self.engine.classify_trade(one_day_more),
            TaxCategory.LONG_TERM_CAPITAL_GAINS)

    def test_unlisted_equity_uses_twenty_four_month_threshold(self):
        # Finance (No. 2) Act, 2024: 24 months for assets other than listed
        # securities. An 18-month hold is long-term if listed, short if not.
        base = dict(
            open_time=datetime.datetime(2023, 1, 1, 10, 0),
            close_time=datetime.datetime(2024, 7, 1, 10, 0),
        )
        self.assertEqual(
            self.engine.classify_trade(make_trade(is_listed=True, **base)),
            TaxCategory.LONG_TERM_CAPITAL_GAINS)
        self.assertEqual(
            self.engine.classify_trade(make_trade(is_listed=False, **base)),
            TaxCategory.SHORT_TERM_CAPITAL_GAINS)

    def test_stock_in_trade_election_overrides_holding_period(self):
        # CBDT Circular No. 6/2016: stock-in-trade treatment applies
        # irrespective of the period of holding.
        engine = TaxClassificationEngine(
            Jurisdiction.INDIA,
            TaxElections(india_equity_as_stock_in_trade=True),
        )
        trade = make_trade(
            open_time=datetime.datetime(2020, 1, 1, 10, 0),
            close_time=datetime.datetime(2025, 1, 1, 10, 0),
        )
        self.assertEqual(
            engine.classify_trade(trade), TaxCategory.NON_SPECULATIVE_BUSINESS)


class TestUnitedStates(unittest.TestCase):
    """The US tax code has no speculative-business bucket."""

    def setUp(self):
        self.engine = TaxClassificationEngine(Jurisdiction.UNITED_STATES)

    def test_intraday_equity_is_short_term_capital_not_speculative(self):
        # Regression: the previous engine returned SPECULATIVE_BUSINESS, a
        # category that does not exist in US tax law.
        trade = make_trade(
            open_time=datetime.datetime(2025, 3, 3, 10, 0, tzinfo=ET),
            close_time=datetime.datetime(2025, 3, 3, 15, 0, tzinfo=ET),
            settled_without_delivery=True,
        )
        self.assertEqual(
            self.engine.classify_trade(trade), TaxCategory.SHORT_TERM_CAPITAL_GAINS)

    def test_section_1256_contract_is_not_business_income(self):
        # Regression: the previous engine returned NON_SPECULATIVE_BUSINESS for
        # every derivative regardless of jurisdiction.
        trade = make_trade(
            asset_class=AssetClass.DERIVATIVE, is_section_1256_contract=True)
        self.assertEqual(
            self.engine.classify_trade(trade), TaxCategory.SECTION_1256_60_40)

    def test_non_1256_option_is_an_ordinary_capital_asset(self):
        trade = make_trade(
            asset_class=AssetClass.DERIVATIVE, is_section_1256_contract=False)
        self.assertEqual(
            self.engine.classify_trade(trade), TaxCategory.SHORT_TERM_CAPITAL_GAINS)

    def test_section_475f_election_makes_equity_ordinary_income(self):
        engine = TaxClassificationEngine(
            Jurisdiction.UNITED_STATES,
            TaxElections(us_section_475f_elected=True),
        )
        trade = make_trade(
            open_time=datetime.datetime(2020, 1, 1, 10, 0),
            close_time=datetime.datetime(2025, 1, 1, 10, 0),
        )
        self.assertEqual(engine.classify_trade(trade), TaxCategory.BUSINESS_INCOME)

    def test_one_year_hold_is_short_term(self):
        trade = make_trade(
            open_time=datetime.datetime(2024, 5, 10, 10, 0),
            close_time=datetime.datetime(2025, 5, 10, 10, 0),
        )
        self.assertEqual(
            self.engine.classify_trade(trade), TaxCategory.SHORT_TERM_CAPITAL_GAINS)

    def test_utc_timestamps_of_one_session_are_not_an_overnight_hold(self):
        # A 15:30 ET close is 20:30 UTC on the same day, but a 20:00 ET close
        # would be 01:00 UTC the next day. Session dates must be exchange-local.
        engine = TaxClassificationEngine(Jurisdiction.UNITED_STATES)
        utc = datetime.timezone.utc
        trade = make_trade(
            open_time=datetime.datetime(2025, 3, 3, 14, 35, tzinfo=utc),
            close_time=datetime.datetime(2025, 3, 4, 0, 30, tzinfo=utc),
            settled_without_delivery=True,
        )
        self.assertEqual(engine.explain_trade(trade).holding_days, 0)


class TestCanada(unittest.TestCase):
    """Canada has no holding-period split at all."""

    def setUp(self):
        self.engine = TaxClassificationEngine(Jurisdiction.CANADA)

    def test_long_hold_is_not_split_into_long_term(self):
        # Regression: the previous engine emitted LONG_TERM_CAPITAL_GAINS, a
        # distinction Canadian tax law does not draw.
        trade = make_trade(
            open_time=datetime.datetime(2018, 1, 1, 10, 0),
            close_time=datetime.datetime(2025, 1, 1, 10, 0),
        )
        self.assertEqual(self.engine.classify_trade(trade), TaxCategory.BUSINESS_INCOME)

    def test_section_39_4_election_gives_plain_capital_gains(self):
        engine = TaxClassificationEngine(
            Jurisdiction.CANADA, TaxElections(canada_section_39_4_elected=True))
        self.assertEqual(
            engine.classify_trade(make_trade()), TaxCategory.CAPITAL_GAINS)

    def test_futures_default_to_income_account(self):
        trade = make_trade(asset_class=AssetClass.DERIVATIVE)
        self.assertEqual(self.engine.classify_trade(trade), TaxCategory.BUSINESS_INCOME)

    def test_speculator_may_report_futures_on_capital_account(self):
        engine = TaxClassificationEngine(
            Jurisdiction.CANADA,
            TaxElections(canada_derivatives_on_capital_account=True),
        )
        trade = make_trade(asset_class=AssetClass.DERIVATIVE)
        self.assertEqual(engine.classify_trade(trade), TaxCategory.CAPITAL_GAINS)

    def test_aggregate_only_exposes_canadian_buckets(self):
        summary = self.engine.aggregate_pnl([make_trade()])
        self.assertEqual(
            set(summary), {TaxCategory.CAPITAL_GAINS, TaxCategory.BUSINESS_INCOME})


class TestAggregationAndValidation(unittest.TestCase):

    def setUp(self):
        self.engine = TaxClassificationEngine(Jurisdiction.INDIA)

    def test_aggregate_sums_exactly_without_float_drift(self):
        trades = [
            make_trade(trade_id=f"T{i}", net_pnl="0.1", settled_without_delivery=True)
            for i in range(10)
        ]
        summary = self.engine.aggregate_pnl(trades)
        self.assertEqual(summary[TaxCategory.SPECULATIVE_BUSINESS], Decimal("1.0"))

    def test_float_pnl_is_accepted_and_normalised(self):
        result = self.engine.explain_trade(make_trade(net_pnl=1234.56))
        self.assertEqual(result.net_pnl, Decimal("1234.56"))

    def test_classify_portfolio_preserves_order(self):
        trades = [make_trade(trade_id="A"), make_trade(trade_id="B")]
        self.assertEqual(
            [c.trade_id for c in self.engine.classify_portfolio(trades)], ["A", "B"])

    def test_negative_holding_period_rejected(self):
        trade = make_trade(
            open_time=datetime.datetime(2025, 1, 10, 10, 0),
            close_time=datetime.datetime(2025, 1, 1, 10, 0),
        )
        with self.assertRaises(TaxClassificationError):
            self.engine.classify_trade(trade)

    def test_mixed_timezone_awareness_rejected(self):
        trade = make_trade(
            open_time=datetime.datetime(2025, 1, 1, 10, 0, tzinfo=IST),
            close_time=datetime.datetime(2025, 1, 10, 10, 0),
        )
        with self.assertRaises(TaxClassificationError):
            self.engine.classify_trade(trade)

    def test_nan_pnl_rejected(self):
        with self.assertRaises(TaxClassificationError):
            self.engine.classify_trade(make_trade(net_pnl=float("nan")))

    def test_infinite_pnl_rejected(self):
        with self.assertRaises(TaxClassificationError):
            self.engine.classify_trade(make_trade(net_pnl=float("inf")))

    def test_unknown_asset_class_rejected(self):
        with self.assertRaises(TaxClassificationError):
            self.engine.classify_trade(make_trade(asset_class="EQUITY"))

    def test_empty_trade_id_rejected(self):
        with self.assertRaises(TaxClassificationError):
            self.engine.classify_trade(make_trade(trade_id=""))

    def test_jurisdiction_must_be_explicit_enum_member(self):
        with self.assertRaises(TaxClassificationError):
            TaxClassificationEngine("India")


if __name__ == "__main__":
    unittest.main()
