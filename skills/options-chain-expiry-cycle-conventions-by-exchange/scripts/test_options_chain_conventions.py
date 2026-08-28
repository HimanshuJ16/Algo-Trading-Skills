"""Unit tests for the options chain expiry / settlement convention resolver.

Expected dates are derived independently of the implementation (from published
exchange calendars and from a separate ``calendar.Calendar.itermonthdates``
derivation), not by re-running the engine's own arithmetic.
"""
import calendar
import unittest
from datetime import date, timedelta

from options_chain_conventions import (
    ASSET_CLASS_DEFAULTS,
    CONTRACT_REGISTRY,
    DELIVERY_CASH,
    DELIVERY_FUTURES,
    DELIVERY_PHYSICAL,
    EXERCISE_AMERICAN,
    EXERCISE_EUROPEAN,
    RULE_LAST_FRIDAY,
    RULE_NOT_CALENDAR_DERIVABLE,
    RULE_THIRD_FRIDAY,
    RULE_VIX_30_DAY_WEDNESDAY,
    SETTLEMENT_AM,
    SETTLEMENT_AUCTION,
    SETTLEMENT_FIXED_TIME,
    SETTLEMENT_PM,
    OptionExpiryQuery,
    OptionsChainConventionReport,
    OptionsChainExpiryConventionsEngine,
    OptionsConventionError,
    UnknownContractError,
    UnsupportedCycleError,
)


def _fridays(year, month):
    """Independent derivation of a month's Fridays, via a different code path."""
    cal = calendar.Calendar()
    return [d for d in cal.itermonthdates(year, month)
            if d.month == month and d.weekday() == 4]


class TestExpiryArithmetic(unittest.TestCase):
    """Calendar rules, checked against independently derived dates."""

    def test_third_friday_known_dates(self):
        # Published Cboe standard monthly expirations.
        cases = {
            (2024, 1): "2024-01-19",
            (2024, 6): "2024-06-21",
            (2022, 4): "2022-04-15",   # five-Friday month
            (2025, 4): "2025-04-18",
            (2026, 3): "2026-03-20",
        }
        for (year, month), expected in cases.items():
            with self.subTest(year=year, month=month):
                self.assertEqual(
                    OptionsChainExpiryConventionsEngine.third_friday(year, month).isoformat(),
                    expected,
                )

    def test_third_friday_matches_independent_derivation_over_ten_years(self):
        for year in range(2020, 2030):
            for month in range(1, 13):
                with self.subTest(year=year, month=month):
                    self.assertEqual(
                        OptionsChainExpiryConventionsEngine.third_friday(year, month),
                        _fridays(year, month)[2],
                    )

    def test_third_friday_is_immune_to_calendar_module_global_state(self):
        # Regression: the previous implementation read week[calendar.FRIDAY] out
        # of calendar.monthcalendar(), whose column layout depends on the
        # process-global first weekday. Any library calling setfirstweekday()
        # silently shifted the result.
        original = calendar.firstweekday()
        try:
            calendar.setfirstweekday(calendar.SUNDAY)
            self.assertEqual(
                OptionsChainExpiryConventionsEngine.third_friday(2024, 1).isoformat(),
                "2024-01-19",
            )
            calendar.setfirstweekday(calendar.SATURDAY)
            self.assertEqual(
                OptionsChainExpiryConventionsEngine.third_friday(2022, 4).isoformat(),
                "2022-04-15",
            )
        finally:
            calendar.setfirstweekday(original)

    def test_get_third_friday_still_returns_a_datetime(self):
        # Preserved public API from v1.0.0.
        result = OptionsChainExpiryConventionsEngine.get_third_friday(2024, 6)
        self.assertEqual(result.strftime("%Y-%m-%d"), "2024-06-21")
        self.assertEqual((result.hour, result.minute, result.second), (0, 0, 0))

    def test_last_friday_known_dates(self):
        # 2026-03-27 is the published Deribit Q1 2026 expiry.
        cases = {
            (2026, 3): "2026-03-27",
            (2022, 4): "2022-04-29",   # third Friday was the 15th
            (2024, 1): "2024-01-26",
            (2026, 12): "2026-12-25",
        }
        for (year, month), expected in cases.items():
            with self.subTest(year=year, month=month):
                self.assertEqual(
                    OptionsChainExpiryConventionsEngine.last_friday(year, month).isoformat(),
                    expected,
                )

    def test_last_friday_matches_independent_derivation_and_handles_year_rollover(self):
        for year in range(2020, 2030):
            for month in range(1, 13):
                with self.subTest(year=year, month=month):
                    self.assertEqual(
                        OptionsChainExpiryConventionsEngine.last_friday(year, month),
                        _fridays(year, month)[-1],
                    )

    def test_vix_monthly_expiry_known_dates(self):
        cases = {
            (2025, 12): "2025-12-17",
            (2026, 1): "2026-01-21",
            (2026, 8): "2026-08-19",
        }
        for (year, month), expected in cases.items():
            with self.subTest(year=year, month=month):
                self.assertEqual(
                    OptionsChainExpiryConventionsEngine.vix_monthly_expiry(year, month).isoformat(),
                    expected,
                )

    def test_vix_expiry_is_always_a_wednesday_never_the_third_friday(self):
        for year in range(2022, 2030):
            for month in range(1, 13):
                expiry = OptionsChainExpiryConventionsEngine.vix_monthly_expiry(year, month)
                with self.subTest(year=year, month=month):
                    self.assertEqual(expiry.weekday(), 2, "VIX expiry must be a Wednesday")
                    self.assertNotEqual(
                        expiry,
                        OptionsChainExpiryConventionsEngine.third_friday(year, month),
                    )

    def test_vix_expiry_is_exactly_30_days_before_next_month_third_friday(self):
        for year, month in [(2026, 1), (2026, 7), (2026, 12)]:
            next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
            with self.subTest(year=year, month=month):
                self.assertEqual(
                    OptionsChainExpiryConventionsEngine.vix_monthly_expiry(year, month)
                    + timedelta(days=30),
                    _fridays(next_year, next_month)[2],
                )


