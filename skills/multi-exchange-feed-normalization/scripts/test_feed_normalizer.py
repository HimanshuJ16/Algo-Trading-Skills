"""
Behavioural tests for multi-exchange-feed-normalization.

Expected timestamp values are computed from an independent source (an explicit
``datetime`` with a declared offset, converted by the standard library) rather
than by re-running the module's own coercion, so a wrong coercion fails instead
of agreeing with itself.

Several tests are regression tests for defects that the pre-2.0 implementation
had, and are marked ``REGRESSION``: each one fails against the old behaviour and
passes against the fix.
"""
import datetime
import logging
import time
import unittest

from feed_normalizer import (
    NormalizationError,
    NormalizedSide,
    TickNormalizerRegistry,
    TimestampUnit,
    UnifiedTick,
)

UTC = datetime.timezone.utc
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

# Independently derived: 2023-11-14T22:13:20Z == 1_700_000_000 epoch seconds.
T_EPOCH_S = datetime.datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC).timestamp()
T_EPOCH_MS = int(T_EPOCH_S * 1_000)
T_EPOCH_US = int(T_EPOCH_S * 1_000_000)
T_EPOCH_NS = int(T_EPOCH_S * 1_000_000_000)


def binance_payload(**overrides):
    """A well-formed Binance spot aggTrade payload."""
    payload = {"s": "BTCUSDT", "p": "65000.50", "q": "0.15", "T": T_EPOCH_MS, "m": False}
    payload.update(overrides)
    return {k: v for k, v in payload.items() if v is not None}


def coinbase_payload(**overrides):
    """A well-formed Coinbase Exchange `matches` payload."""
    payload = {
        "product_id": "BTC-USD",
        "price": "65000.50",
        "size": "0.15",
        "side": "sell",
        "time": "2023-11-14T22:13:20.028459Z",
    }
    payload.update(overrides)
    return {k: v for k, v in payload.items() if v is not None}


def zerodha_payload(**overrides):
    """A well-formed pykiteconnect full-mode tick."""
    payload = {
        "tradingsymbol": "INFY",
        "last_price": 1500.25,
        "last_traded_quantity": 50,
        "volume_traded": 12_500_000,
        "last_trade_time": datetime.datetime(2023, 11, 14, 22, 13, 20, tzinfo=IST),
    }
    payload.update(overrides)
    return {k: v for k, v in payload.items() if v is not None}


class BaseCase(unittest.TestCase):
    def setUp(self):
        self.normalizer = TickNormalizerRegistry()
        self.normalizer.register_symbol_mapping("binance", "BTCUSDT", "BTC/USD")
        self.normalizer.register_symbol_mapping("coinbase", "BTC-USD", "BTC/USD")
        self.normalizer.register_symbol_mapping("zerodha", "INFY", "INFY")
        # Failure paths log warnings by design; keep test output clean.
        logging.getLogger("feed_normalizer").setLevel(logging.ERROR)


