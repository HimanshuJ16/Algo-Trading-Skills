"""
Unit tests for forex-broker-integration-oanda-mt5.

Covers:
1. Pip sizing from broker instrument metadata (OANDA pipLocation, MT5 digits).
2. The name-based pip heuristic, and its refusal to guess non-FX instruments.
3. Pip valuation in the account currency, including the cross-currency case.
4. Overnight swap accrual and value-date-driven triple-swap rollovers.
5. MT5 terminal liveness monitoring.
6. OANDA practice/live host isolation and lot/unit conversion.

Expected values for the quantitative cases are derived independently of the
implementation (worked by hand in the comments) rather than restating its
formulas.
"""
import datetime as dt
import logging
import unittest

from pip_conversion import (
    ForexPipEngine,
    InstrumentSpec,
    MT5BridgeMonitor,
    SwapRolloverCalculator,
    UnknownInstrumentError,
    count_triple_swap_rollovers,
    infer_pip_location,
    lots_to_units,
    mt5_terminal_connected_check,
    oanda_hosts,
    pip_size,
    price_diff_to_pips,
    triple_swap_weekday,
    units_to_lots,
)

# Broker swap rates are inputs, never defaults. These are illustrative fixture
# values, not rates published by any broker.
FIXTURE_SWAP_RATES = {
    "EURUSD": {"long": -5.20, "short": 1.50},
    "USDJPY": {"long": 8.40, "short": -14.10},
}


class _FakeMT5SymbolInfo:
    def __init__(self, name, digits):
        self.name = name
        self.digits = digits


class _FakeTerminalInfo:
    def __init__(self, connected, trade_allowed):
        self.connected = connected
        self.trade_allowed = trade_allowed


class _FakeMT5Module:
    def __init__(self, terminal_info):
        self._terminal_info = terminal_info

    def terminal_info(self):
        return self._terminal_info


class TestPipSizingFromBrokerMetadata(unittest.TestCase):
    """Pip size must come from broker metadata, not from the instrument name."""

    def test_oanda_instrument_payload_drives_pip_size(self):
        eur_usd = InstrumentSpec.from_oanda_instrument(
            {"name": "EUR_USD", "pipLocation": -4, "displayPrecision": 5}
        )
        usd_jpy = InstrumentSpec.from_oanda_instrument(
            {"name": "USD_JPY", "pipLocation": -2, "displayPrecision": 3}
        )
        self.assertAlmostEqual(eur_usd.pip_size, 0.0001)
        self.assertAlmostEqual(usd_jpy.pip_size, 0.01)
        self.assertEqual(usd_jpy.quote_currency, "JPY")
        self.assertEqual(usd_jpy.base_currency, "USD")

    def test_oanda_cfd_with_pip_location_zero(self):
        # OANDA reports pipLocation 0 for some CFDs -- a pip of 1.0, which no
        # name-based heuristic could produce.
        index_cfd = InstrumentSpec.from_oanda_instrument(
            {"name": "EU50_EUR", "pipLocation": 0, "displayPrecision": 1}
        )
        self.assertAlmostEqual(index_cfd.pip_size, 1.0)
        self.assertAlmostEqual(ForexPipEngine.pip_size("EU50_EUR", index_cfd), 1.0)

    def test_oanda_payload_missing_pip_location_raises(self):
        with self.assertRaises(UnknownInstrumentError):
            InstrumentSpec.from_oanda_instrument({"name": "EUR_USD"})

    def test_mt5_digits_drive_pip_size(self):
        # 5-digit and 3-digit quotes are priced in pipettes, so the pip sits one
        # decimal place left of the last digit.
        five_digit = InstrumentSpec.from_mt5_symbol_info(_FakeMT5SymbolInfo("EURUSD", 5))
        three_digit = InstrumentSpec.from_mt5_symbol_info(_FakeMT5SymbolInfo("USDJPY", 3))
        four_digit = InstrumentSpec.from_mt5_symbol_info(_FakeMT5SymbolInfo("EURUSD", 4))
        self.assertAlmostEqual(five_digit.pip_size, 0.0001)
        self.assertAlmostEqual(three_digit.pip_size, 0.01)
        self.assertAlmostEqual(four_digit.pip_size, 0.0001)

    def test_mt5_symbol_info_missing_digits_raises(self):
        with self.assertRaises(UnknownInstrumentError):
            InstrumentSpec.from_mt5_symbol_info(_FakeMT5SymbolInfo("EURUSD", None))


