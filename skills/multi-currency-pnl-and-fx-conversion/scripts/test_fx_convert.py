"""
Unit tests for multi-currency-pnl-and-fx-conversion.

Expected values are derived by hand in the comments rather than by re-running the
module's own expressions, so a sign flip or a mis-placed interaction term fails
instead of agreeing with itself.

Covered:
1.  Rate direction convention and conversion.
2.  Refusal to invent a rate (no silent parity fallback).
3.  Direct / inverse / pivot triangulation.
4.  Point-in-time as-of resolution, lookahead refusal, staleness bound, tz mixing.
5.  PnL decomposition: reconciliation, interaction term, shorts, losses.
6.  ISO 4217 minor units, half-up money rounding, unknown-currency warning.
7.  Aggregation: single rounding at the end, refusal to drop a failed leg.
8.  Input validation (NaN/Inf, blank codes, non-positive rates, untagged numbers).
9.  Pre-2.0 module-level helper compatibility.
"""
import datetime
import logging
import math
import unittest

from fx_convert import (
    CURRENCY_DECIMALS,
    CurrencyAmount,
    DecomposedPnL,
    FXConversionError,
    FXRateUnavailableError,
    HistoricalFXRateStore,
    ISO_4217_MINOR_UNITS,
    MultiCurrencyPnLEngine,
    PointInTimeFXResolver,
    aggregate_in_base_currency,
    convert,
    minor_units_for,
    normalize_currency,
    round_money,
)

UTC = datetime.timezone.utc


def setUpModule():
    """Keep the module's (correct) unknown-currency warnings out of test output."""
    module_logger = logging.getLogger("fx_convert")
    module_logger.addHandler(logging.NullHandler())
    module_logger.propagate = False


def table_provider(rates):
    """Build a RateProviderFn from a {(from, to): rate} dict."""

    def provider(from_ccy, to_ccy, timestamp=None):
        try:
            return rates[(from_ccy, to_ccy)]
        except KeyError:
            raise FXRateUnavailableError(f"no {from_ccy}->{to_ccy}")

    return provider


class TestRateDirectionAndConversion(unittest.TestCase):

    def test_convert_with_custom_resolver(self):
        # 100 EUR at 1.10 USD per EUR = 110.00 USD.
        amt = CurrencyAmount(amount=100.0, currency="EUR")
        resolver = PointInTimeFXResolver(
            rate_provider_fn=table_provider({("EUR", "USD"): 1.10}))
        engine = MultiCurrencyPnLEngine(fx_resolver=resolver)

        res = engine.convert(amt, "USD")
        self.assertEqual(res.currency, "USD")
        self.assertEqual(res.amount, 110.0)

    def test_rate_means_target_units_per_source_unit(self):
        # 1 USD -> 83.50 INR, so 200 USD is 16,700.00 INR.
        engine = MultiCurrencyPnLEngine(
            fx_resolver=PointInTimeFXResolver(
                rate_provider_fn=table_provider({("USD", "INR"): 83.50})))
        self.assertEqual(
            engine.convert(CurrencyAmount(200.0, "USD"), "INR").amount, 16700.0)

    def test_same_currency_returns_a_normalised_copy_not_the_input(self):
        # Regression: the same-currency path used to return the caller's own object,
        # skipping both rounding and code normalisation.
        engine = MultiCurrencyPnLEngine()
        original = CurrencyAmount(amount=10.005, currency="usd")
        result = engine.convert(original, "USD")

        self.assertIsNot(result, original)
        self.assertEqual(result.currency, "USD")
        self.assertEqual(result.amount, 10.01)          # rounded half-up
        self.assertEqual(original.amount, 10.005)       # caller's object untouched
        self.assertEqual(original.currency, "usd")


class TestNoSilentParityFallback(unittest.TestCase):
    """
    The pre-2.0 default provider returned 1.0 for any pair it did not know, so
    BTC->USD converted at parity. Nothing about the output revealed it.
    """

    def test_bare_resolver_raises_instead_of_returning_one(self):
        resolver = PointInTimeFXResolver()
        with self.assertRaises(FXRateUnavailableError):
            resolver.get_rate("BTC", "USD")

    def test_engine_refuses_to_convert_an_unknown_pair(self):
        engine = MultiCurrencyPnLEngine()
        with self.assertRaises(FXRateUnavailableError):
            engine.convert(CurrencyAmount(1.0, "BTC"), "USD")

    def test_unknown_pair_raises_even_when_other_pairs_resolve(self):
        engine = MultiCurrencyPnLEngine(
            fx_resolver=PointInTimeFXResolver(
                rate_provider_fn=table_provider({("EUR", "USD"): 1.10})))
        with self.assertRaises(FXRateUnavailableError):
            engine.convert(CurrencyAmount(1.0, "BTC"), "USD")

    def test_identity_pair_still_resolves_without_a_provider(self):
        self.assertEqual(PointInTimeFXResolver().get_rate("USD", "usd"), 1.0)