class TestHappyPath(BaseCase):
    def test_binance_normalization(self):
        tick = self.normalizer.normalize("binance", binance_payload())
        self.assertEqual(tick.symbol, "BTC/USD")
        self.assertEqual(tick.venue, "binance")
        self.assertEqual(tick.price, 65000.50)
        self.assertEqual(tick.quantity, 0.15)
        self.assertEqual(tick.side, NormalizedSide.BUY)
        self.assertEqual(tick.exchange_timestamp, T_EPOCH_S)

    def test_coinbase_normalization(self):
        tick = self.normalizer.normalize("coinbase", coinbase_payload())
        self.assertEqual(tick.symbol, "BTC/USD")
        self.assertEqual(tick.venue, "coinbase")
        self.assertEqual(tick.price, 65000.50)
        self.assertEqual(tick.quantity, 0.15)
        self.assertAlmostEqual(tick.exchange_timestamp, T_EPOCH_S + 0.028459, places=6)

    def test_zerodha_normalization(self):
        tick = self.normalizer.normalize("zerodha", zerodha_payload())
        self.assertEqual(tick.symbol, "INFY")
        self.assertEqual(tick.venue, "zerodha")
        self.assertEqual(tick.price, 1500.25)
        self.assertEqual(tick.quantity, 50.0)
        self.assertEqual(tick.side, NormalizedSide.UNKNOWN)

    def test_all_venues_share_one_schema(self):
        """The whole point of the skill: identical field names and types out."""
        ticks = [
            self.normalizer.normalize("binance", binance_payload()),
            self.normalizer.normalize("coinbase", coinbase_payload()),
            self.normalizer.normalize("zerodha", zerodha_payload()),
        ]
        for tick in ticks:
            self.assertIsInstance(tick, UnifiedTick)
            self.assertIsInstance(tick.symbol, str)
            self.assertIsInstance(tick.price, float)
            self.assertIsInstance(tick.quantity, float)
            self.assertIsInstance(tick.side, NormalizedSide)
            self.assertIsInstance(tick.exchange_timestamp, float)
            self.assertIsInstance(tick.receipt_timestamp, float)

    def test_venue_key_is_case_insensitive(self):
        tick = self.normalizer.normalize("BINANCE", binance_payload())
        self.assertEqual(tick.venue, "binance")


class TestAggressorSideConvention(BaseCase):
    """
    REGRESSION (G1). Coinbase documents `side` as the *maker* order side, while
    Binance's `m` yields the *taker* side. Passing Coinbase's field through
    unchanged flips the sign of cross-venue order-flow imbalance.
    """

    def test_binance_buyer_is_maker_means_seller_aggressed(self):
        tick = self.normalizer.normalize("binance", binance_payload(m=True))
        self.assertEqual(tick.side, NormalizedSide.SELL)

    def test_binance_buyer_is_taker_means_buyer_aggressed(self):
        tick = self.normalizer.normalize("binance", binance_payload(m=False))
        self.assertEqual(tick.side, NormalizedSide.BUY)

    def test_coinbase_maker_side_is_inverted_to_aggressor(self):
        sell_maker = self.normalizer.normalize("coinbase", coinbase_payload(side="sell"))
        buy_maker = self.normalizer.normalize("coinbase", coinbase_payload(side="BUY"))
        self.assertEqual(sell_maker.side, NormalizedSide.BUY)
        self.assertEqual(buy_maker.side, NormalizedSide.SELL)

    def test_economically_identical_trades_agree_across_venues(self):
        """
        A resting bid being hit is a SELL-aggressed trade on both venues.
        Binance encodes that as m=True; Coinbase as maker side "buy".
        """
        binance = self.normalizer.normalize("binance", binance_payload(m=True))
        coinbase = self.normalizer.normalize("coinbase", coinbase_payload(side="buy"))
        self.assertEqual(binance.side, NormalizedSide.SELL)
        self.assertEqual(coinbase.side, binance.side)

    def test_missing_side_flag_is_unknown_not_a_default(self):
        """REGRESSION (G7): a missing `m` used to default silently to BUY."""
        tick = self.normalizer.normalize("binance", binance_payload(m=None))
        self.assertEqual(tick.side, NormalizedSide.UNKNOWN)

    def test_missing_coinbase_side_is_unknown(self):
        tick = self.normalizer.normalize("coinbase", coinbase_payload(side=None))
        self.assertEqual(tick.side, NormalizedSide.UNKNOWN)

    def test_unrecognised_coinbase_side_is_unknown(self):
        tick = self.normalizer.normalize("coinbase", coinbase_payload(side="cross"))
        self.assertEqual(tick.side, NormalizedSide.UNKNOWN)

    def test_non_bool_binance_maker_flag_is_rejected(self):
        with self.assertRaises(NormalizationError):
            self.normalizer.normalize("binance", binance_payload(m="true"))