class TestPipNameHeuristic(unittest.TestCase):
    def test_infers_fx_conventions_and_warns(self):
        with self.assertLogs("pip_conversion", level=logging.WARNING):
            self.assertEqual(infer_pip_location("EUR/USD"), -4)
        with self.assertLogs("pip_conversion", level=logging.WARNING):
            self.assertEqual(infer_pip_location("USD_JPY"), -2)

    def test_jpy_as_base_currency_is_not_treated_as_jpy_quoted(self):
        # Regression: substring matching on "JPY" gave a 0.01 pip to any
        # instrument containing JPY, including JPY-base pairs whose pip is
        # decided by the quote currency.
        with self.assertLogs("pip_conversion", level=logging.WARNING):
            self.assertEqual(infer_pip_location("JPYUSD"), -4)

    def test_refuses_to_guess_non_fx_instruments(self):
        # Regression: gold and index CFDs previously got a silently invented pip
        # size. Metal, index and crypto pip definitions vary by broker and must
        # come from instrument metadata. XAU_USD and BTCUSD are the sharp cases:
        # both are six alphabetic characters, so a shape-only check would treat
        # them as ordinary currency pairs.
        for symbol in ("XAU_USD", "XAG_USD", "BTCUSD", "EU50_EUR", "SPX500USD"):
            with self.subTest(symbol=symbol):
                with self.assertRaises(UnknownInstrumentError):
                    infer_pip_location(symbol)

    def test_empty_pair_rejected(self):
        with self.assertRaises(ValueError):
            pip_size("   ")


class TestPipArithmetic(unittest.TestCase):
    def setUp(self):
        self.eur_usd = InstrumentSpec("EURUSD", pip_location=-4, quote_currency="USD")
        self.usd_jpy = InstrumentSpec("USDJPY", pip_location=-2, quote_currency="JPY")

    def test_price_diff_to_pips(self):
        # EUR/USD 1.0850 -> 1.0875 is 25 pips.
        self.assertAlmostEqual(
            ForexPipEngine.price_diff_to_pips("EURUSD", 0.0025, self.eur_usd), 25.0
        )
        # USD/JPY 150.00 -> 150.50 is 50 pips.
        self.assertAlmostEqual(
            ForexPipEngine.price_diff_to_pips("USDJPY", 0.50, self.usd_jpy), 50.0
        )

    def test_pips_to_price_diff_round_trips(self):
        self.assertAlmostEqual(
            ForexPipEngine.pips_to_price_diff("USDJPY", 50.0, self.usd_jpy), 0.50
        )

    def test_pipette_is_one_tenth_of_a_pip(self):
        self.assertAlmostEqual(
            ForexPipEngine.pipette_size("EURUSD", self.eur_usd), 0.00001
        )

    def test_non_finite_price_diff_rejected(self):
        with self.assertRaises(ValueError):
            ForexPipEngine.price_diff_to_pips("EURUSD", float("nan"), self.eur_usd)

    def test_pip_value_same_quote_and_account_currency(self):
        # 1 standard lot EUR/USD, USD account: 100,000 * 0.0001 = 10 USD.
        value = ForexPipEngine.calculate_pip_value(
            "EURUSD", units=100_000, account_currency="USD", spec=self.eur_usd
        )
        self.assertAlmostEqual(value, 10.0)

    def test_pip_value_cross_currency_requires_explicit_rate(self):
        # Regression, and the most consequential fix in this module: USD/JPY is
        # quoted in JPY, so a USD account cannot value its pip without a
        # JPY->USD rate. The previous default of 1.0 returned 1000.0 here --
        # overstating pip value by the USD/JPY rate and mis-sizing every
        # risk-per-trade calculation that consumed it.
        with self.assertRaises(ValueError) as ctx:
            ForexPipEngine.calculate_pip_value(
                "USDJPY", units=100_000, account_currency="USD", spec=self.usd_jpy
            )
        self.assertIn("JPY", str(ctx.exception))

    def test_pip_value_cross_currency_with_rate(self):
        # 1 standard lot USD/JPY at 150.00, USD account.
        # Pip value in JPY = 100,000 * 0.01 = 1,000 JPY.
        # JPY->USD at 150.00 = 1/150 -> 1,000 / 150 = 6.666... USD.
        value = ForexPipEngine.calculate_pip_value(
            "USDJPY",
            units=100_000,
            account_currency="USD",
            quote_to_account_fx_rate=1.0 / 150.0,
            spec=self.usd_jpy,
        )
        self.assertAlmostEqual(value, 1000.0 / 150.0, places=9)
        self.assertAlmostEqual(value, 6.666666667, places=6)

    def test_pip_value_micro_lot_jpy_pair(self):
        # The checklist's manual cross-check: 1 micro lot (1,000 units) USD/JPY
        # at 150.00 -> 1,000 * 0.01 = 10 JPY -> 10 / 150 = 0.0666... USD.
        value = ForexPipEngine.calculate_pip_value(
            "USDJPY",
            units=lots_to_units(1.0, "micro"),
            account_currency="USD",
            quote_to_account_fx_rate=1.0 / 150.0,
            spec=self.usd_jpy,
        )
        self.assertAlmostEqual(value, 10.0 / 150.0, places=9)

    def test_pip_value_rejects_non_positive_rate(self):
        with self.assertRaises(ValueError):
            ForexPipEngine.calculate_pip_value(
                "USDJPY",
                units=100_000,
                account_currency="USD",
                quote_to_account_fx_rate=0.0,
                spec=self.usd_jpy,
            )