class TestCboeIndexConventions(unittest.TestCase):

    def setUp(self):
        self.engine = OptionsChainExpiryConventionsEngine()

    def test_spx_monthly_am_settlement_convention(self):
        query = OptionExpiryQuery(
            exchange="CBOE",
            underlying_symbol="SPX",
            reference_date_iso="2024-01-01",
            target_year=2024,
            target_month=1,
            cycle_type="MONTHLY",
        )
        report = self.engine.resolve_conventions(query)
        self.assertEqual(report.expiration_date_iso, "2024-01-19")
        self.assertEqual(report.dte_days, 18)
        self.assertEqual(report.settlement_type, SETTLEMENT_AM)
        self.assertEqual(report.exercise_style, EXERCISE_EUROPEAN)
        self.assertEqual(report.delivery_type, DELIVERY_CASH)
        self.assertEqual(report.expiry_rule, RULE_THIRD_FRIDAY)
        self.assertFalse(report.is_expired)

    def test_am_settled_spx_last_trading_day_precedes_expiration_date(self):
        # Regression: v1.0.0 reported a single date and called it DTE, which
        # overstates the tradeable life of an AM-settled monthly by one day.
        # Cboe: trading ceases on the business day (usually Thursday) preceding
        # the day the exercise-settlement value is calculated.
        report = self.engine.resolve_conventions(
            OptionExpiryQuery("CBOE", "SPX", "2024-01-01", 2024, 1)
        )
        self.assertEqual(report.expiration_date_iso, "2024-01-19")  # Friday
        self.assertEqual(report.last_trading_date_iso, "2024-01-18")  # Thursday
        self.assertEqual(report.dte_to_last_trading_day, 17)
        self.assertLess(report.dte_to_last_trading_day, report.dte_days)

    def test_xsp_is_an_index_option_that_is_pm_settled(self):
        # Refutes the v1.0.0 rule "index options => AM_SETTLED". XSP is an
        # index option, European and cash-settled, but PM-settled.
        report = self.engine.resolve_conventions(
            OptionExpiryQuery("CBOE", "XSP", "2024-01-01", 2024, 1)
        )
        self.assertEqual(report.settlement_type, SETTLEMENT_PM)
        self.assertEqual(report.exercise_style, EXERCISE_EUROPEAN)
        self.assertEqual(report.delivery_type, DELIVERY_CASH)
        # PM-settled: the option trades through its expiration date.
        self.assertEqual(report.last_trading_date_iso, report.expiration_date_iso)

    def test_vix_monthly_does_not_resolve_to_the_third_friday(self):
        # Regression: v1.0.0 returned the third Friday for every symbol.
        report = self.engine.resolve_conventions(
            OptionExpiryQuery("CBOE", "VIX", "2026-01-01", 2026, 1)
        )
        self.assertEqual(report.expiration_date_iso, "2026-01-21")
        self.assertNotEqual(report.expiration_date_iso, "2026-01-16")
        self.assertEqual(report.expiry_rule, RULE_VIX_30_DAY_WEDNESDAY)
        self.assertEqual(report.settlement_type, SETTLEMENT_AM)
        self.assertEqual(report.last_trading_date_iso, "2026-01-20")

    def test_rut_and_ndx_monthlies_are_am_settled_european_cash(self):
        for symbol in ("RUT", "NDX"):
            with self.subTest(symbol=symbol):
                report = self.engine.resolve_conventions(
                    OptionExpiryQuery("CBOE", symbol, "2024-01-01", 2024, 1)
                )
                self.assertEqual(report.settlement_type, SETTLEMENT_AM)
                self.assertEqual(report.exercise_style, EXERCISE_EUROPEAN)
                self.assertEqual(report.delivery_type, DELIVERY_CASH)
                self.assertEqual(report.expiration_date_iso, "2024-01-19")


