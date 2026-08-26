import unittest

from jse_south_africa_api_integration import (
    MAX_ORDER_QUANTITY,
    JseOrderPayload,
    JseSouthAfricaApiEngine,
)


class TestAlphaCodeValidation(unittest.TestCase):
    """JSE alpha codes are alphanumeric and are not all three letters."""

    def setUp(self):
        self.engine = JseSouthAfricaApiEngine()

    def test_three_letter_equity_codes_accepted(self):
        self.assertEqual(self.engine.validate_jse_alpha_code("NPN"), "NPN")
        self.assertEqual(self.engine.validate_jse_alpha_code(" agl "), "AGL")

    def test_alphanumeric_code_accepted(self):
        # Regression: S32 (South32) is a Top 40 constituent whose JSE alpha code
        # contains digits. A letters-only rule rejects a real, liquid instrument.
        self.assertEqual(self.engine.validate_jse_alpha_code("S32"), "S32")

    def test_longer_etp_code_accepted(self):
        # Regression: ETP codes such as ETFSWX run past three characters.
        self.assertEqual(self.engine.validate_jse_alpha_code("ETFSWX"), "ETFSWX")

    def test_empty_and_non_alphanumeric_codes_rejected(self):
        for bad in ("", "   ", "NPN-R", "NP N", "NPN.J"):
            with self.subTest(code=bad):
                with self.assertRaises(ValueError):
                    self.engine.validate_jse_alpha_code(bad)

    def test_code_longer_than_configured_bound_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.validate_jse_alpha_code("ETFSWX40")

    def test_length_bound_is_configurable_and_disableable(self):
        self.assertEqual(
            JseSouthAfricaApiEngine(max_alpha_code_length=None).validate_jse_alpha_code(
                "ETFSWX40"
            ),
            "ETFSWX40",
        )

    def test_non_string_code_raises_type_error(self):
        with self.assertRaises(TypeError):
            self.engine.validate_jse_alpha_code(123)


class TestTickSize(unittest.TestCase):
    """The JSE tick size is 1 ZAC for every instrument -- there is no ladder."""

    def setUp(self):
        self.engine = JseSouthAfricaApiEngine()

    def test_tick_is_one_at_every_price_level(self):
        for price in (1.0, 500.0, 9_999.0, 10_000.0, 85_500.0, 1_000_000.0):
            with self.subTest(price=price):
                self.assertEqual(self.engine.get_jse_tick_size_zac(price), 1)

    def test_non_positive_or_non_finite_price_rejected(self):
        for bad in (0.0, -100.0, float("nan"), float("inf")):
            with self.subTest(price=bad):
                with self.assertRaises(ValueError):
                    self.engine.get_jse_tick_size_zac(bad)

    def test_whole_cent_price_accepted_regardless_of_level(self):
        # Regression: 85,502 ZAC is a perfectly legal JSE price. A fabricated
        # "5 ZAC tick above 10,000 ZAC" rule rejects it, blocking a valid order.
        payload = JseOrderPayload(
            "NPN", "BUY", price_zac=85_502.0, quantity=100, reference_price_zac=85_500.0
        )
        report = self.engine.validate_and_route_order(payload)
        self.assertTrue(report.is_price_tick_valid)
        self.assertFalse(report.is_rejected)
        self.assertEqual(report.applicable_tick_size_zac, 1)

    def test_fractional_cent_price_rejected(self):
        payload = JseOrderPayload(
            "NPN", "BUY", price_zac=85_500.5, quantity=100, reference_price_zac=85_500.0
        )
        report = self.engine.validate_and_route_order(payload)
        self.assertEqual(report.status, "INVALID_TICK_SIZE")
        self.assertTrue(report.is_rejected)

    def test_near_integer_price_is_off_tick(self):
        # Integer arithmetic, not a float tolerance: 85,500.0001 ZAC is not a
        # whole cent and must not be waved through.
        payload = JseOrderPayload(
            "NPN", "BUY", price_zac=85_500.0001, quantity=100, reference_price_zac=85_500.0
        )
        self.assertEqual(
            self.engine.validate_and_route_order(payload).status, "INVALID_TICK_SIZE"
        )