class TestTripleSwapDay(unittest.TestCase):
    def test_t_plus_2_pairs_roll_on_wednesday(self):
        # Mon=0 ... Wed=2, Thu=3.
        self.assertEqual(triple_swap_weekday("EUR/USD"), 2)
        self.assertEqual(triple_swap_weekday("GBPUSD"), 2)

    def test_t_plus_1_pairs_roll_on_thursday(self):
        # USD/CAD, USD/TRY, USD/RUB and USD/PHP settle T+1, so their value date
        # rolls Friday->Monday one rollover earlier than the T+2 default.
        self.assertEqual(triple_swap_weekday("USD_CAD"), 3)
        self.assertEqual(triple_swap_weekday("USDTRY"), 3)

    def test_explicit_settlement_override(self):
        self.assertEqual(triple_swap_weekday("EURUSD", settlement_days=1), 3)
        with self.assertRaises(ValueError):
            triple_swap_weekday("EURUSD", settlement_days=3)

    def test_count_triple_swap_rollovers_over_two_weeks(self):
        # 2026-01-05 is a Monday; 2026-01-18 is the following Sunday. The
        # Wednesdays in that inclusive range are 2026-01-07 and 2026-01-14.
        start, end = dt.date(2026, 1, 5), dt.date(2026, 1, 18)
        self.assertEqual(start.weekday(), 0)
        self.assertEqual(end.weekday(), 6)
        self.assertEqual(count_triple_swap_rollovers(start, end, 2), 2)

    def test_count_triple_swap_rollovers_edges(self):
        wednesday = dt.date(2026, 1, 7)
        self.assertEqual(wednesday.weekday(), 2)
        # Single-day range landing exactly on the triple-swap day.
        self.assertEqual(count_triple_swap_rollovers(wednesday, wednesday, 2), 1)
        # Range that skips it entirely (Thursday to the following Tuesday).
        self.assertEqual(
            count_triple_swap_rollovers(dt.date(2026, 1, 8), dt.date(2026, 1, 13), 2), 0
        )
        # Reversed range yields zero rather than a negative count.
        self.assertEqual(
            count_triple_swap_rollovers(dt.date(2026, 1, 18), dt.date(2026, 1, 5), 2), 0
        )