class TestZerodhaFieldNames(BaseCase):
    """REGRESSION (G2). KiteTicker emits last_traded_quantity / volume_traded."""

    def test_websocket_field_name_is_read(self):
        tick = self.normalizer.normalize("zerodha", zerodha_payload())
        self.assertEqual(tick.quantity, 50.0)

    def test_rest_quote_field_name_is_also_accepted(self):
        payload = zerodha_payload(last_traded_quantity=None, last_quantity=50)
        tick = self.normalizer.normalize("zerodha", payload)
        self.assertEqual(tick.quantity, 50.0)

    def test_cumulative_volume_is_never_used_as_trade_size(self):
        """
        With no per-trade size present the parser must fail, not silently report
        the 12.5M-share cumulative session volume as one trade.
        """
        payload = zerodha_payload(last_traded_quantity=None)
        with self.assertRaises(NormalizationError):
            self.normalizer.normalize("zerodha", payload)

    def test_naive_kite_datetime_uses_configured_zone(self):
        """
        REGRESSION (G6): a datetime object was an unhandled type and silently
        became time.time().
        """
        naive = datetime.datetime(2023, 11, 14, 22, 13, 20)
        utc_reg = TickNormalizerRegistry(strict_symbols=False, naive_timestamp_tz=UTC)
        ist_reg = TickNormalizerRegistry(strict_symbols=False, naive_timestamp_tz=IST)
        utc_tick = utc_reg.normalize("zerodha", zerodha_payload(last_trade_time=naive))
        ist_tick = ist_reg.normalize("zerodha", zerodha_payload(last_trade_time=naive))
        self.assertEqual(utc_tick.exchange_timestamp, T_EPOCH_S)
        # IST is UTC+5:30, so the same wall-clock reading is 19800s earlier.
        self.assertEqual(ist_tick.exchange_timestamp, T_EPOCH_S - 19800.0)

    def test_aware_datetime_ignores_the_naive_policy(self):
        aware = datetime.datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)
        reg = TickNormalizerRegistry(strict_symbols=False, naive_timestamp_tz=IST)
        tick = reg.normalize("zerodha", zerodha_payload(last_trade_time=aware))
        self.assertEqual(tick.exchange_timestamp, T_EPOCH_S)

    def test_exchange_timestamp_is_a_fallback_for_last_trade_time(self):
        payload = zerodha_payload(
            last_trade_time=None,
            exchange_timestamp=datetime.datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC),
        )
        tick = self.normalizer.normalize("zerodha", payload)
        self.assertEqual(tick.exchange_timestamp, T_EPOCH_S)