class TestNonDerivableSeries(unittest.TestCase):
    """Weekly / EOM series: conventions are known, the date is not derivable."""

    def setUp(self):
        self.engine = OptionsChainExpiryConventionsEngine()

    def test_spxw_weekly_cannot_be_resolved_from_year_and_month(self):
        # Regression: v1.0.0 accepted cycle_type='WEEKLY' and silently returned
        # the monthly third Friday while labelling the report WEEKLY.
        with self.assertRaises(UnsupportedCycleError):
            self.engine.resolve_conventions(
                OptionExpiryQuery("CBOE", "SPXW", "2024-01-01", 2024, 1, cycle_type="WEEKLY")
            )
        with self.assertRaises(UnsupportedCycleError):
            self.engine.resolve_conventions(
                OptionExpiryQuery("CBOE", "SPXW", "2024-01-01", 2024, 1, cycle_type="MONTHLY")
            )

    def test_spxw_conventions_are_still_available_without_a_date(self):
        convention = self.engine.get_contract_convention("CBOE", "SPXW")
        self.assertEqual(convention.settlement_type, SETTLEMENT_PM)
        self.assertEqual(convention.exercise_style, EXERCISE_EUROPEAN)
        self.assertEqual(convention.delivery_type, DELIVERY_CASH)
        self.assertEqual(convention.expiry_rule, RULE_NOT_CALENDAR_DERIVABLE)

    def test_ndxp_and_rutw_are_pm_settled_european_cash(self):
        for symbol in ("NDXP", "RUTW"):
            with self.subTest(symbol=symbol):
                convention = self.engine.get_contract_convention("CBOE", symbol)
                self.assertEqual(convention.settlement_type, SETTLEMENT_PM)
                self.assertEqual(convention.exercise_style, EXERCISE_EUROPEAN)
                self.assertEqual(convention.delivery_type, DELIVERY_CASH)