class TestSwapRolloverCalculator(unittest.TestCase):
    def setUp(self):
        self.calc = SwapRolloverCalculator(FIXTURE_SWAP_RATES)

    def test_requires_broker_supplied_rates(self):
        # Regression: the calculator previously shipped invented default rates
        # and silently accrued -3.0/lot/day for any unconfigured pair.
        with self.assertRaises(ValueError):
            SwapRolloverCalculator({})
        with self.assertRaises(TypeError):
            SwapRolloverCalculator()  # noqa: E1120 - required argument

    def test_duplicate_pair_spelling_rejected(self):
        with self.assertRaises(ValueError):
            SwapRolloverCalculator(
                {"EUR/USD": {"long": -5.2}, "EURUSD": {"long": -9.9}}
            )

    def test_numeric_string_lots_rejected(self):
        with self.assertRaises(TypeError):
            self.calc.calculate_swap("USDJPY", side="LONG", lots="1.0", hold_days=1)

    def test_unknown_pair_raises_rather_than_assuming(self):
        with self.assertRaises(UnknownInstrumentError):
            self.calc.calculate_swap("AUDNZD", side="LONG", lots=1.0, hold_days=1)

    def test_single_rollover(self):
        # 1 lot long USD/JPY for one rollover at +8.40 per lot per day.
        res = self.calc.calculate_swap("USDJPY", side="LONG", lots=1.0, hold_days=1)
        self.assertAlmostEqual(res.total_swap_cost, 8.40)
        self.assertEqual(res.effective_swap_days, 1)
        self.assertFalse(res.is_triple_swap_applied)

    def test_triple_swap_single_week(self):
        # One rollover, and it is the triple-swap one: 8.40 * 3 = 25.20.
        res = self.calc.calculate_swap(
            "USDJPY", side="LONG", lots=1.0, hold_days=1, triple_swap_days=1
        )
        self.assertAlmostEqual(res.total_swap_cost, 25.20)
        self.assertEqual(res.effective_swap_days, 3)
        self.assertTrue(res.is_triple_swap_applied)

    def test_triple_swap_across_two_weeks(self):
        # Regression: a boolean flag could only ever add one triple-swap day.
        # 14 rollovers of which 2 are triple: 14 + 2*2 = 18 charged days.
        # 1 lot long EUR/USD at -5.20 -> -93.60.
        res = self.calc.calculate_swap(
            "EURUSD", side="LONG", lots=1.0, hold_days=14, triple_swap_days=2
        )
        self.assertEqual(res.effective_swap_days, 18)
        self.assertAlmostEqual(res.total_swap_cost, -93.60)

    def test_legacy_includes_wednesday_flag_still_works(self):
        legacy = self.calc.calculate_swap(
            "USDJPY", side="LONG", lots=1.0, hold_days=1, includes_wednesday=True
        )
        explicit = self.calc.calculate_swap(
            "USDJPY", side="LONG", lots=1.0, hold_days=1, triple_swap_days=1
        )
        self.assertAlmostEqual(legacy.total_swap_cost, explicit.total_swap_cost)

    def test_conflicting_triple_swap_arguments_rejected(self):
        with self.assertRaises(ValueError):
            self.calc.calculate_swap(
                "USDJPY",
                side="LONG",
                lots=1.0,
                hold_days=1,
                includes_wednesday=True,
                triple_swap_days=1,
            )

    def test_triple_swap_days_cannot_exceed_hold_days(self):
        with self.assertRaises(ValueError):
            self.calc.calculate_swap(
                "USDJPY", side="LONG", lots=1.0, hold_days=1, triple_swap_days=2
            )

    def test_buy_sell_aliases_resolve_to_the_configured_rate(self):
        # Regression: an unrecognised side string silently fell back to an
        # assumed -3.0/lot/day instead of the configured rate.
        buy = self.calc.calculate_swap("USDJPY", side="BUY", lots=1.0, hold_days=1)
        long = self.calc.calculate_swap("USDJPY", side="LONG", lots=1.0, hold_days=1)
        self.assertAlmostEqual(buy.total_swap_cost, long.total_swap_cost)
        sell = self.calc.calculate_swap("USDJPY", side="SELL", lots=1.0, hold_days=1)
        self.assertAlmostEqual(sell.total_swap_cost, -14.10)

    def test_unrecognised_side_raises(self):
        with self.assertRaises(ValueError):
            self.calc.calculate_swap("USDJPY", side="flat", lots=1.0, hold_days=1)

    def test_negative_hold_days_rejected(self):
        with self.assertRaises(ValueError):
            self.calc.calculate_swap("USDJPY", side="LONG", lots=1.0, hold_days=-1)

    def test_short_side_carries_its_own_rate(self):
        # 2 lots short EUR/USD for 3 rollovers at +1.50: 2 * 1.50 * 3 = 9.00.
        res = self.calc.calculate_swap("EUR/USD", side="SHORT", lots=2.0, hold_days=3)
        self.assertAlmostEqual(res.total_swap_cost, 9.00)
        self.assertEqual(res.pair, "EURUSD")
        self.assertEqual(res.side, "SHORT")


