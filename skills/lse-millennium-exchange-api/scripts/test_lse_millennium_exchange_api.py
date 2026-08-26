import unittest
from decimal import Decimal

from lse_millennium_exchange_api import (
    GBX,
    RTS11_TICK_TABLE,
    STATUS_INVALID_CURRENCY,
    STATUS_INVALID_PRICE,
    STATUS_INVALID_QUANTITY,
    STATUS_INVALID_SIDE,
    STATUS_INVALID_TICK_SIZE,
    STATUS_INVALID_TIDM,
    STATUS_REFERENCE_DATA_REQUIRED,
    STATUS_VALIDATED,
    TICK_SOURCE_REFERENCE_DATA,
    TICK_SOURCE_RTS11_FLOOR,
    InvalidTidmError,
    LiquidityBandRequiredError,
    LseGatewayError,
    LseInstrument,
    LseMillenniumExchangeApiEngine,
    LseOrderPayload,
    PriceTickBand,
    UnknownInstrumentError,
    is_on_tick,
    liquidity_band_for_adnt,
    normalise_tidm,
    rts11_floor_tick,
)


class TestTidmValidation(unittest.TestCase):
    """The Exchange defines TIDM as STRING(4) (MIT401) - not 'letters only'."""

    def test_plain_alphabetic_tidm_is_normalised(self):
        self.assertEqual(normalise_tidm(" shel "), "SHEL")

    def test_tidm_containing_a_full_stop_is_accepted(self):
        # Regression: an isalpha() check rejects BT.A and BP., both live LSE mnemonics.
        for tidm in ("BT.A", "BP.", "RR."):
            with self.subTest(tidm=tidm):
                self.assertEqual(normalise_tidm(tidm), tidm)

    def test_tidm_starting_with_a_digit_is_accepted(self):
        # Regression: 3IN (3i Infrastructure plc) is a live LSE mnemonic.
        self.assertEqual(normalise_tidm("3in"), "3IN")

    def test_five_character_symbol_is_rejected(self):
        # The field is STRING(4); the old implementation allowed 5.
        with self.assertRaises(InvalidTidmError):
            normalise_tidm("ABCDE")

    def test_ric_and_bloomberg_codes_are_rejected(self):
        for symbol in ("SHEL.L", "SHEL LN"):
            with self.subTest(symbol=symbol):
                with self.assertRaises(InvalidTidmError):
                    normalise_tidm(symbol)

    def test_empty_and_non_string_tidms_are_rejected(self):
        with self.assertRaises(InvalidTidmError):
            normalise_tidm("   ")
        with self.assertRaises(InvalidTidmError):
            normalise_tidm(None)


class TestRts11FloorTick(unittest.TestCase):
    """Values taken cell by cell from the RTS 11 Annex as published by the FCA."""

    def test_table_shape_matches_the_annex(self):
        self.assertEqual(len(RTS11_TICK_TABLE), 19)
        for _upper, ticks in RTS11_TICK_TABLE:
            self.assertEqual(len(ticks), 6)

    def test_liquid_share_at_shell_price_takes_a_half_penny_tick(self):
        # 3385 GBX sits in the 2000 <= P < 5000 row; band 6 (ADNT >= 9000) -> 0.5.
        # A price-only ladder returns 1.00 here, twice the tick LSE actually quotes.
        self.assertEqual(rts11_floor_tick("3385", 6), Decimal("0.5"))

    def test_same_price_in_an_illiquid_band_takes_a_much_coarser_tick(self):
        # The point of the two-dimensional table: price alone does not determine the tick.
        self.assertEqual(rts11_floor_tick("3385", 1), Decimal("20"))
        self.assertEqual(rts11_floor_tick("3385", 3), Decimal("5"))

    def test_astrazeneca_price_band(self):
        # 12228 GBX -> 10000 <= P < 20000 row, band 6 -> 2.
        self.assertEqual(rts11_floor_tick("12228", 6), Decimal("2"))

    def test_sub_penny_bottom_row(self):
        self.assertEqual(rts11_floor_tick("0.05", 1), Decimal("0.0005"))
        self.assertEqual(rts11_floor_tick("0.05", 6), Decimal("0.0001"))

    def test_top_row_is_unbounded_above(self):
        self.assertEqual(rts11_floor_tick("75000", 6), Decimal("10"))
        self.assertEqual(rts11_floor_tick("10000000", 1), Decimal("500"))

    def test_price_band_boundaries_are_lower_inclusive(self):
        # 200 belongs to [200, 500), not to [100, 200).
        self.assertEqual(rts11_floor_tick("200", 6), Decimal("0.05"))
        self.assertEqual(rts11_floor_tick("199.99", 6), Decimal("0.02"))

    def test_non_positive_and_out_of_range_band_inputs_raise(self):
        with self.assertRaises(LseGatewayError):
            rts11_floor_tick("0", 6)
        with self.assertRaises(LseGatewayError):
            rts11_floor_tick("-3385", 6)
        with self.assertRaises(LiquidityBandRequiredError):
            rts11_floor_tick("3385", 0)
        with self.assertRaises(LiquidityBandRequiredError):
            rts11_floor_tick("3385", 7)