class TestCurrencyConversion(unittest.TestCase):
    """Prices are in ZAC; 100 ZAC = ZAR 1."""

    def setUp(self):
        self.engine = JseSouthAfricaApiEngine()

    def test_naspers_order_converts_zac_to_zar(self):
        # 85,500 ZAC = ZAR 855.00; 100 shares = ZAR 85,500.00 notional.
        payload = JseOrderPayload(
            "NPN", "BUY", price_zac=85_500.0, quantity=100, reference_price_zac=85_500.0
        )
        report = self.engine.validate_and_route_order(payload)
        self.assertEqual(report.status, "JSE_ORDER_VALIDATED")
        self.assertFalse(report.is_rejected)
        self.assertEqual(report.equivalent_price_zar, 855.00)
        self.assertEqual(report.notional_value_zar, 85_500.00)

    def test_sub_rand_price_converts(self):
        payload = JseOrderPayload(
            "XYZ", "SELL", price_zac=7.0, quantity=1_000, reference_price_zac=7.0
        )
        report = self.engine.validate_and_route_order(payload)
        self.assertEqual(report.equivalent_price_zar, 0.07)
        self.assertEqual(report.notional_value_zar, 70.00)


class TestPriceBand(unittest.TestCase):
    """ZA01 carries a +/-90% price band; other segments publish none."""

    def setUp(self):
        self.engine = JseSouthAfricaApiEngine()

    def test_za01_band_bounds(self):
        lower, upper = self.engine.get_price_band_zac(10_000.0, "ZA01")
        self.assertAlmostEqual(lower, 1_000.0)
        self.assertAlmostEqual(upper, 19_000.0)

    def test_order_outside_za01_band_rejected(self):
        payload = JseOrderPayload(
            "NPN", "BUY", price_zac=20_000.0, quantity=100, reference_price_zac=10_000.0
        )
        report = self.engine.validate_and_route_order(payload)
        self.assertEqual(report.status, "PRICE_BAND_BREACH")
        self.assertTrue(report.is_rejected)
        self.assertFalse(report.is_price_band_valid)

    def test_order_inside_za01_band_is_not_rejected(self):
        # +50% from the static reference is far outside any circuit breaker but
        # still inside the price band, so the order is accepted, not rejected.
        payload = JseOrderPayload(
            "NPN", "BUY", price_zac=15_000.0, quantity=100, reference_price_zac=10_000.0
        )
        report = self.engine.validate_and_route_order(payload)
        self.assertTrue(report.is_price_band_valid)
        self.assertFalse(report.is_rejected)

    def test_no_band_published_for_other_segments(self):
        self.assertEqual(self.engine.get_price_band_zac(10_000.0, "ZA02"), (None, None))
        payload = JseOrderPayload(
            "ABC",
            "BUY",
            price_zac=100_000.0,
            quantity=100,
            reference_price_zac=10_000.0,
            trading_segment="ZA02",
        )
        report = self.engine.validate_and_route_order(payload)
        self.assertTrue(report.is_price_band_valid)
        self.assertIsNone(report.price_band_lower_zac)
        self.assertFalse(report.is_rejected)