class TestTriangulation(unittest.TestCase):

    def test_inverse_path(self):
        # Provider only knows EUR->USD = 1.25, so USD->EUR must be 1/1.25 = 0.8.
        resolver = PointInTimeFXResolver(
            rate_provider_fn=table_provider({("EUR", "USD"): 1.25}))
        self.assertAlmostEqual(resolver.get_rate("USD", "EUR"), 0.8, places=12)

    def test_pivot_triangulation_through_usd(self):
        # EUR->USD 1.10 and USD->INR 83.50 give EUR->INR = 1.10 * 83.50 = 91.85.
        resolver = PointInTimeFXResolver(rate_provider_fn=table_provider({
            ("EUR", "USD"): 1.10,
            ("USD", "INR"): 83.50,
        }))
        self.assertAlmostEqual(resolver.get_rate("EUR", "INR"), 91.85, places=10)

    def test_pivot_triangulation_with_both_legs_inverted(self):
        # Provider quotes USD->EUR and INR->USD; EUR->INR still resolves:
        # (1 / 0.90909...) * (1 / 0.0125) = 1.1 * 80 = 88.0
        resolver = PointInTimeFXResolver(rate_provider_fn=table_provider({
            ("USD", "EUR"): 1.0 / 1.1,
            ("INR", "USD"): 1.0 / 80.0,
        }))
        self.assertAlmostEqual(resolver.get_rate("EUR", "INR"), 88.0, places=9)

    def test_pivot_is_configurable(self):
        # No USD anywhere: triangulate GBP->JPY through EUR.
        # 1.15 EUR per GBP * 170 JPY per EUR = 195.5 JPY per GBP.
        resolver = PointInTimeFXResolver(
            rate_provider_fn=table_provider({("GBP", "EUR"): 1.15, ("EUR", "JPY"): 170.0}),
            pivot_currencies=("EUR",))
        self.assertAlmostEqual(resolver.get_rate("GBP", "JPY"), 195.5, places=9)

    def test_no_path_raises(self):
        resolver = PointInTimeFXResolver(
            rate_provider_fn=table_provider({("EUR", "USD"): 1.10}))
        with self.assertRaises(FXRateUnavailableError):
            resolver.get_rate("JPY", "INR")

    def test_provider_raising_keyerror_is_treated_as_unavailable(self):
        def raw_dict_provider(from_ccy, to_ccy, timestamp=None):
            return {("EUR", "USD"): 1.10}[(from_ccy, to_ccy)]

        resolver = PointInTimeFXResolver(rate_provider_fn=raw_dict_provider)
        self.assertEqual(resolver.get_rate("EUR", "USD"), 1.10)
        with self.assertRaises(FXRateUnavailableError):
            resolver.get_rate("JPY", "INR")

    def test_provider_returning_none_is_treated_as_unavailable(self):
        resolver = PointInTimeFXResolver(
            rate_provider_fn=lambda f, t, ts=None: 1.10 if (f, t) == ("EUR", "USD") else None)
        self.assertEqual(resolver.get_rate("EUR", "USD"), 1.10)
        with self.assertRaises(FXRateUnavailableError):
            resolver.get_rate("JPY", "INR")