class TestTimestampCoercion(BaseCase):
    """REGRESSION (G4, G5). Units are resolved, never guessed by one threshold."""

    def test_each_epoch_unit_resolves_to_the_same_instant(self):
        for raw in (int(T_EPOCH_S), T_EPOCH_MS, T_EPOCH_US, T_EPOCH_NS):
            with self.subTest(raw=raw):
                tick = self.normalizer.normalize("binance", binance_payload(T=raw))
                self.assertAlmostEqual(tick.exchange_timestamp, T_EPOCH_S, places=6)

    def test_binance_microsecond_stream_is_not_thrown_into_the_far_future(self):
        """
        Binance serves these streams in microseconds under timeUnit=MICROSECOND.
        The old `>1e11 -> /1000` rule turned that into ~year 55839 silently.
        """
        tick = self.normalizer.normalize("binance", binance_payload(T=T_EPOCH_US))
        self.assertAlmostEqual(tick.exchange_timestamp, T_EPOCH_S, places=6)
        self.assertLess(tick.exchange_timestamp, 4_102_444_800.0)

    def test_float_seconds_string_is_parsed_not_discarded(self):
        """
        REGRESSION: "1700000000.123" fails str.isdigit(), so the old code fell
        through to ISO parsing, raised, and substituted time.time().
        """
        tick = self.normalizer.normalize("binance", binance_payload(T="1700000000.123"))
        self.assertAlmostEqual(tick.exchange_timestamp, T_EPOCH_S + 0.123, places=6)

    def test_iso_string_with_offset_is_exact(self):
        tick = self.normalizer.normalize(
            "coinbase", coinbase_payload(time="2023-11-15T03:43:20+05:30")
        )
        self.assertEqual(tick.exchange_timestamp, T_EPOCH_S)

    def test_iso_string_with_nanosecond_fraction_is_accepted(self):
        tick = self.normalizer.normalize(
            "coinbase", coinbase_payload(time="2023-11-14T22:13:20.123456789Z")
        )
        self.assertAlmostEqual(tick.exchange_timestamp, T_EPOCH_S + 0.123456, places=6)

    def test_declared_unit_rejects_a_mismatched_magnitude(self):
        reg = TickNormalizerRegistry(strict_symbols=False)
        reg.register_parser("binance", reg.parse_binance, TimestampUnit.SECONDS)
        with self.assertRaises(NormalizationError):
            reg.normalize("binance", binance_payload(T=T_EPOCH_MS))

    def test_declared_unit_accepts_the_matching_magnitude(self):
        reg = TickNormalizerRegistry(strict_symbols=False)
        reg.register_parser("binance", reg.parse_binance, TimestampUnit.MILLISECONDS)
        tick = reg.normalize("binance", binance_payload(T=T_EPOCH_MS))
        self.assertEqual(tick.exchange_timestamp, T_EPOCH_S)

    def test_missing_timestamp_raises_instead_of_stamping_now(self):
        with self.assertRaises(NormalizationError):
            self.normalizer.normalize("binance", binance_payload(T=None, E=None))

    def test_unparseable_timestamp_raises_instead_of_stamping_now(self):
        for bad in ("not-a-time", "", "nan", "inf", float("nan"), True, [1]):
            with self.subTest(bad=bad):
                with self.assertRaises(NormalizationError):
                    self.normalizer.normalize("binance", binance_payload(T=bad))

    def test_out_of_band_number_is_rejected(self):
        """A 1970-era or year-5000 value matches no unit band."""
        for bad in (1.0, 12345.0, 1e30):
            with self.subTest(bad=bad):
                with self.assertRaises(NormalizationError):
                    self.normalizer.normalize("binance", binance_payload(T=bad))

    def test_binance_falls_back_to_event_time_when_trade_time_absent(self):
        tick = self.normalizer.normalize("binance", binance_payload(T=None, E=T_EPOCH_MS))
        self.assertEqual(tick.exchange_timestamp, T_EPOCH_S)