class TestNonCboeVenues(unittest.TestCase):

    def setUp(self):
        self.engine = OptionsChainExpiryConventionsEngine()

    def test_deribit_monthly_is_the_last_friday_not_the_third(self):
        # Regression: v1.0.0 ignored `exchange` and returned the Cboe third
        # Friday for every venue. Deribit monthlies expire the last Friday
        # at 08:00 UTC -- the published Q1 2026 expiry is 2026-03-27.
        report = self.engine.resolve_conventions(
            OptionExpiryQuery("DERIBIT", "BTC", "2026-03-01", 2026, 3)
        )
        self.assertEqual(report.expiration_date_iso, "2026-03-27")
        self.assertNotEqual(report.expiration_date_iso, "2026-03-20")  # third Friday
        self.assertEqual(report.expiry_rule, RULE_LAST_FRIDAY)
        self.assertEqual(report.settlement_type, SETTLEMENT_FIXED_TIME)
        self.assertEqual(report.exercise_style, EXERCISE_EUROPEAN)
        self.assertEqual(report.delivery_type, DELIVERY_CASH)

    def test_deribit_and_cboe_diverge_in_a_five_friday_month(self):
        deribit = self.engine.resolve_conventions(
            OptionExpiryQuery("DERIBIT", "ETH", "2022-04-01", 2022, 4)
        )
        cboe = self.engine.resolve_conventions(
            OptionExpiryQuery("CBOE", "SPX", "2022-04-01", 2022, 4)
        )
        self.assertEqual(deribit.expiration_date_iso, "2022-04-29")
        self.assertEqual(cboe.expiration_date_iso, "2022-04-15")

    def test_cme_es_quarterly_is_american_and_delivers_a_future(self):
        report = self.engine.resolve_conventions(
            OptionExpiryQuery("CME", "ES", "2026-01-02", 2026, 3, cycle_type="QUARTERLY")
        )
        self.assertEqual(report.expiration_date_iso, "2026-03-20")
        self.assertEqual(report.exercise_style, EXERCISE_AMERICAN)
        self.assertEqual(report.delivery_type, DELIVERY_FUTURES)
        self.assertEqual(report.settlement_type, SETTLEMENT_AM)

    def test_cme_es_rejects_a_monthly_cycle(self):
        # The European-style Third-Friday Monthly series is a different CME
        # product; resolving it under the quarterly symbol would report the
        # wrong exercise style.
        with self.assertRaises(UnsupportedCycleError):
            self.engine.resolve_conventions(
                OptionExpiryQuery("CME", "ES", "2026-01-02", 2026, 3, cycle_type="MONTHLY")
            )

    def test_eurex_odax_is_auction_settled_not_am_or_pm(self):
        report = self.engine.resolve_conventions(
            OptionExpiryQuery("EUREX", "ODAX", "2026-01-02", 2026, 3)
        )
        self.assertEqual(report.expiration_date_iso, "2026-03-20")
        self.assertEqual(report.settlement_type, SETTLEMENT_AUCTION)
        self.assertEqual(report.exercise_style, EXERCISE_EUROPEAN)
        self.assertEqual(report.delivery_type, DELIVERY_CASH)
        self.assertEqual(report.last_trading_date_iso, report.expiration_date_iso)