class TestPointInTimeResolution(unittest.TestCase):

    def setUp(self):
        self.store = HistoricalFXRateStore()
        for day, rate in ((1, 80.0), (5, 82.0), (9, 85.0)):
            self.store.add_rate("USD", "INR", datetime.datetime(2024, 1, day), rate)

    def test_as_of_returns_the_rate_in_force_not_the_next_one(self):
        # 7 Jan sits between the 5 Jan (82.0) and 9 Jan (85.0) observations.
        # Using 85.0 would be lookahead.
        self.assertEqual(
            self.store.get_rate("USD", "INR", datetime.datetime(2024, 1, 7)), 82.0)

    def test_exact_timestamp_match_uses_that_observation(self):
        self.assertEqual(
            self.store.get_rate("USD", "INR", datetime.datetime(2024, 1, 5)), 82.0)

    def test_lookup_before_first_observation_raises_rather_than_borrowing_it(self):
        with self.assertRaises(FXRateUnavailableError):
            self.store.get_rate("USD", "INR", datetime.datetime(2023, 12, 31))

    def test_out_of_order_insertion_is_still_resolved_as_of(self):
        store = HistoricalFXRateStore()
        store.add_rate("USD", "INR", datetime.datetime(2024, 1, 9), 85.0)
        store.add_rate("USD", "INR", datetime.datetime(2024, 1, 1), 80.0)
        store.add_rate("USD", "INR", datetime.datetime(2024, 1, 5), 82.0)
        self.assertEqual(store.get_rate("USD", "INR", datetime.datetime(2024, 1, 6)), 82.0)

    def test_last_write_wins_on_a_duplicate_timestamp(self):
        store = HistoricalFXRateStore()
        store.add_rate("USD", "INR", datetime.datetime(2024, 1, 1), 80.0)
        store.add_rate("USD", "INR", datetime.datetime(2024, 1, 1), 81.0)
        self.assertEqual(store.get_rate("USD", "INR", datetime.datetime(2024, 1, 1)), 81.0)

    def test_staleness_bound_rejects_an_old_rate(self):
        store = HistoricalFXRateStore(max_staleness=datetime.timedelta(days=2))
        store.add_rate("USD", "INR", datetime.datetime(2024, 1, 1), 80.0)
        self.assertEqual(store.get_rate("USD", "INR", datetime.datetime(2024, 1, 3)), 80.0)
        with self.assertRaises(FXRateUnavailableError):
            store.get_rate("USD", "INR", datetime.datetime(2024, 1, 4))

    def test_missing_timestamp_is_an_error_not_an_implicit_latest(self):
        with self.assertRaises(FXConversionError):
            self.store.get_rate("USD", "INR")

    def test_require_timestamp_blocks_untimestamped_resolution(self):
        resolver = PointInTimeFXResolver(
            rate_provider_fn=table_provider({("USD", "INR"): 83.5}),
            require_timestamp=True)
        self.assertEqual(
            resolver.get_rate("USD", "INR", datetime.datetime(2024, 1, 1)), 83.5)
        with self.assertRaises(FXConversionError):
            resolver.get_rate("USD", "INR")

    def test_mixing_naive_and_aware_timestamps_raises_a_clear_error(self):
        with self.assertRaises(FXConversionError):
            self.store.get_rate("USD", "INR", datetime.datetime(2024, 1, 7, tzinfo=UTC))
        with self.assertRaises(FXConversionError):
            self.store.add_rate(
                "USD", "INR", datetime.datetime(2024, 1, 11, tzinfo=UTC), 86.0)

    def test_store_works_as_a_resolver_provider_with_triangulation(self):
        store = HistoricalFXRateStore()
        store.add_rate("EUR", "USD", datetime.datetime(2024, 1, 1), 1.10)
        store.add_rate("USD", "INR", datetime.datetime(2024, 1, 1), 83.50)
        engine = MultiCurrencyPnLEngine(
            fx_resolver=PointInTimeFXResolver(rate_provider_fn=store))
        # 1,000 EUR -> INR via USD: 1000 * 1.10 * 83.50 = 91,850.00
        converted = engine.convert(
            CurrencyAmount(1000.0, "EUR", datetime.datetime(2024, 1, 3)), "INR")
        self.assertEqual(converted.amount, 91850.0)

    def test_as_of_lookup_stays_correct_over_a_large_series(self):
        # Guards the parallel-list layout added for lookup cost: a rebuilt key list
        # made both insert and lookup O(n) on the module's backtest hot path.
        store = HistoricalFXRateStore()
        base = datetime.datetime(2020, 1, 1)
        for i in range(5000):
            store.add_rate("USD", "INR", base + datetime.timedelta(minutes=i), 80.0 + i)
        # 30 seconds past minute 1234 must still resolve to minute 1234's rate.
        query = base + datetime.timedelta(minutes=1234, seconds=30)
        self.assertEqual(store.get_rate("USD", "INR", query), 80.0 + 1234)
        self.assertEqual(
            store.get_rate("USD", "INR", base + datetime.timedelta(minutes=4999)),
            80.0 + 4999)

    def test_overwriting_one_observation_leaves_its_neighbours_intact(self):
        store = HistoricalFXRateStore()
        base = datetime.datetime(2024, 1, 1)
        for i, rate in enumerate((80.0, 81.0, 82.0)):
            store.add_rate("USD", "INR", base + datetime.timedelta(days=i), rate)
        store.add_rate("USD", "INR", base + datetime.timedelta(days=1), 99.0)
        self.assertEqual(store.get_rate("USD", "INR", base), 80.0)
        self.assertEqual(
            store.get_rate("USD", "INR", base + datetime.timedelta(days=1)), 99.0)
        self.assertEqual(
            store.get_rate("USD", "INR", base + datetime.timedelta(days=2)), 82.0)

    def test_store_rejects_an_identity_pair(self):
        with self.assertRaises(FXConversionError):
            self.store.add_rate("USD", "USD", datetime.datetime(2024, 1, 1), 1.0)

    def test_store_identity_lookup_is_one(self):
        self.assertEqual(
            self.store.get_rate("USD", "USD", datetime.datetime(2024, 1, 7)), 1.0)