class TestCircuitBreakers(unittest.TestCase):
    """A circuit breaker triggers a volatility auction; it does not reject."""

    def setUp(self):
        self.engine = JseSouthAfricaApiEngine()

    def test_published_tolerances_by_segment_and_session(self):
        self.assertEqual(
            self.engine.get_circuit_breaker_tolerances_pct("ZA01", "CONTINUOUS_TRADING"),
            (10.0, 3.0),
        )
        self.assertEqual(
            self.engine.get_circuit_breaker_tolerances_pct("ZA01", "CLOSING_AUCTION_CALL"),
            (4.0, 2.0),
        )
        self.assertEqual(
            self.engine.get_circuit_breaker_tolerances_pct("ZA03", "INTRADAY_AUCTION_CALL"),
            (50.0, 25.0),
        )

    def test_sessions_without_a_published_breaker_return_none(self):
        self.assertIsNone(
            self.engine.get_circuit_breaker_tolerances_pct("ZA01", "INTRADAY_AUCTION_CALL")
        )
        self.assertIsNone(
            self.engine.get_circuit_breaker_tolerances_pct("ZA04", "CLOSING_AUCTION_CALL")
        )

    def test_nsx_segments_have_no_eqm_tolerance_table(self):
        self.assertIsNone(
            self.engine.get_circuit_breaker_tolerances_pct("ZA11", "CONTINUOUS_TRADING")
        )

    def test_static_breach_flags_risk_without_rejecting(self):
        # +12% against a ZA01 continuous-trading static tolerance of 10%.
        payload = JseOrderPayload(
            "NPN", "BUY", price_zac=11_200.0, quantity=100, reference_price_zac=10_000.0
        )
        report = self.engine.validate_and_route_order(payload)
        self.assertEqual(report.status, "VOLATILITY_AUCTION_RISK")
        self.assertTrue(report.circuit_breaker_would_trigger)
        self.assertFalse(report.is_rejected)

    def test_tolerance_is_breached_at_exactly_the_threshold(self):
        # "equal or greater than that permitted" -- exactly 10% breaches.
        payload = JseOrderPayload(
            "NPN", "BUY", price_zac=11_000.0, quantity=100, reference_price_zac=10_000.0
        )
        self.assertTrue(
            self.engine.validate_and_route_order(payload).circuit_breaker_would_trigger
        )
        payload_inside = JseOrderPayload(
            "NPN", "BUY", price_zac=10_999.0, quantity=100, reference_price_zac=10_000.0
        )
        self.assertFalse(
            self.engine.validate_and_route_order(payload_inside).circuit_breaker_would_trigger
        )

    def test_dynamic_breaker_uses_last_traded_price(self):
        # Only 1% off the static reference, but 4% off the last traded price,
        # against a ZA01 continuous-trading dynamic tolerance of 3%.
        payload = JseOrderPayload(
            "NPN",
            "BUY",
            price_zac=10_100.0,
            quantity=100,
            reference_price_zac=10_000.0,
            dynamic_reference_price_zac=9_712.0,
        )
        report = self.engine.validate_and_route_order(payload)
        self.assertTrue(report.circuit_breaker_would_trigger)
        self.assertEqual(report.status, "VOLATILITY_AUCTION_RISK")

    def test_missing_dynamic_reference_is_reported_not_assumed_passing(self):
        payload = JseOrderPayload(
            "NPN", "BUY", price_zac=10_100.0, quantity=100, reference_price_zac=10_000.0
        )
        report = self.engine.validate_and_route_order(payload)
        self.assertFalse(report.circuit_breaker_would_trigger)
        self.assertTrue(
            any("not evaluated" in note for note in report.circuit_breaker_notes),
            report.circuit_breaker_notes,
        )

    def test_no_breaker_for_session_is_not_reported_as_a_breach(self):
        _, _, would_trigger, notes = self.engine.assess_circuit_breaker(
            50_000.0, 10_000.0, None, "ZA01", "INTRADAY_AUCTION_CALL"
        )
        self.assertFalse(would_trigger)
        self.assertTrue(any("No circuit breaker published" in n for n in notes), notes)