class TestHolidayAdjustment(unittest.TestCase):
    """Third Friday that is not a trading day rolls back one business day."""

    def test_good_friday_2025_rolls_expiry_back_to_thursday(self):
        # 18 April 2025 was both the third Friday and Good Friday; US equity
        # and index options expired Thursday 17 April 2025.
        engine = OptionsChainExpiryConventionsEngine(holiday_calendar=["2025-04-18"])
        report = engine.resolve_conventions(
            OptionExpiryQuery("CBOE", "XSP", "2025-04-01", 2025, 4)
        )
        self.assertEqual(report.expiration_date_iso, "2025-04-17")
        self.assertTrue(report.holiday_adjusted)
        self.assertTrue(report.holiday_calendar_applied)
        self.assertEqual(report.warnings, ())

    def test_good_friday_2022_rolls_expiry_back_to_thursday(self):
        engine = OptionsChainExpiryConventionsEngine(holiday_calendar=[date(2022, 4, 15)])
        report = engine.resolve_conventions(
            OptionExpiryQuery("CBOE", "XSP", "2022-04-01", 2022, 4)
        )
        self.assertEqual(report.expiration_date_iso, "2022-04-14")
        self.assertTrue(report.holiday_adjusted)

    def test_am_settled_last_trading_day_also_shifts_when_expiry_rolls_back(self):
        engine = OptionsChainExpiryConventionsEngine(holiday_calendar=["2025-04-18"])
        report = engine.resolve_conventions(
            OptionExpiryQuery("CBOE", "SPX", "2025-04-01", 2025, 4)
        )
        self.assertEqual(report.expiration_date_iso, "2025-04-17")   # Thursday
        self.assertEqual(report.last_trading_date_iso, "2025-04-16")  # Wednesday

    def test_consecutive_closures_roll_back_further(self):
        engine = OptionsChainExpiryConventionsEngine(
            holiday_calendar=["2025-04-18", "2025-04-17"]
        )
        report = engine.resolve_conventions(
            OptionExpiryQuery("CBOE", "XSP", "2025-04-01", 2025, 4)
        )
        self.assertEqual(report.expiration_date_iso, "2025-04-16")

    def test_no_calendar_means_no_adjustment_but_an_explicit_warning(self):
        # The module must not invent a holiday calendar, but it must not stay
        # silent about the resulting uncertainty either.
        engine = OptionsChainExpiryConventionsEngine()
        report = engine.resolve_conventions(
            OptionExpiryQuery("CBOE", "XSP", "2025-04-01", 2025, 4)
        )
        self.assertEqual(report.expiration_date_iso, "2025-04-18")
        self.assertFalse(report.holiday_calendar_applied)
        self.assertFalse(report.holiday_adjusted)
        self.assertTrue(report.warnings)
        self.assertIn("holiday", report.warnings[0].lower())

    def test_supplying_a_calendar_clears_the_unverified_warning(self):
        engine = OptionsChainExpiryConventionsEngine(holiday_calendar=["2025-01-01"])
        report = engine.resolve_conventions(
            OptionExpiryQuery("CBOE", "SPX", "2025-04-01", 2025, 4)
        )
        self.assertEqual(report.warnings, ())
        self.assertEqual(report.expiration_date_iso, "2025-04-18")

    def test_deribit_is_never_holiday_adjusted(self):
        # Deribit trades continuously; rolling an expiry off a US or European
        # market holiday would itself introduce the error.
        engine = OptionsChainExpiryConventionsEngine(
            holiday_calendar=["2025-04-25", "2022-04-29"]
        )
        report = engine.resolve_conventions(
            OptionExpiryQuery("DERIBIT", "BTC", "2025-04-01", 2025, 4)
        )
        self.assertEqual(report.expiration_date_iso, "2025-04-25")
        self.assertFalse(report.holiday_adjusted)
        self.assertEqual(report.warnings, ())

    def test_per_exchange_calendars_do_not_leak_across_venues(self):
        # A US calendar must not silently adjust a Eurex expiry, and vice versa.
        engine = OptionsChainExpiryConventionsEngine(
            holiday_calendar={"CBOE": ["2025-04-18"]}
        )
        cboe = engine.resolve_conventions(
            OptionExpiryQuery("CBOE", "XSP", "2025-04-01", 2025, 4)
        )
        eurex = engine.resolve_conventions(
            OptionExpiryQuery("EUREX", "ODAX", "2025-04-01", 2025, 4)
        )
        self.assertEqual(cboe.expiration_date_iso, "2025-04-17")
        self.assertTrue(cboe.holiday_adjusted)
        # Eurex was not covered by the mapping, so it is reported as unverified
        # rather than adjusted using someone else's calendar.
        self.assertEqual(eurex.expiration_date_iso, "2025-04-18")
        self.assertFalse(eurex.holiday_calendar_applied)
        self.assertTrue(eurex.warnings)
        self.assertIn("EUREX", eurex.warnings[0])

    def test_mapping_form_adjusts_each_venue_with_its_own_calendar(self):
        engine = OptionsChainExpiryConventionsEngine(
            holiday_calendar={"CBOE": ["2025-04-18"], "EUREX": ["2025-04-18"]}
        )
        eurex = engine.resolve_conventions(
            OptionExpiryQuery("EUREX", "ODAX", "2025-04-01", 2025, 4)
        )
        self.assertEqual(eurex.expiration_date_iso, "2025-04-17")
        self.assertTrue(eurex.holiday_adjusted)
        self.assertEqual(eurex.warnings, ())

    def test_holiday_calendar_accepts_strings_and_date_objects_alike(self):
        by_string = OptionsChainExpiryConventionsEngine(holiday_calendar=["2025-04-18"])
        by_date = OptionsChainExpiryConventionsEngine(holiday_calendar=[date(2025, 4, 18)])
        query = OptionExpiryQuery("CBOE", "XSP", "2025-04-01", 2025, 4)
        self.assertEqual(
            by_string.resolve_conventions(query).expiration_date_iso,
            by_date.resolve_conventions(query).expiration_date_iso,
        )