class TestPnLDecomposition(unittest.TestCase):

    def setUp(self):
        self.engine = MultiCurrencyPnLEngine()

    def test_pnl_decomposition(self):
        # Long 10 US shares, entry $100 / exit $110, base INR, FX 80 -> 85.
        #   price effect at entry FX : (110 - 100) * 10 * 80 =   8,000 INR
        #   FX on exit notional      : 110 * 10 * (85 - 80)  =   5,500 INR
        #   total                    : 110*10*85 - 100*10*80 = 93,500 - 80,000 = 13,500
        decomp = self.engine.calculate_decomposed_pnl(
            entry_price=100.0, exit_price=110.0, quantity=10.0,
            native_currency="USD", base_currency="INR",
            override_entry_fx=80.0, override_exit_fx=85.0)

        self.assertEqual(decomp.native_price_pnl, 8000.0)
        self.assertEqual(decomp.fx_translation_pnl, 5500.0)
        self.assertEqual(decomp.total_base_pnl, 13500.0)
        self.assertEqual(decomp.native_pnl, 100.0)          # $100, native currency

    def test_total_equals_direct_valuation(self):
        # Independent check: total must equal q*P1*X1 - q*P0*X0 computed directly.
        decomp = self.engine.calculate_decomposed_pnl(
            entry_price=100.0, exit_price=110.0, quantity=10.0,
            native_currency="USD", base_currency="INR",
            override_entry_fx=80.0, override_exit_fx=85.0)
        expected = 110.0 * 10.0 * 85.0 - 100.0 * 10.0 * 80.0
        self.assertEqual(decomp.total_base_pnl, expected)

    def test_interaction_term_is_reported_and_lives_inside_the_fx_component(self):
        # FX on entry notional : 100 * 10 * 5 = 5,000
        # price/FX interaction : (110-100) * 10 * 5 =  500
        # their sum is the reported fx_translation_pnl of 5,500 -- i.e. the whole
        # cross term is inside the FX leg under this convention.
        decomp = self.engine.calculate_decomposed_pnl(
            entry_price=100.0, exit_price=110.0, quantity=10.0,
            native_currency="USD", base_currency="INR",
            override_entry_fx=80.0, override_exit_fx=85.0)
        self.assertEqual(decomp.fx_on_entry_notional, 5000.0)
        self.assertEqual(decomp.price_fx_interaction, 500.0)
        self.assertEqual(
            decomp.fx_on_entry_notional + decomp.price_fx_interaction,
            decomp.fx_translation_pnl)

    def test_components_reconcile_to_the_total_under_awkward_rounding(self):
        # Regression. Long 1 unit, $10.00 -> $10.40, FX 1.0 -> 1.4, base JPY (0 dp):
        #   price effect raw = 0.4  * 1 * 1.0 = 0.40
        #   FX effect raw    = 10.4 * 1 * 0.4 = 4.16
        #   total raw                          = 4.56  -> 5 JPY
        # Rounding all three independently reported 0 + 4 against a total of 5, so
        # the components did not add up to the number beside them.
        decomp = self.engine.calculate_decomposed_pnl(
            entry_price=10.0, exit_price=10.4, quantity=1.0,
            native_currency="USD", base_currency="JPY",
            override_entry_fx=1.0, override_exit_fx=1.4)
        self.assertEqual(decomp.total_base_pnl, 5.0)
        self.assertEqual(decomp.native_price_pnl, 0.0)
        self.assertEqual(
            decomp.native_price_pnl + decomp.fx_translation_pnl, decomp.total_base_pnl)

    def test_short_position_fx_gain_is_signed_correctly(self):
        # Short 10 shares (q = -10), entry $110 exit $100, base INR, FX 80 -> 85.
        #   total = -10*100*85 - (-10*110*80) = -85,000 + 88,000 = 3,000
        #   price effect = (100-110) * -10 * 80 = 8,000
        #   FX effect    = -10 * 100 * 5        = -5,000
        decomp = self.engine.calculate_decomposed_pnl(
            entry_price=110.0, exit_price=100.0, quantity=-10.0,
            native_currency="USD", base_currency="INR",
            override_entry_fx=80.0, override_exit_fx=85.0)
        self.assertEqual(decomp.native_price_pnl, 8000.0)
        self.assertEqual(decomp.fx_translation_pnl, -5000.0)
        self.assertEqual(decomp.total_base_pnl, 3000.0)

    def test_price_gain_can_be_erased_by_an_fx_loss(self):
        # The reason the decomposition exists: +$100 of trading edge, -13,000 INR
        # of currency, for a net loss on a winning trade.
        #   price effect = 10 * 10 * 85 = 8,500
        #   FX effect    = 110 * 10 * (72 - 85) = -14,300
        decomp = self.engine.calculate_decomposed_pnl(
            entry_price=100.0, exit_price=110.0, quantity=10.0,
            native_currency="USD", base_currency="INR",
            override_entry_fx=85.0, override_exit_fx=72.0)
        self.assertEqual(decomp.native_pnl, 100.0)
        self.assertEqual(decomp.native_price_pnl, 8500.0)
        self.assertEqual(decomp.fx_translation_pnl, -14300.0)
        self.assertEqual(decomp.total_base_pnl, -5800.0)

    def test_flat_fx_leaves_no_translation_component(self):
        # (110 - 100) * 10 * 83 = 8,300 INR, all of it price.
        decomp = self.engine.calculate_decomposed_pnl(
            entry_price=100.0, exit_price=110.0, quantity=10.0,
            native_currency="USD", base_currency="INR",
            override_entry_fx=83.0, override_exit_fx=83.0)
        self.assertEqual(decomp.fx_translation_pnl, 0.0)
        self.assertEqual(decomp.total_base_pnl, 8300.0)

    def test_same_currency_decomposition_has_no_fx_effect(self):
        engine = MultiCurrencyPnLEngine()  # bare resolver: identity needs no provider
        decomp = engine.calculate_decomposed_pnl(
            entry_price=100.0, exit_price=110.0, quantity=10.0,
            native_currency="USD", base_currency="USD")
        self.assertEqual(decomp.entry_fx_rate, 1.0)
        self.assertEqual(decomp.fx_translation_pnl, 0.0)
        self.assertEqual(decomp.total_base_pnl, 100.0)

    def test_decomposition_resolves_rates_at_the_supplied_timestamps(self):
        store = HistoricalFXRateStore()
        store.add_rate("USD", "INR", datetime.datetime(2024, 1, 1), 80.0)
        store.add_rate("USD", "INR", datetime.datetime(2024, 6, 1), 85.0)
        engine = MultiCurrencyPnLEngine(
            fx_resolver=PointInTimeFXResolver(rate_provider_fn=store))

        decomp = engine.calculate_decomposed_pnl(
            entry_price=100.0, exit_price=110.0, quantity=10.0,
            native_currency="USD", base_currency="INR",
            entry_timestamp=datetime.datetime(2024, 2, 1),
            exit_timestamp=datetime.datetime(2024, 7, 1))
        # Entry resolves to the 1 Jan rate (80), exit to the 1 Jun rate (85) --
        # not one current rate applied to both ends.
        self.assertEqual(decomp.entry_fx_rate, 80.0)
        self.assertEqual(decomp.exit_fx_rate, 85.0)
        self.assertEqual(decomp.total_base_pnl, 13500.0)

    def test_rates_are_reported_unrounded(self):
        decomp = self.engine.calculate_decomposed_pnl(
            entry_price=100.0, exit_price=110.0, quantity=10.0,
            native_currency="USD", base_currency="JPY",
            override_entry_fx=155.5555, override_exit_fx=151.1111)
        self.assertEqual(decomp.entry_fx_rate, 155.5555)
        self.assertEqual(decomp.exit_fx_rate, 151.1111)

    def test_non_positive_or_non_finite_rates_are_rejected(self):
        for bad in (0.0, -80.0, float("nan"), float("inf")):
            with self.assertRaises(FXConversionError):
                self.engine.calculate_decomposed_pnl(
                    entry_price=100.0, exit_price=110.0, quantity=10.0,
                    native_currency="USD", base_currency="INR",
                    override_entry_fx=bad, override_exit_fx=85.0)

    def test_non_finite_prices_are_rejected(self):
        with self.assertRaises(FXConversionError):
            self.engine.calculate_decomposed_pnl(
                entry_price=float("nan"), exit_price=110.0, quantity=10.0,
                native_currency="USD", base_currency="INR",
                override_entry_fx=80.0, override_exit_fx=85.0)

    def test_inverted_exit_before_entry_is_warned_about(self):
        with self.assertLogs("fx_convert", level="WARNING"):
            self.engine.calculate_decomposed_pnl(
                entry_price=100.0, exit_price=110.0, quantity=10.0,
                native_currency="USD", base_currency="USD",
                entry_timestamp=datetime.datetime(2024, 6, 1),
                exit_timestamp=datetime.datetime(2024, 1, 1))

    def test_decomposed_pnl_positional_construction_still_works(self):
        # The three new fields are appended with defaults, so pre-2.0 positional
        # construction of the original seven is unaffected.
        record = DecomposedPnL("USD", "INR", 8000.0, 5500.0, 13500.0, 80.0, 85.0)
        self.assertEqual(record.total_base_pnl, 13500.0)
        self.assertEqual(record.price_fx_interaction, 0.0)