class TestOrderSizeAndInputValidation(unittest.TestCase):
    def setUp(self):
        self.engine = JseSouthAfricaApiEngine()

    def _payload(self, **overrides):
        base = dict(
            alpha_code="NPN",
            side="BUY",
            price_zac=85_500.0,
            quantity=100,
            reference_price_zac=85_500.0,
        )
        base.update(overrides)
        return JseOrderPayload(**base)

    def test_maximum_order_size_enforced(self):
        report = self.engine.validate_and_route_order(
            self._payload(quantity=MAX_ORDER_QUANTITY + 1)
        )
        self.assertEqual(report.status, "ORDER_SIZE_EXCEEDED")
        self.assertTrue(report.is_rejected)
        self.assertFalse(report.is_order_size_valid)

    def test_maximum_order_size_boundary_accepted(self):
        report = self.engine.validate_and_route_order(
            self._payload(quantity=MAX_ORDER_QUANTITY)
        )
        self.assertTrue(report.is_order_size_valid)

    def test_zero_reference_price_raises_instead_of_dividing_by_zero(self):
        # Regression: a missing previous close used to raise ZeroDivisionError
        # from inside the deviation calculation.
        with self.assertRaises(ValueError):
            self.engine.validate_and_route_order(self._payload(reference_price_zac=0.0))

    def test_non_finite_prices_raise(self):
        for bad in (float("nan"), float("inf")):
            with self.subTest(price=bad):
                with self.assertRaises(ValueError):
                    self.engine.validate_and_route_order(self._payload(price_zac=bad))

    def test_invalid_side_raises(self):
        # Regression: an unrecognised side used to be echoed onto an approved
        # report, so a typo could be routed to the exchange.
        with self.assertRaises(ValueError):
            self.engine.validate_and_route_order(self._payload(side="BUUY"))

    def test_side_is_normalised(self):
        self.assertEqual(
            self.engine.validate_and_route_order(self._payload(side=" sell ")).side, "SELL"
        )

    def test_non_positive_quantity_raises(self):
        # Regression: a negative quantity used to be approved with a negative
        # notional value.
        for bad in (0, -100):
            with self.subTest(quantity=bad):
                with self.assertRaises(ValueError):
                    self.engine.validate_and_route_order(self._payload(quantity=bad))

    def test_non_integer_quantity_raises(self):
        with self.assertRaises(TypeError):
            self.engine.validate_and_route_order(self._payload(quantity=100.5))

    def test_unknown_segment_or_session_raises(self):
        with self.assertRaises(ValueError):
            self.engine.validate_and_route_order(self._payload(trading_segment="ZA99"))
        with self.assertRaises(ValueError):
            self.engine.validate_and_route_order(self._payload(trading_session="LUNCH"))

    def test_session_name_is_normalised(self):
        report = self.engine.validate_and_route_order(
            self._payload(trading_session="continuous trading")
        )
        self.assertEqual(report.trading_session, "CONTINUOUS_TRADING")


class TestHouseDeviationLimit(unittest.TestCase):
    """The optional in-house cap is a house control, not a JSE rule."""

    def test_disabled_by_default(self):
        engine = JseSouthAfricaApiEngine()
        self.assertIsNone(engine.house_price_deviation_limit_pct)
        payload = JseOrderPayload(
            "NPN", "BUY", price_zac=15_000.0, quantity=100, reference_price_zac=10_000.0
        )
        self.assertFalse(engine.validate_and_route_order(payload).is_rejected)

    def test_house_cap_rejects_when_configured(self):
        engine = JseSouthAfricaApiEngine(house_price_deviation_limit_pct=15.0)
        payload = JseOrderPayload(
            "NPN", "BUY", price_zac=12_000.0, quantity=100, reference_price_zac=10_000.0
        )
        report = engine.validate_and_route_order(payload)
        self.assertEqual(report.status, "HOUSE_LIMIT_EXCEEDED")
        self.assertTrue(report.is_rejected)

    def test_invalid_house_cap_raises(self):
        for bad in (0.0, -5.0, float("nan")):
            with self.subTest(cap=bad):
                with self.assertRaises(ValueError):
                    JseSouthAfricaApiEngine(house_price_deviation_limit_pct=bad)


if __name__ == "__main__":
    unittest.main()