class TestFailClosedOnUnknownContracts(unittest.TestCase):
    """v1.0.0 silently defaulted every unrecognised symbol to AMERICAN/PHYSICAL."""

    def setUp(self):
        self.engine = OptionsChainExpiryConventionsEngine()

    def test_unregistered_symbol_without_declared_asset_class_raises(self):
        with self.assertRaises(UnknownContractError):
            self.engine.resolve_conventions(
                OptionExpiryQuery("CBOE", "AAPL", "2024-01-01", 2024, 1)
            )

    def test_unregistered_index_symbol_is_not_guessed_as_american_physical(self):
        # An unrecognised cash-settled European index option must not come back
        # as an American physically-settled equity option.
        with self.assertRaises(UnknownContractError):
            self.engine.get_contract_convention("CBOE", "MRUT")
        with self.assertRaises(UnknownContractError):
            self.engine.get_contract_convention("EUREX", "OESX")

    def test_declared_equity_resolves_to_american_physical_pm(self):
        report = self.engine.resolve_conventions(
            OptionExpiryQuery(
                "CBOE", "AAPL", "2024-01-01", 2024, 1, asset_class="EQUITY"
            )
        )
        self.assertEqual(report.expiration_date_iso, "2024-01-19")
        self.assertEqual(report.settlement_type, SETTLEMENT_PM)
        self.assertEqual(report.exercise_style, EXERCISE_AMERICAN)
        self.assertEqual(report.delivery_type, DELIVERY_PHYSICAL)
        self.assertEqual(report.last_trading_date_iso, "2024-01-19")

    def test_declared_asset_class_never_overrides_a_registered_contract(self):
        report = self.engine.resolve_conventions(
            OptionExpiryQuery(
                "CBOE", "SPX", "2024-01-01", 2024, 1, asset_class="EQUITY"
            )
        )
        self.assertEqual(report.exercise_style, EXERCISE_EUROPEAN)
        self.assertEqual(report.delivery_type, DELIVERY_CASH)

    def test_unknown_asset_class_raises(self):
        with self.assertRaises(UnknownContractError):
            self.engine.get_contract_convention("CBOE", "AAPL", asset_class="WARRANT")
        with self.assertRaises(UnknownContractError):
            self.engine.get_contract_convention("DERIBIT", "SOL", asset_class="EQUITY")


class TestDteSemantics(unittest.TestCase):

    def setUp(self):
        self.engine = OptionsChainExpiryConventionsEngine()

    def test_dte_is_signed_for_an_already_expired_contract(self):
        # Regression: v1.0.0 used max(0, ...), so an expired contract was
        # indistinguishable from one expiring today.
        report = self.engine.resolve_conventions(
            OptionExpiryQuery("CBOE", "SPX", "2024-02-01", 2024, 1)
        )
        self.assertEqual(report.expiration_date_iso, "2024-01-19")
        self.assertEqual(report.dte_days, -13)
        self.assertTrue(report.is_expired)

    def test_zero_dte_on_the_expiration_date_itself(self):
        report = self.engine.resolve_conventions(
            OptionExpiryQuery("CBOE", "XSP", "2024-01-19", 2024, 1)
        )
        self.assertEqual(report.dte_days, 0)
        self.assertFalse(report.is_expired)

    def test_am_settled_contract_is_already_untradeable_on_its_expiration_date(self):
        report = self.engine.resolve_conventions(
            OptionExpiryQuery("CBOE", "SPX", "2024-01-19", 2024, 1)
        )
        self.assertEqual(report.dte_days, 0)
        self.assertEqual(report.dte_to_last_trading_day, -1)