class TestPrecisionAndRounding(unittest.TestCase):

    def setUp(self):
        self.engine = MultiCurrencyPnLEngine()

    def test_currency_rounding(self):
        self.assertEqual(self.engine.round_amount(123.456, "USD"), 123.46)
        self.assertEqual(self.engine.round_amount(123.456, "JPY"), 123.0)

    def test_money_rounding_is_half_up_not_bankers(self):
        # round(2.675, 2) is 2.67 -- half-to-even on the binary float. Settlement
        # convention is half-up.
        self.assertEqual(round(2.675, 2), 2.67)
        self.assertEqual(self.engine.round_amount(2.675, "USD"), 2.68)
        self.assertEqual(self.engine.round_amount(0.125, "USD"), 0.13)
        self.assertEqual(self.engine.round_amount(2.5, "JPY"), 3.0)

    def test_half_up_is_symmetric_for_negative_amounts(self):
        self.assertEqual(self.engine.round_amount(-2.675, "USD"), -2.68)
        self.assertEqual(self.engine.round_amount(-2.5, "JPY"), -3.0)

    def test_iso_4217_zero_decimal_currencies(self):
        for code in ("JPY", "KRW", "CLP", "ISK", "VND", "XOF"):
            self.assertEqual(ISO_4217_MINOR_UNITS[code], 0, code)
            self.assertEqual(self.engine.round_amount(1234.56, code), 1235.0, code)

    def test_krw_regression(self):
        # references/standards.md has always documented KRW as 0 decimals, but the
        # pre-2.0 table omitted it, so it silently rounded to 2.
        self.assertEqual(self.engine.round_amount(1234.567, "KRW"), 1235.0)

    def test_iso_4217_three_decimal_currencies(self):
        for code in ("KWD", "BHD", "JOD", "OMR", "TND", "IQD", "LYD"):
            self.assertEqual(ISO_4217_MINOR_UNITS[code], 3, code)
            self.assertEqual(self.engine.round_amount(1.23456, code), 1.235, code)

    def test_unknown_currency_warns_before_falling_back_to_two_decimals(self):
        import fx_convert
        fx_convert._warned_unknown_currencies.discard("ZZZ")
        with self.assertLogs("fx_convert", level="WARNING"):
            self.assertEqual(minor_units_for("ZZZ"), 2)

    def test_venue_precision_can_be_registered(self):
        engine = MultiCurrencyPnLEngine(minor_units={"USDT": 6})
        self.assertEqual(engine.round_amount(1.23456789, "USDT"), 1.234568)
        engine.register_currency_precision("SOL", 4)
        self.assertEqual(engine.round_amount(1.234567, "SOL"), 1.2346)

    def test_registering_precision_does_not_leak_into_the_module_table(self):
        engine = MultiCurrencyPnLEngine()
        engine.register_currency_precision("USD", 0)
        self.assertEqual(engine.round_amount(1.6, "USD"), 2.0)
        self.assertEqual(CURRENCY_DECIMALS["USD"], 2)
        self.assertEqual(MultiCurrencyPnLEngine().round_amount(1.6, "USD"), 1.6)

    def test_crypto_defaults_are_present_but_overridable(self):
        self.assertEqual(CURRENCY_DECIMALS["BTC"], 8)
        self.assertEqual(self.engine.round_amount(0.123456785, "BTC"), 0.12345679)

    def test_round_money_rejects_non_finite_and_negative_places(self):
        with self.assertRaises(FXConversionError):
            round_money(float("nan"), 2)
        with self.assertRaises(FXConversionError):
            round_money(1.0, -1)

    def test_high_magnitude_amounts_round_instead_of_overflowing_the_context(self):
        # Decimal's default 28-digit context cannot quantise 1e30 to two places, so
        # a fixed context raised InvalidOperation at an arbitrary ceiling. That
        # ceiling sits inside the plausible range for 0-decimal currencies such as
        # KRW, IDR and VND.
        self.assertEqual(self.engine.round_amount(1.23e25, "KRW"), 1.23e25)
        self.assertEqual(self.engine.round_amount(9.87654321e18, "VND"), 9.87654321e18)
        self.assertEqual(self.engine.round_amount(1e300, "USD"), 1e300)

    def test_subnormal_amount_rounds_to_zero_rather_than_raising(self):
        self.assertEqual(self.engine.round_amount(1e-12, "BTC"), 0.0)