class TestLiquidityBandForAdnt(unittest.TestCase):

    def test_band_boundaries_are_lower_inclusive(self):
        self.assertEqual(liquidity_band_for_adnt(0), 1)
        self.assertEqual(liquidity_band_for_adnt(9.99), 1)
        self.assertEqual(liquidity_band_for_adnt(10), 2)
        self.assertEqual(liquidity_band_for_adnt(80), 3)
        self.assertEqual(liquidity_band_for_adnt(600), 4)
        self.assertEqual(liquidity_band_for_adnt(2000), 5)
        self.assertEqual(liquidity_band_for_adnt(9000), 6)
        self.assertEqual(liquidity_band_for_adnt(1_000_000), 6)

    def test_negative_adnt_is_rejected(self):
        with self.assertRaises(LseGatewayError):
            liquidity_band_for_adnt(-1)


class TestInstrumentReferenceData(unittest.TestCase):

    def test_currency_is_normalised_and_required(self):
        instrument = LseInstrument(tidm="shel", currency=" gbx ")
        self.assertEqual(instrument.tidm, "SHEL")
        self.assertEqual(instrument.currency, GBX)
        self.assertTrue(instrument.is_pence_quoted)
        with self.assertRaises(LseGatewayError):
            LseInstrument(tidm="SHEL", currency="  ")

    def test_liquidity_band_must_be_one_to_six(self):
        with self.assertRaises(LseGatewayError):
            LseInstrument(tidm="SHEL", currency=GBX, liquidity_band=7)

    def test_overlapping_price_tick_bands_are_rejected_at_registration(self):
        with self.assertRaises(LseGatewayError):
            LseInstrument(
                tidm="TEST",
                currency=GBX,
                price_tick_table=(
                    PriceTickBand("0", "1000", "0.5"),
                    PriceTickBand("900", "5000", "1"),
                ),
            )

    def test_inverted_or_non_positive_tick_bands_are_rejected(self):
        with self.assertRaises(LseGatewayError):
            PriceTickBand("100", "50", "0.5")
        with self.assertRaises(LseGatewayError):
            PriceTickBand("0", "100", "0")


class TestTickSourceSelection(unittest.TestCase):

    def setUp(self):
        self.engine = LseMillenniumExchangeApiEngine(instruments=())

    def test_instrument_tick_table_wins_over_the_regulatory_floor(self):
        # RTS 11 is a floor ("equal to or greater than"), so a venue may quote coarser.
        instrument = LseInstrument(
            tidm="TEST",
            currency=GBX,
            liquidity_band=6,
            price_tick_table=(PriceTickBand("0", "1000000", "1"),),
            price_tick_table_id="EXAMPLE",
        )
        self.engine.register_instrument(instrument)
        tick, source = self.engine.active_tick_size(instrument, "3385")
        self.assertEqual(tick, Decimal("1"))
        self.assertEqual(source, TICK_SOURCE_REFERENCE_DATA)

    def test_price_outside_every_band_of_a_loaded_table_fails_closed(self):
        instrument = LseInstrument(
            tidm="TEST",
            currency=GBX,
            liquidity_band=6,
            price_tick_table=(PriceTickBand("0", "1000", "0.5"),),
        )
        with self.assertRaises(LseGatewayError):
            self.engine.active_tick_size(instrument, "5000")

    def test_instrument_without_band_or_table_fails_closed(self):
        instrument = LseInstrument(tidm="IGLN", currency="USD")
        with self.assertRaises(LiquidityBandRequiredError):
            self.engine.active_tick_size(instrument, "90.05")

    def test_unknown_tidm_is_not_silently_defaulted(self):
        with self.assertRaises(UnknownInstrumentError):
            self.engine.resolve_instrument("SHEL")