class TestCycleValidation(unittest.TestCase):

    def setUp(self):
        self.engine = OptionsChainExpiryConventionsEngine()

    def test_quarterly_rejected_outside_the_quarterly_months(self):
        with self.assertRaises(UnsupportedCycleError):
            self.engine.resolve_conventions(
                OptionExpiryQuery("CBOE", "SPX", "2024-01-01", 2024, 1, cycle_type="QUARTERLY")
            )

    def test_quarterly_accepted_in_a_quarterly_month(self):
        report = self.engine.resolve_conventions(
            OptionExpiryQuery("CBOE", "SPX", "2024-01-01", 2024, 3, cycle_type="QUARTERLY")
        )
        self.assertEqual(report.expiration_date_iso, "2024-03-15")
        self.assertEqual(report.cycle_type, "QUARTERLY")

    def test_leaps_uses_the_same_third_friday_anchor(self):
        report = self.engine.resolve_conventions(
            OptionExpiryQuery("CBOE", "SPX", "2024-01-02", 2027, 1, cycle_type="LEAPS")
        )
        self.assertEqual(report.expiration_date_iso, "2027-01-15")
        self.assertEqual(report.cycle_type, "LEAPS")

    def test_vix_rejects_quarterly_and_leaps(self):
        for cycle in ("QUARTERLY", "LEAPS"):
            with self.subTest(cycle=cycle):
                with self.assertRaises(UnsupportedCycleError):
                    self.engine.resolve_conventions(
                        OptionExpiryQuery("CBOE", "VIX", "2026-01-01", 2026, 3, cycle_type=cycle)
                    )

    def test_cycle_and_symbol_are_case_and_whitespace_insensitive(self):
        report = self.engine.resolve_conventions(
            OptionExpiryQuery(" cboe ", " spx ", "2024-01-01", 2024, 1, cycle_type=" monthly ")
        )
        self.assertEqual(report.exchange, "CBOE")
        self.assertEqual(report.underlying_symbol, "SPX")
        self.assertEqual(report.cycle_type, "MONTHLY")


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = OptionsChainExpiryConventionsEngine()

    def test_malformed_reference_date_raises_a_typed_error(self):
        for bad in ("2024-13-01", "01/01/2024", "", "2024-1-1x", "not-a-date"):
            with self.subTest(bad=bad):
                with self.assertRaises(OptionsConventionError):
                    self.engine.resolve_conventions(
                        OptionExpiryQuery("CBOE", "SPX", bad, 2024, 1)
                    )

    def test_out_of_range_month_raises(self):
        for month in (0, 13, -1, 99):
            with self.subTest(month=month):
                with self.assertRaises(OptionsConventionError):
                    self.engine.resolve_conventions(
                        OptionExpiryQuery("CBOE", "SPX", "2024-01-01", 2024, month)
                    )

    def test_non_integer_year_or_month_raises(self):
        with self.assertRaises(OptionsConventionError):
            OptionsChainExpiryConventionsEngine.third_friday("2024", 1)
        with self.assertRaises(OptionsConventionError):
            OptionsChainExpiryConventionsEngine.third_friday(2024, 1.0)
        with self.assertRaises(OptionsConventionError):
            OptionsChainExpiryConventionsEngine.third_friday(2024, True)

    def test_empty_exchange_or_symbol_raises(self):
        with self.assertRaises(OptionsConventionError):
            self.engine.get_contract_convention("", "SPX")
        with self.assertRaises(OptionsConventionError):
            self.engine.get_contract_convention("CBOE", "   ")

    def test_empty_cycle_type_raises(self):
        with self.assertRaises(OptionsConventionError):
            self.engine.resolve_conventions(
                OptionExpiryQuery("CBOE", "SPX", "2024-01-01", 2024, 1, cycle_type="  ")
            )

    def test_malformed_holiday_calendar_entry_raises_at_construction(self):
        with self.assertRaises(OptionsConventionError):
            OptionsChainExpiryConventionsEngine(holiday_calendar=["2025-04-31"])