class TestAggregation(unittest.TestCase):

    def setUp(self):
        self.engine = MultiCurrencyPnLEngine(
            fx_resolver=PointInTimeFXResolver(rate_provider_fn=table_provider({
                ("EUR", "USD"): 1.10,
                ("INR", "USD"): 0.012,
            })))

    def test_base_currency_aggregation(self):
        # 100 EUR * 1.10 = 110.00; 5,000 INR * 0.012 = 60.00; plus 40 USD = 210.00
        total = self.engine.aggregate_in_base_currency([
            CurrencyAmount(100.0, "EUR"),
            CurrencyAmount(5000.0, "INR"),
            CurrencyAmount(40.0, "USD"),
        ], "USD")
        self.assertEqual(total, 210.0)

    def test_rounding_happens_once_at_the_end_not_per_leg(self):
        # Four legs of 0.4 USD converted 1:1 into a 0-decimal base. Rounding each
        # leg first gives 0+0+0+0 = 0; summing first gives 1.6 -> 2.
        engine = self._unit_rate_engine()
        legs = [CurrencyAmount(0.4, "USD") for _ in range(4)]
        self.assertEqual(engine.aggregate_in_base_currency(legs, "JPY"), 2.0)

    def test_per_leg_rounding_drift_is_avoided_at_scale(self):
        # Regression: 1,000 legs of 0.5 USD at parity into JPY. The exact total is
        # 500. Rounding each leg to JPY's 0 decimals first reported 0.0 -- a
        # complete erasure of the exposure, not a rounding nudge.
        engine = self._unit_rate_engine()
        legs = [CurrencyAmount(0.5, "USD") for _ in range(1000)]
        self.assertEqual(engine.aggregate_in_base_currency(legs, "JPY"), 500.0)

    @staticmethod
    def _unit_rate_engine():
        """USD -> JPY at exactly 1.0, isolating rounding from conversion."""
        return MultiCurrencyPnLEngine(
            fx_resolver=PointInTimeFXResolver(
                rate_provider_fn=table_provider({("USD", "JPY"): 1.0})))

    def test_empty_aggregate_is_zero(self):
        self.assertEqual(self.engine.aggregate_in_base_currency([], "USD"), 0.0)

    def test_a_leg_that_cannot_convert_fails_the_whole_aggregate(self):
        # Silently dropping the leg would understate the exposure a risk check reads.
        with self.assertRaises(FXConversionError) as ctx:
            self.engine.aggregate_in_base_currency(
                [CurrencyAmount(100.0, "EUR"), CurrencyAmount(1.0, "BTC")], "USD")
        self.assertIn("leg 1", str(ctx.exception))

    def test_untagged_number_is_rejected(self):
        with self.assertRaises(FXConversionError):
            self.engine.aggregate_in_base_currency([100.0], "USD")

    def test_a_bare_currency_amount_is_not_a_sequence(self):
        with self.assertRaises(FXConversionError):
            self.engine.aggregate_in_base_currency(CurrencyAmount(1.0, "USD"), "USD")

    def test_per_leg_timestamps_win_over_the_aggregate_timestamp(self):
        store = HistoricalFXRateStore()
        store.add_rate("EUR", "USD", datetime.datetime(2024, 1, 1), 1.00)
        store.add_rate("EUR", "USD", datetime.datetime(2024, 6, 1), 1.50)
        engine = MultiCurrencyPnLEngine(
            fx_resolver=PointInTimeFXResolver(rate_provider_fn=store))
        legs = [
            CurrencyAmount(100.0, "EUR", datetime.datetime(2024, 2, 1)),   # @ 1.00
            CurrencyAmount(100.0, "EUR", datetime.datetime(2024, 7, 1)),   # @ 1.50
        ]
        self.assertEqual(
            engine.aggregate_in_base_currency(
                legs, "USD", timestamp=datetime.datetime(2024, 7, 1)),
            250.0)


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = MultiCurrencyPnLEngine(
            fx_resolver=PointInTimeFXResolver(
                rate_provider_fn=table_provider({("EUR", "USD"): 1.10})))

    def test_blank_and_non_string_currency_codes_are_rejected(self):
        for bad in ("", "   ", None, 840, "US D"):
            with self.assertRaises(FXConversionError):
                normalize_currency(bad)

    def test_currency_codes_are_normalised(self):
        self.assertEqual(normalize_currency("  eur "), "EUR")

    def test_non_finite_amount_is_rejected_rather_than_propagating(self):
        # A NaN exposure compares False against every risk threshold.
        for bad in (float("nan"), float("inf"), -float("inf")):
            with self.assertRaises(FXConversionError):
                self.engine.convert(CurrencyAmount(bad, "EUR"), "USD")

    def test_nan_does_not_survive_aggregation(self):
        with self.assertRaises(FXConversionError):
            self.engine.aggregate_in_base_currency(
                [CurrencyAmount(100.0, "EUR"), CurrencyAmount(float("nan"), "USD")], "USD")

    def test_provider_returning_a_non_positive_rate_is_rejected(self):
        for bad in (0.0, -1.10, float("nan")):
            engine = MultiCurrencyPnLEngine(
                fx_resolver=PointInTimeFXResolver(
                    rate_provider_fn=table_provider({("EUR", "USD"): bad})))
            with self.assertRaises(FXConversionError):
                engine.convert(CurrencyAmount(100.0, "EUR"), "USD")

    def test_non_callable_provider_is_rejected(self):
        with self.assertRaises(FXConversionError):
            PointInTimeFXResolver(rate_provider_fn="not callable")

    def test_non_datetime_timestamp_is_rejected(self):
        with self.assertRaises(FXConversionError):
            self.engine.convert(CurrencyAmount(100.0, "EUR"), "USD", timestamp="2024-01-01")

    def test_negative_max_staleness_is_rejected(self):
        with self.assertRaises(FXConversionError):
            HistoricalFXRateStore(max_staleness=datetime.timedelta(days=-1))

    def test_bad_registered_precision_is_rejected(self):
        engine = MultiCurrencyPnLEngine()
        for bad in (-1, 2.5, "two", True):
            with self.assertRaises(FXConversionError):
                engine.register_currency_precision("XYZ", bad)

    def test_no_nan_reaches_a_decomposed_result(self):
        decomp = self.engine.calculate_decomposed_pnl(
            entry_price=100.0, exit_price=110.0, quantity=10.0,
            native_currency="EUR", base_currency="USD")
        for value in (decomp.native_price_pnl, decomp.fx_translation_pnl,
                      decomp.total_base_pnl, decomp.entry_fx_rate, decomp.exit_fx_rate):
            self.assertTrue(math.isfinite(value))