class TestOrderValidation(unittest.TestCase):

    def setUp(self):
        self.engine = LseMillenniumExchangeApiEngine()

    def test_shell_order_on_the_half_penny_tick_validates(self):
        # 1,000 shares at 3,384.5 GBX = GBP 33,845.00.
        report = self.engine.validate_and_route_order(
            LseOrderPayload("SHEL", "BUY", price=3384.5, quantity=1000)
        )
        self.assertEqual(report.status, STATUS_VALIDATED)
        self.assertTrue(report.ready_to_send)
        self.assertEqual(report.applicable_tick_size, Decimal("0.5"))
        self.assertEqual(report.tick_size_source, TICK_SOURCE_RTS11_FLOOR)
        self.assertEqual(report.liquidity_band, 6)
        self.assertEqual(report.notional_gbp, Decimal("33845.00"))
        self.assertEqual(report.notional_quoted, Decimal("3384500.0"))
        self.assertFalse(report.tick_below_rts11_floor)

    def test_quarter_penny_price_is_off_tick(self):
        report = self.engine.validate_and_route_order(
            LseOrderPayload("SHEL", "BUY", price="3385.25", quantity=1000)
        )
        self.assertEqual(report.status, STATUS_INVALID_TICK_SIZE)
        self.assertFalse(report.is_price_tick_valid)
        self.assertFalse(report.ready_to_send)

    def test_half_penny_price_the_old_ladder_rejected_now_validates(self):
        # Regression against the previous 7-tier price-only schedule, which gave SHEL a
        # 1.00 GBX tick and so accepted only whole pence. 3384.5 is on tick; a naive
        # ladder rejects it. Both directions are checked so the fix cannot silently
        # invert.
        on_tick = self.engine.validate_and_route_order(
            LseOrderPayload("SHEL", "SELL", price="3384.5", quantity=100)
        )
        self.assertEqual(on_tick.status, STATUS_VALIDATED)

    def test_float_price_is_read_through_its_shortest_repr(self):
        # 205.3 is not exactly representable; a naive Decimal(float) tick test fails it.
        report = self.engine.validate_and_route_order(
            LseOrderPayload("BT.A", "SELL", price=205.3, quantity=500)
        )
        self.assertEqual(report.status, STATUS_VALIDATED)
        self.assertEqual(report.applicable_tick_size, Decimal("0.1"))
        self.assertEqual(report.notional_gbp, Decimal("1026.50"))

    def test_gbp_scaled_price_is_rejected_on_currency_not_silently_priced(self):
        report = self.engine.validate_and_route_order(
            LseOrderPayload("SHEL", "BUY", price=33.845, quantity=1000, currency="GBP")
        )
        self.assertEqual(report.status, STATUS_INVALID_CURRENCY)
        self.assertIn("100x", report.audit_notes)
        self.assertIsNone(report.notional_gbp)

    def test_usd_quoted_lse_line_rejects_a_gbx_payload(self):
        # LSE is not a GBX-only venue: IGLN is quoted in USD.
        report = self.engine.validate_and_route_order(
            LseOrderPayload("IGLN", "BUY", price="90.05", quantity=10, currency=GBX)
        )
        self.assertEqual(report.status, STATUS_INVALID_CURRENCY)

    def test_usd_quoted_line_without_a_tick_table_fails_closed(self):
        report = self.engine.validate_and_route_order(
            LseOrderPayload("IGLN", "BUY", price="90.05", quantity=10, currency="USD")
        )
        self.assertEqual(report.status, STATUS_REFERENCE_DATA_REQUIRED)
        self.assertFalse(report.ready_to_send)

    def test_notional_is_not_converted_for_a_non_sterling_line(self):
        engine = LseMillenniumExchangeApiEngine(
            instruments=(
                LseInstrument(
                    tidm="IGLN",
                    currency="USD",
                    price_tick_table=(PriceTickBand("0", "1000", "0.01"),),
                ),
            )
        )
        report = engine.validate_and_route_order(
            LseOrderPayload("IGLN", "BUY", price="90.05", quantity=10, currency="USD")
        )
        self.assertEqual(report.status, STATUS_VALIDATED)
        self.assertEqual(report.notional_quoted, Decimal("900.50"))
        self.assertIsNone(report.notional_gbp)

    def test_negative_price_is_rejected_even_though_it_is_on_tick(self):
        # -3385.0 % 0.5 == 0, so the modulo test alone would pass it.
        report = self.engine.validate_and_route_order(
            LseOrderPayload("SHEL", "BUY", price="-3385.0", quantity=1000)
        )
        self.assertEqual(report.status, STATUS_INVALID_PRICE)

    def test_nan_price_does_not_propagate_into_the_report(self):
        report = self.engine.validate_and_route_order(
            LseOrderPayload("SHEL", "BUY", price=float("nan"), quantity=1000)
        )
        self.assertEqual(report.status, STATUS_INVALID_PRICE)
        self.assertIsNone(report.notional_gbp)

    def test_non_positive_and_non_integer_quantities_are_rejected(self):
        for quantity in (0, -100, 10.5, True):
            with self.subTest(quantity=quantity):
                report = self.engine.validate_and_route_order(
                    LseOrderPayload("SHEL", "BUY", price="3385.0", quantity=quantity)
                )
                self.assertEqual(report.status, STATUS_INVALID_QUANTITY)

    def test_unknown_side_is_rejected(self):
        report = self.engine.validate_and_route_order(
            LseOrderPayload("SHEL", "SHORT", price="3385.0", quantity=100)
        )
        self.assertEqual(report.status, STATUS_INVALID_SIDE)

    def test_vendor_symbol_is_rejected_as_an_invalid_tidm(self):
        report = self.engine.validate_and_route_order(
            LseOrderPayload("SHEL.L", "BUY", price="3385.0", quantity=100)
        )
        self.assertEqual(report.status, STATUS_INVALID_TIDM)

    def test_unregistered_tidm_asks_for_reference_data(self):
        report = self.engine.validate_and_route_order(
            LseOrderPayload("HSBA", "BUY", price="900.0", quantity=100)
        )
        self.assertEqual(report.status, STATUS_REFERENCE_DATA_REQUIRED)

    def test_tick_finer_than_the_rts11_floor_validates_but_is_flagged(self):
        # UK RTS 11 Article 2(2A) permits a third-country primary market's smaller tick,
        # so this is a warning, not a rejection - but it must not pass unremarked.
        engine = LseMillenniumExchangeApiEngine(
            instruments=(
                LseInstrument(
                    tidm="TEST",
                    currency=GBX,
                    liquidity_band=1,
                    price_tick_table=(PriceTickBand("0", "1000000", "0.01"),),
                ),
            )
        )
        report = engine.validate_and_route_order(
            LseOrderPayload("TEST", "BUY", price="3385.01", quantity=100)
        )
        self.assertEqual(report.status, STATUS_VALIDATED)
        self.assertTrue(report.tick_below_rts11_floor)
        self.assertEqual(report.rts11_floor_tick, Decimal("20"))
        self.assertTrue(report.warnings)

    def test_implausibly_large_price_returns_a_verdict_not_an_exception(self):
        # Regression: the default 28-digit Decimal context raises DivisionImpossible on
        # Decimal('1E+40') % Decimal('10'), which escaped the order path unhandled.
        report = self.engine.validate_and_route_order(
            LseOrderPayload("SHEL", "BUY", price="1E+40", quantity=10 ** 18)
        )
        self.assertEqual(report.status, STATUS_VALIDATED)
        # Decimal comparison is numeric, so this holds against the 0.01-quantized value.
        self.assertEqual(report.notional_gbp, Decimal("1E+56"))

    def test_tick_test_stays_exact_beyond_default_precision(self):
        self.assertFalse(is_on_tick(Decimal("1" + "0" * 39 + "5"), Decimal("10")))
        self.assertTrue(is_on_tick(Decimal("1" + "0" * 40), Decimal("10")))

    def test_notional_is_exact_not_rounded_to_context_precision(self):
        report = self.engine.validate_and_route_order(
            LseOrderPayload("SHEL", "BUY", price="1.0000000001E+30", quantity=7)
        )
        self.assertEqual(report.notional_quoted, Decimal("7.0000000007E+30"))

    def test_payload_is_not_mutated(self):
        payload = LseOrderPayload("shel ", "buy", price="3385.0", quantity=100)
        self.engine.validate_and_route_order(payload)
        self.assertEqual(payload.tidm, "shel ")
        self.assertEqual(payload.side, "buy")


if __name__ == "__main__":
    unittest.main()