class TestRegistryIntegrity(unittest.TestCase):
    """The registry is reference data; these guard it against silent drift."""

    def test_every_entry_carries_a_source_and_an_as_of_date(self):
        for key, convention in CONTRACT_REGISTRY.items():
            with self.subTest(contract=key):
                self.assertTrue(convention.source.strip(), f"{key} has no source")
                self.assertRegex(convention.source_as_of, r"^\d{4}-\d{2}$")
                self.assertTrue(convention.settlement_basis.strip())

    def test_registry_key_matches_the_entry_it_holds(self):
        for (exchange, symbol), convention in CONTRACT_REGISTRY.items():
            with self.subTest(contract=(exchange, symbol)):
                self.assertEqual(convention.exchange, exchange)
                self.assertEqual(convention.symbol, symbol)

    def test_supported_cycles_are_empty_exactly_when_the_rule_is_not_derivable(self):
        for key, convention in CONTRACT_REGISTRY.items():
            with self.subTest(contract=key):
                derivable = convention.expiry_rule != RULE_NOT_CALENDAR_DERIVABLE
                self.assertEqual(derivable, bool(convention.supported_cycles))

    def test_every_derivable_entry_actually_resolves(self):
        engine = OptionsChainExpiryConventionsEngine()
        for (exchange, symbol), convention in CONTRACT_REGISTRY.items():
            if not convention.supported_cycles:
                continue
            cycle = "MONTHLY" if "MONTHLY" in convention.supported_cycles else "QUARTERLY"
            with self.subTest(contract=(exchange, symbol)):
                report = engine.resolve_conventions(
                    OptionExpiryQuery(exchange, symbol, "2026-01-02", 2026, 3, cycle_type=cycle)
                )
                self.assertIsInstance(report, OptionsChainConventionReport)
                self.assertRegex(report.expiration_date_iso, r"^\d{4}-\d{2}-\d{2}$")
                self.assertLessEqual(report.last_trading_date_iso, report.expiration_date_iso)

    def test_only_continuously_traded_venues_opt_out_of_holiday_adjustment(self):
        for (exchange, symbol), convention in CONTRACT_REGISTRY.items():
            with self.subTest(contract=(exchange, symbol)):
                self.assertEqual(
                    convention.observes_exchange_holidays,
                    exchange != "DERIBIT",
                )

    def test_asset_class_defaults_are_us_equity_style(self):
        for key, convention in ASSET_CLASS_DEFAULTS.items():
            with self.subTest(asset_class=key):
                self.assertEqual(convention.exercise_style, EXERCISE_AMERICAN)
                self.assertEqual(convention.delivery_type, DELIVERY_PHYSICAL)
                self.assertEqual(convention.expiry_rule, RULE_THIRD_FRIDAY)

    def test_a_custom_registry_can_be_injected(self):
        engine = OptionsChainExpiryConventionsEngine(
            registry={("CBOE", "SPX"): CONTRACT_REGISTRY[("DERIBIT", "BTC")]}
        )
        with self.assertRaises(UnknownContractError):
            engine.get_contract_convention("DERIBIT", "BTC")


class TestAuditTrail(unittest.TestCase):

    def test_report_carries_provenance_for_a_compliance_trail(self):
        engine = OptionsChainExpiryConventionsEngine(holiday_calendar=["2025-04-18"])
        report = engine.resolve_conventions(
            OptionExpiryQuery("CBOE", "SPX", "2025-04-01", 2025, 4)
        )
        self.assertEqual(report.reference_date_iso, "2025-04-01")
        self.assertIn("cboe.com", report.source)
        self.assertIn("Special Opening Quotation", report.settlement_basis)
        self.assertIn("holiday-adjusted", report.audit_notes)
        self.assertIn("2025-04-17", report.audit_notes)


if __name__ == "__main__":
    unittest.main()