class TestLegacyModuleHelpers(unittest.TestCase):

    def test_backward_compatibility(self):
        amt = CurrencyAmount(amount=50.0, currency="EUR")
        rate_fn = lambda f, t: 1.2 if f == "EUR" and t == "USD" else 1.0

        res = convert(amt, "USD", rate_fn)
        self.assertEqual(res.amount, 60.0)

        total = aggregate_in_base_currency([amt, CurrencyAmount(100.0, "USD")], "USD", rate_fn)
        self.assertEqual(total, 160.0)

    def test_legacy_convert_matches_currency_case_insensitively(self):
        amt = CurrencyAmount(amount=50.0, currency="usd")

        def rate_fn(from_ccy, to_ccy):
            raise AssertionError("must not look up a rate for a same-currency convert")

        self.assertEqual(convert(amt, "USD", rate_fn).amount, 50.0)

    def test_legacy_convert_passes_the_timestamp_through_when_present(self):
        seen = {}

        def rate_fn(from_ccy, to_ccy, ts):
            seen["ts"] = ts
            return 1.5

        stamp = datetime.datetime(2024, 1, 1)
        result = convert(CurrencyAmount(10.0, "EUR", stamp), "USD", rate_fn)
        self.assertEqual(result.amount, 15.0)
        self.assertEqual(seen["ts"], stamp)


if __name__ == "__main__":
    unittest.main()