class TestNumericValidation(BaseCase):
    """REGRESSION (G3, G11). Missing price used to become 0.0 silently."""

    def test_missing_price_raises(self):
        with self.assertRaises(NormalizationError):
            self.normalizer.normalize("binance", binance_payload(p=None))

    def test_zero_price_raises_by_default(self):
        with self.assertRaises(NormalizationError):
            self.normalizer.normalize("binance", binance_payload(p="0"))

    def test_negative_price_raises_by_default(self):
        with self.assertRaises(NormalizationError):
            self.normalizer.normalize("binance", binance_payload(p="-1.5"))

    def test_negative_price_allowed_when_explicitly_enabled(self):
        reg = TickNormalizerRegistry(strict_symbols=False, allow_non_positive_price=True)
        tick = reg.normalize("binance", binance_payload(p="-37.63"))
        self.assertEqual(tick.price, -37.63)

    def test_non_finite_price_raises(self):
        for bad in ("nan", "inf", "-inf", float("nan"), float("inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(NormalizationError):
                    self.normalizer.normalize("binance", binance_payload(p=bad))

    def test_malformed_numeric_raises_normalization_error_not_bare_value_error(self):
        """
        A feed handler catches one exception type at the venue boundary; the old
        code leaked a raw ValueError from float().
        """
        with self.assertRaises(NormalizationError):
            self.normalizer.normalize("binance", binance_payload(p="6.5e4.2"))

    def test_missing_quantity_raises(self):
        with self.assertRaises(NormalizationError):
            self.normalizer.normalize("binance", binance_payload(q=None))

    def test_zero_quantity_raises(self):
        """A zero-size print is a data error, and divides by zero in VWAP."""
        with self.assertRaises(NormalizationError):
            self.normalizer.normalize("binance", binance_payload(q="0"))

    def test_negative_quantity_raises(self):
        with self.assertRaises(NormalizationError):
            self.normalizer.normalize("binance", binance_payload(q="-1"))

    def test_bool_is_not_accepted_as_a_number(self):
        """bool is an int subclass; True must not silently become price 1.0."""
        with self.assertRaises(NormalizationError):
            self.normalizer.normalize("binance", binance_payload(p=True))


class TestSymbolMapping(BaseCase):
    """REGRESSION (G8). Unmapped symbols used to pass through silently."""

    def test_registered_mapping_is_applied(self):
        self.assertEqual(self.normalizer.get_canonical_symbol("binance", "BTCUSDT"), "BTC/USD")

    def test_venue_symbols_converge_on_one_canonical_symbol(self):
        binance = self.normalizer.normalize("binance", binance_payload())
        coinbase = self.normalizer.normalize("coinbase", coinbase_payload())
        self.assertEqual(binance.symbol, coinbase.symbol)

    def test_lookup_is_case_insensitive_on_both_venue_and_ticker(self):
        self.assertEqual(self.normalizer.get_canonical_symbol("BINANCE", "btcusdt"), "BTC/USD")

    def test_unmapped_symbol_raises_in_strict_mode(self):
        with self.assertRaises(NormalizationError):
            self.normalizer.normalize("binance", binance_payload(s="ETHUSDT"))

    def test_unmapped_symbol_passes_through_when_strictness_is_waived(self):
        reg = TickNormalizerRegistry(strict_symbols=False)
        tick = reg.normalize("binance", binance_payload(s="ETHUSDT"))
        self.assertEqual(tick.symbol, "ETHUSDT")

    def test_unmapped_symbols_do_not_consolidate(self):
        """Documents exactly what strict mode is protecting against."""
        reg = TickNormalizerRegistry(strict_symbols=False)
        binance = reg.normalize("binance", binance_payload())
        coinbase = reg.normalize("coinbase", coinbase_payload())
        self.assertNotEqual(binance.symbol, coinbase.symbol)

    def test_empty_symbol_raises(self):
        with self.assertRaises(NormalizationError):
            self.normalizer.normalize("binance", binance_payload(s=""))

    def test_empty_mapping_arguments_are_rejected(self):
        with self.assertRaises(NormalizationError):
            self.normalizer.register_symbol_mapping("binance", "", "BTC/USD")


class TestReceiptTimestamp(BaseCase):
    """REGRESSION (G9). Arrival time must be capturable at socket read."""

    def test_supplied_receipt_timestamp_is_preserved(self):
        arrival = T_EPOCH_S + 0.004
        tick = self.normalizer.normalize("binance", binance_payload(), receipt_timestamp=arrival)
        self.assertEqual(tick.receipt_timestamp, arrival)

    def test_feed_latency_is_measurable_from_the_supplied_arrival_time(self):
        arrival = T_EPOCH_S + 0.004
        tick = self.normalizer.normalize("binance", binance_payload(), receipt_timestamp=arrival)
        self.assertAlmostEqual(tick.receipt_timestamp - tick.exchange_timestamp, 0.004, places=6)

    def test_omitted_receipt_timestamp_defaults_to_now(self):
        # Bracketed with the same clock the default uses; datetime.now().timestamp()
        # rounds differently and would make this flaky at sub-microsecond scale.
        before = time.time()
        tick = self.normalizer.normalize("binance", binance_payload())
        after = time.time()
        self.assertGreaterEqual(tick.receipt_timestamp, before)
        self.assertLessEqual(tick.receipt_timestamp, after)


class TestRegistryDispatch(BaseCase):
    """REGRESSION (G10). The registry must actually be extensible."""

    def test_builtin_venues_are_reported(self):
        self.assertEqual(self.normalizer.supported_venues(), ("binance", "coinbase", "zerodha"))

    def test_unregistered_venue_raises_and_names_what_is_available(self):
        with self.assertRaises(NormalizationError) as ctx:
            self.normalizer.normalize("kraken", {})
        self.assertIn("binance", str(ctx.exception))

    def test_a_custom_venue_parser_can_be_registered(self):
        def parse_kraken(payload, receipt_timestamp=None):
            return UnifiedTick(
                symbol=self.normalizer.get_canonical_symbol("kraken", payload["pair"]),
                venue="kraken",
                price=float(payload["price"]),
                quantity=float(payload["volume"]),
                side=NormalizedSide.UNKNOWN,
                exchange_timestamp=self.normalizer._coerce_timestamp(payload["time"], "time"),
                receipt_timestamp=receipt_timestamp or T_EPOCH_S,
            )

        self.normalizer.register_parser("kraken", parse_kraken)
        self.normalizer.register_symbol_mapping("kraken", "XBT/USD", "BTC/USD")
        tick = self.normalizer.normalize(
            "kraken",
            {"pair": "XBT/USD", "price": "65000.50", "volume": "0.15", "time": "1700000000.123"},
        )
        self.assertEqual(tick.symbol, "BTC/USD")
        self.assertIn("kraken", self.normalizer.supported_venues())

    def test_non_callable_parser_is_rejected(self):
        with self.assertRaises(NormalizationError):
            self.normalizer.register_parser("kraken", "not-a-function")

    def test_non_mapping_payload_is_rejected(self):
        for bad in (None, "payload", ["s", "BTCUSDT"], 42):
            with self.subTest(bad=bad):
                with self.assertRaises(NormalizationError):
                    self.normalizer.normalize("binance", bad)


class TestUnifiedTickInvariants(unittest.TestCase):
    """A custom parser must not be able to emit a malformed tick."""

    def _tick(self, **overrides):
        kwargs = {
            "symbol": "BTC/USD",
            "venue": "test",
            "price": 100.0,
            "quantity": 1.0,
            "side": NormalizedSide.BUY,
            "exchange_timestamp": T_EPOCH_S,
            "receipt_timestamp": T_EPOCH_S,
        }
        kwargs.update(overrides)
        return UnifiedTick(**kwargs)

    def test_well_formed_tick_is_accepted(self):
        self.assertEqual(self._tick().price, 100.0)

    def test_integer_inputs_are_normalised_to_float(self):
        tick = self._tick(price=100, quantity=1)
        self.assertIsInstance(tick.price, float)
        self.assertIsInstance(tick.quantity, float)

    def test_nan_price_is_rejected(self):
        with self.assertRaises(NormalizationError):
            self._tick(price=float("nan"))

    def test_zero_quantity_is_rejected(self):
        with self.assertRaises(NormalizationError):
            self._tick(quantity=0.0)

    def test_millisecond_timestamp_passed_as_seconds_is_rejected(self):
        with self.assertRaises(NormalizationError):
            self._tick(exchange_timestamp=T_EPOCH_MS)

    def test_empty_symbol_is_rejected(self):
        with self.assertRaises(NormalizationError):
            self._tick(symbol="")

    def test_raw_string_side_is_rejected(self):
        with self.assertRaises(NormalizationError):
            self._tick(side="BUY")


if __name__ == "__main__":
    unittest.main()