class TestMT5BridgeMonitor(unittest.TestCase):
    def test_healthy_terminal(self):
        ok, msg = MT5BridgeMonitor(lambda: True).is_terminal_connected()
        self.assertTrue(ok)
        self.assertIn("healthy", msg)

    def test_disconnected_terminal(self):
        ok, msg = MT5BridgeMonitor(lambda: False).is_terminal_connected()
        self.assertFalse(ok)
        self.assertIn("lost connection", msg)

    def test_check_function_is_required(self):
        # Regression: the monitor previously defaulted to a check that always
        # returned True, so an unconfigured monitor reported a terminal it had
        # never probed as healthy.
        with self.assertRaises(TypeError):
            MT5BridgeMonitor()  # noqa: E1120 - required argument
        with self.assertRaises(TypeError):
            MT5BridgeMonitor(terminal_connected_check_fn="not-callable")

    def test_none_result_is_unhealthy(self):
        # mt5.terminal_info() yields None when no terminal is attached, so a
        # check function that forwards it must not read as healthy.
        ok, _ = MT5BridgeMonitor(lambda: None).is_terminal_connected()
        self.assertFalse(ok)

    def test_probe_exception_is_unhealthy(self):
        def _boom():
            raise RuntimeError("IPC pipe closed")

        with self.assertLogs("pip_conversion", level=logging.ERROR):
            ok, msg = MT5BridgeMonitor(_boom).is_terminal_connected()
        self.assertFalse(ok)
        self.assertIn("IPC pipe closed", msg)


class TestMT5TerminalConnectedCheck(unittest.TestCase):
    def test_missing_terminal_reports_disconnected(self):
        # terminal_info() returns None when no terminal is attached; the naive
        # `terminal_info().connected` would raise in exactly this case.
        check = mt5_terminal_connected_check(_FakeMT5Module(None))
        self.assertFalse(check())

    def test_connected_but_trading_disabled_reports_disconnected(self):
        check = mt5_terminal_connected_check(
            _FakeMT5Module(_FakeTerminalInfo(connected=True, trade_allowed=False))
        )
        self.assertFalse(check())

    def test_fully_healthy_terminal(self):
        check = mt5_terminal_connected_check(
            _FakeMT5Module(_FakeTerminalInfo(connected=True, trade_allowed=True))
        )
        self.assertTrue(check())


class TestOandaEnvironmentIsolation(unittest.TestCase):
    def test_practice_and_live_hosts_are_disjoint(self):
        practice = oanda_hosts("practice")
        live = oanda_hosts("live")
        self.assertEqual(practice["rest"], "https://api-fxpractice.oanda.com")
        self.assertEqual(live["rest"], "https://api-fxtrade.oanda.com")
        self.assertNotEqual(practice["rest"], live["rest"])
        self.assertNotEqual(practice["stream"], live["stream"])

    def test_unknown_environment_rejected(self):
        for value in ("", "prod", "demo"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    oanda_hosts(value)

    def test_returned_mapping_is_a_copy(self):
        hosts = oanda_hosts("practice")
        hosts["rest"] = "https://api-fxtrade.oanda.com"
        self.assertEqual(oanda_hosts("practice")["rest"], "https://api-fxpractice.oanda.com")


class TestLotConversion(unittest.TestCase):
    def test_lot_types(self):
        self.assertEqual(lots_to_units(2.5, "standard"), 250_000.0)
        self.assertEqual(lots_to_units(1.0, "micro"), 1_000.0)
        self.assertEqual(units_to_lots(50_000.0, "mini"), 5.0)

    def test_unknown_lot_type_rejected(self):
        with self.assertRaises(ValueError):
            lots_to_units(1.0, "jumbo")

    def test_non_finite_lots_rejected(self):
        with self.assertRaises(ValueError):
            lots_to_units(float("inf"))


class TestModuleLevelWrappers(unittest.TestCase):
    def test_wrappers_accept_specs(self):
        spec = InstrumentSpec("USDJPY", pip_location=-2, quote_currency="JPY")
        self.assertAlmostEqual(pip_size("USDJPY", spec), 0.01)
        self.assertAlmostEqual(price_diff_to_pips("USDJPY", 0.25, spec), 25.0)


if __name__ == "__main__":
    unittest.main()
