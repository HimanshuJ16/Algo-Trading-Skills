"""Unit tests for the PSE pre-trade order validator.

Expected values are derived from PSE primary sources, not from the
implementation:

* the board lot / tick table transcribed from the "Existing" column of PSE
  Consultation Paper CN-2025-0046;
* the +50% / -30% static threshold from PSE Circular CN-2020-0028;
* PSE's published worked example for PLDT (TEL) at a Reference Price of
  PHP 1,642.00 -> ceiling PHP 2,463.00, floor PHP 1,150.00.

Tests whose name ends in ``_regression`` are written to FAIL against the
pre-2.0.0 implementation and PASS against the fix.
"""
import unittest
from decimal import Decimal

from philippine_stock_exchange_api import (
    MARKET_DDS,
    MARKET_PHP,
    PSE_PHP_SCHEDULE,
    Config,
    PSEOrderRequest,
    PSEReport,
    PhilippineStockExchangeEngine,
)


class TestPseTierLookup(unittest.TestCase):
    """Board lot and tick size selection from the Reference Price."""

    def setUp(self):
        self.engine = PhilippineStockExchangeEngine(Config(api_key="test_key"))

    def test_every_published_band_boundary(self):
        """Both the From and the To of every PSE band map to that band.

        The expected pairs are transcribed from the PSE table, not read back
        from the module's own schedule constant.
        """
        expected = [
            # (from, to, tick, lot)
            ("0.0001", "0.0099", "0.0001", 1_000_000),
            ("0.0100", "0.0490", "0.0010", 100_000),
            ("0.0500", "0.2490", "0.0010", 10_000),
            ("0.2500", "0.4950", "0.0050", 10_000),
            ("0.5000", "4.9900", "0.0100", 1_000),
            ("5.0000", "9.9900", "0.0100", 100),
            ("10.0000", "19.9800", "0.0200", 100),
            ("20.0000", "49.9500", "0.0500", 100),
            ("50.0000", "99.9500", "0.0500", 10),
            ("100.0000", "199.9000", "0.1000", 10),
            ("200.0000", "499.8000", "0.2000", 10),
            ("500.0000", "999.5000", "0.5000", 10),
            ("1000.0000", "1999.0000", "1.0000", 5),
            ("2000.0000", "4998.0000", "2.0000", 5),
        ]
        self.assertEqual(len(expected) + 1, len(PSE_PHP_SCHEDULE))
        for band_from, band_to, tick, lot in expected:
            for edge in (band_from, band_to):
                with self.subTest(price=edge):
                    self.assertEqual(
                        self.engine.get_pse_tier(Decimal(edge)),
                        (Decimal(tick), lot),
                    )

    def test_open_ended_top_band(self):
        self.assertEqual(
            self.engine.get_pse_tier(Decimal("5000.0000")), (Decimal("5.0000"), 5)
        )
        self.assertEqual(
            self.engine.get_pse_tier(Decimal("87500.00")), (Decimal("5.0000"), 5)
        )

    def test_minimum_reference_price_accepted(self):
        self.assertEqual(
            self.engine.get_pse_tier(Decimal("0.0001")),
            (Decimal("0.0001"), 1_000_000),
        )

    def test_reference_price_below_minimum_raises(self):
        with self.assertRaises(ValueError):
            self.engine.get_pse_tier(Decimal("0.00005"))

    def test_off_lattice_reference_price_falls_to_lower_band(self):
        """PHP 49.97 sits in the gap between the 49.9500 and 50.0000 bands.

        The lower band is the conservative choice (never a coarser tick, never
        a smaller lot) and the mismatch must be logged rather than swallowed.
        """
        with self.assertLogs("philippine_stock_exchange_api", level="WARNING") as cap:
            tick, lot = self.engine.get_pse_tier(Decimal("49.97"))
        self.assertEqual((tick, lot), (Decimal("0.0500"), 100))
        self.assertIn("off the PSE price lattice", "".join(cap.output))

    def test_unknown_market_raises(self):
        with self.assertRaises(ValueError):
            self.engine.get_pse_tier(Decimal("10.00"), market="SGX")

    def test_non_finite_reference_price_raises(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                self.engine.get_pse_tier(bad)


class TestStaticThresholdBand(unittest.TestCase):
    """The +50% / -30% static threshold, rounded onto the day's tick lattice."""

    def setUp(self):
        self.engine = PhilippineStockExchangeEngine()

    def test_pse_published_tel_example(self):
        """PSE worked example: Reference Price PHP 1,642.00.

        Ceiling 1,642 x 1.50 = 2,463.00; floor 1,642 x 0.70 = 1,149.40, which
        rounds UP to PHP 1,150.00 on the PHP 1.00 tick of the Reference Price's
        band. PHP 2,463.00 is deliberately NOT a multiple of the PHP 2.00 tick
        of the band the ceiling itself falls in -- the Reference Price governs.
        """
        self.assertEqual(
            self.engine.get_pse_tier(Decimal("1642.00")), (Decimal("1.0000"), 5)
        )
        floor, ceiling = self.engine.get_static_threshold_bounds(Decimal("1642.00"))
        self.assertEqual(floor, Decimal("1150"))
        self.assertEqual(ceiling, Decimal("2463"))

    def test_lower_threshold_is_thirty_percent_regression(self):
        """CN-2020-0028: the floor is -30%, not -50%, since 24 March 2020."""
        floor, ceiling = self.engine.get_static_threshold_bounds(Decimal("100.00"))
        self.assertEqual(floor, Decimal("70"))
        self.assertEqual(ceiling, Decimal("150"))

        # PHP 60.00 is -40%: legal under the pre-2020 -50% floor, rejected now.
        report = self.engine.validate_pse_order(
            PSEOrderRequest("BDO", "BUY", Decimal("60.00"), 10,
                            reference_price=Decimal("100.00"))
        )
        self.assertEqual(report.status, "PRICE_BAND_BREACH")
        self.assertFalse(report.is_within_price_band)

    def test_pre_2020_symmetric_band_via_override(self):
        """Replaying a session on or before 23 March 2020 needs the -50% floor."""
        legacy = PhilippineStockExchangeEngine(lower_static_threshold_pct=Decimal("50"))
        floor, ceiling = legacy.get_static_threshold_bounds(Decimal("100.00"))
        self.assertEqual(floor, Decimal("50"))
        self.assertEqual(ceiling, Decimal("150"))

    def test_bounds_are_rounded_onto_the_tick_lattice(self):
        """Reference PHP 10.50 -> tick PHP 0.02.

        Raw ceiling 15.75 is not a multiple of 0.02, so it rounds DOWN to
        15.74; raw floor 7.35 rounds UP to 7.36. Rounding the floor down to
        7.34 would publish a bound representing a fall of more than 30%.
        """
        self.assertEqual(
            self.engine.get_pse_tier(Decimal("10.50")), (Decimal("0.0200"), 100)
        )
        floor, ceiling = self.engine.get_static_threshold_bounds(Decimal("10.50"))
        self.assertEqual(ceiling, Decimal("15.74"))
        self.assertEqual(floor, Decimal("7.36"))
        self.assertEqual(ceiling % Decimal("0.02"), 0)
        self.assertEqual(floor % Decimal("0.02"), 0)

    def test_bounds_are_inclusive(self):
        for price, expected in ((Decimal("150.00"), "ORDER_VALID_COMPLIANT"),
                                (Decimal("70.00"), "ORDER_VALID_COMPLIANT"),
                                (Decimal("150.10"), "PRICE_BAND_BREACH"),
                                (Decimal("69.90"), "PRICE_BAND_BREACH")):
            with self.subTest(price=price):
                report = self.engine.validate_pse_order(
                    PSEOrderRequest("BDO", "BUY", price, 10,
                                    reference_price=Decimal("100.00"))
                )
                self.assertEqual(report.status, expected)

    def test_band_collapses_at_the_minimum_price(self):
        """At PHP 0.0001 the band is a single price, and that is correct.

        0.0001 x 1.50 = 0.00015, which floors to 0.0001 on the PHP 0.0001 tick,
        and there is no placeable price below the minimum. A sub-centavo issue
        pinned at the floor genuinely cannot move -- the engine must not invent
        headroom the exchange does not offer.
        """
        floor, ceiling = self.engine.get_static_threshold_bounds(Decimal("0.0001"))
        self.assertEqual(floor, Decimal("0.0001"))
        self.assertEqual(ceiling, Decimal("0.0001"))

    def test_band_never_exceeds_the_regulatory_percentages(self):
        """Inward rounding must never widen the band past +50% / -30%."""
        for reference in ("0.0055", "0.3000", "1.2340", "10.50", "49.95",
                          "100.00", "1642.00", "4998.00", "12345.00"):
            with self.subTest(reference=reference):
                ref = Decimal(reference)
                floor, ceiling = self.engine.get_static_threshold_bounds(ref)
                self.assertLessEqual((ref - floor) / ref * 100, Decimal("30"))
                self.assertLessEqual((ceiling - ref) / ref * 100, Decimal("50"))
                self.assertLessEqual(floor, ref)
                self.assertLessEqual(ref, ceiling)

    def test_float_ceiling_precision_regression(self):
        """Reference PHP 0.30 -> ceiling exactly PHP 0.45.

        In binary floating point ``0.30 * 1.50`` is 0.4499999999999999, so an
        order at the ceiling was rejected as a band breach.
        """
        floor, ceiling = self.engine.get_static_threshold_bounds(Decimal("0.30"))
        self.assertEqual(ceiling, Decimal("0.45"))
        self.assertEqual(floor, Decimal("0.21"))
        report = self.engine.validate_pse_order(
            PSEOrderRequest("PENNY", "BUY", Decimal("0.45"), 10_000,
                            reference_price=Decimal("0.30"))
        )
        self.assertEqual(report.status, "ORDER_VALID_COMPLIANT")


class TestOrderValidation(unittest.TestCase):
    """Board lot, tick alignment and status precedence."""

    def setUp(self):
        self.engine = PhilippineStockExchangeEngine()

    def test_valid_order_high_tier(self):
        # SM Investments, Reference Price PHP 890.00 -> band 500.0000-999.5000
        # => tick PHP 0.50, lot 10. Band: 623.00 - 1,335.00.
        report = self.engine.validate_pse_order(
            PSEOrderRequest("SM", "BUY", Decimal("900.00"), 100,
                            reference_price=Decimal("890.00"))
        )
        self.assertIsInstance(report, PSEReport)
        self.assertEqual(report.status, "ORDER_VALID_COMPLIANT")
        self.assertEqual(report.required_board_lot, 10)
        self.assertEqual(report.required_tick_size, Decimal("0.5000"))
        self.assertEqual(report.price_floor, Decimal("623"))
        self.assertEqual(report.price_ceiling, Decimal("1335"))
        self.assertEqual(report.currency, "PHP")
        self.assertTrue(report.is_valid_board_lot)
        self.assertTrue(report.is_valid_tick_size)
        self.assertTrue(report.is_within_price_band)

    def test_board_lot_follows_reference_price_not_order_price_regression(self):
        """Reference PHP 4.90 fixes lot 1,000 for the WHOLE day.

        An order priced at PHP 5.05 does not move the security into the
        100-share band: Article IV Section 8 keys the lot off the Reference
        Price. The pre-2.0.0 engine looked the tier up from the order price and
        accepted 100 shares here.
        """
        rejected = self.engine.validate_pse_order(
            PSEOrderRequest("MEG", "BUY", Decimal("5.05"), 100,
                            reference_price=Decimal("4.90"))
        )
        self.assertEqual(rejected.required_board_lot, 1_000)
        self.assertEqual(rejected.status, "INVALID_BOARD_LOT")

        accepted = self.engine.validate_pse_order(
            PSEOrderRequest("MEG", "BUY", Decimal("5.05"), 1_000,
                            reference_price=Decimal("4.90"))
        )
        self.assertEqual(accepted.status, "ORDER_VALID_COMPLIANT")

    def test_tick_size_follows_reference_price_regression(self):
        """Reference PHP 1,642.00 fixes the PHP 1.00 tick for the whole day.

        PHP 2,005.00 is on that lattice and inside the band, so it is valid --
        even though PHP 2,005.00 sits in the band whose own tick is PHP 2.00.
        The pre-2.0.0 engine rejected it as INVALID_TICK_SIZE.
        """
        report = self.engine.validate_pse_order(
            PSEOrderRequest("TEL", "BUY", Decimal("2005.00"), 5,
                            reference_price=Decimal("1642.00"))
        )
        self.assertEqual(report.required_tick_size, Decimal("1.0000"))
        self.assertEqual(report.status, "ORDER_VALID_COMPLIANT")

    def test_sub_tick_price_rejected_regression(self):
        """PHP 1,000.00005 is off the PHP 1.00 tick.

        The pre-2.0.0 scale-and-round test computed
        ``round(1000.00005 * 10000) % round(1.0 * 10000)``, whose left operand
        rounds to 10,000,000 -- the sub-tick remainder vanishes and the order
        was reported ORDER_VALID_COMPLIANT.
        """
        report = self.engine.validate_pse_order(
            PSEOrderRequest("TEL", "BUY", Decimal("1000.00005"), 5,
                            reference_price=Decimal("1000.00"))
        )
        self.assertEqual(report.status, "INVALID_TICK_SIZE")
        self.assertFalse(report.is_valid_tick_size)
        self.assertTrue(report.is_within_price_band)

    def test_invalid_board_lot_rejection(self):
        # Ayala Corp, Reference Price PHP 700.00 -> lot 10. 15 is not a multiple.
        report = self.engine.validate_pse_order(
            PSEOrderRequest("AC", "BUY", Decimal("700.00"), 15,
                            reference_price=Decimal("700.00"))
        )
        self.assertEqual(report.status, "INVALID_BOARD_LOT")
        self.assertFalse(report.is_valid_board_lot)

    def test_price_ceiling_breach(self):
        # Reference PHP 100.00 -> ceiling PHP 150.00. Order at PHP 160.00.
        report = self.engine.validate_pse_order(
            PSEOrderRequest("BDO", "BUY", Decimal("160.00"), 100,
                            reference_price=Decimal("100.00"))
        )
        self.assertEqual(report.status, "PRICE_BAND_BREACH")
        self.assertFalse(report.is_within_price_band)

    def test_report_carries_repricing_information(self):
        """A rejection must let the caller reprice without re-deriving the band."""
        report = self.engine.validate_pse_order(
            PSEOrderRequest("BDO", "SELL", Decimal("160.00"), 100,
                            reference_price=Decimal("100.00"))
        )
        self.assertEqual(report.reference_price, Decimal("100"))
        self.assertEqual(report.price_ceiling, Decimal("150"))
        self.assertEqual(report.price_floor, Decimal("70"))
        self.assertEqual(report.side, "SELL")
        self.assertIn("PRICE_BAND_BREACH", report.audit_notes)

    def test_side_is_normalised(self):
        report = self.engine.validate_pse_order(
            PSEOrderRequest("SM", " sell ", Decimal("900.00"), 10,
                            reference_price=Decimal("890.00"))
        )
        self.assertEqual(report.side, "SELL")


class TestDynamicThreshold(unittest.TestCase):
    """The per-security band measured against the Last Traded Price."""

    def setUp(self):
        self.engine = PhilippineStockExchangeEngine()

    def test_not_checked_unless_requested(self):
        report = self.engine.validate_pse_order(
            PSEOrderRequest("BDO", "BUY", Decimal("120.00"), 10,
                            reference_price=Decimal("100.00"))
        )
        self.assertIsNone(report.is_within_dynamic_band)
        self.assertIsNone(report.dynamic_floor)
        self.assertIsNone(report.dynamic_ceiling)
        self.assertIn("NOT CHECKED", report.audit_notes)
        self.assertEqual(report.status, "ORDER_VALID_COMPLIANT")

    def test_breach_inside_the_static_band(self):
        """LTP PHP 100.00 at the cluster-C 10% threshold -> PHP 90.00-110.00.

        PHP 120.00 is comfortably inside the static band (70.00-150.00) and
        still rejected. A static-only validator passes this order.
        """
        report = self.engine.validate_pse_order(
            PSEOrderRequest("BDO", "BUY", Decimal("120.00"), 10,
                            reference_price=Decimal("100.00"),
                            last_traded_price=Decimal("100.00"),
                            dynamic_threshold_pct=Decimal("10"))
        )
        self.assertTrue(report.is_within_price_band)
        self.assertFalse(report.is_within_dynamic_band)
        self.assertEqual(report.status, "DYNAMIC_THRESHOLD_BREACH")
        self.assertEqual(report.dynamic_floor, Decimal("90"))
        self.assertEqual(report.dynamic_ceiling, Decimal("110"))

    def test_within_dynamic_band_is_accepted(self):
        report = self.engine.validate_pse_order(
            PSEOrderRequest("BDO", "BUY", Decimal("110.00"), 10,
                            reference_price=Decimal("100.00"),
                            last_traded_price=Decimal("100.00"),
                            dynamic_threshold_pct=Decimal("10"))
        )
        self.assertEqual(report.status, "ORDER_VALID_COMPLIANT")
        self.assertTrue(report.is_within_dynamic_band)

    def test_half_supplied_inputs_raise(self):
        base = dict(reference_price=Decimal("100.00"))
        with self.assertRaises(ValueError):
            self.engine.validate_pse_order(
                PSEOrderRequest("BDO", "BUY", Decimal("110.00"), 10,
                                last_traded_price=Decimal("100.00"), **base)
            )
        with self.assertRaises(ValueError):
            self.engine.validate_pse_order(
                PSEOrderRequest("BDO", "BUY", Decimal("110.00"), 10,
                                dynamic_threshold_pct=Decimal("10"), **base)
            )

    def test_out_of_range_percentage_raises(self):
        for pct in (Decimal("0"), Decimal("100"), Decimal("-5")):
            with self.subTest(pct=pct), self.assertRaises(ValueError):
                self.engine.get_dynamic_threshold_bounds(
                    Decimal("100.00"), pct, Decimal("100.00")
                )


class TestDollarDenominatedSecurities(unittest.TestCase):
    """The DDS schedule is a different table, not a currency relabelling."""

    def setUp(self):
        self.engine = PhilippineStockExchangeEngine()

    def test_lot_differs_from_the_peso_schedule(self):
        self.assertEqual(
            self.engine.get_pse_tier(Decimal("1.50"), MARKET_DDS),
            (Decimal("0.01"), 20),
        )
        self.assertEqual(
            self.engine.get_pse_tier(Decimal("1.50"), MARKET_PHP),
            (Decimal("0.0100"), 1_000),
        )

    def test_dds_order_reports_usd(self):
        report = self.engine.validate_pse_order(
            PSEOrderRequest("DDS1", "BUY", Decimal("1.50"), 20,
                            reference_price=Decimal("1.50"), market=MARKET_DDS)
        )
        self.assertEqual(report.status, "ORDER_VALID_COMPLIANT")
        self.assertEqual(report.currency, "USD")
        self.assertEqual(report.market, MARKET_DDS)
        self.assertIn("USD", report.audit_notes)

    def test_peso_lot_applied_to_a_dds_order_would_reject_regression(self):
        """20 shares is a full DDS lot at USD 1.50 and an odd lot on the peso table."""
        as_dds = self.engine.validate_pse_order(
            PSEOrderRequest("DDS1", "BUY", Decimal("1.50"), 20,
                            reference_price=Decimal("1.50"), market=MARKET_DDS)
        )
        as_php = self.engine.validate_pse_order(
            PSEOrderRequest("DDS1", "BUY", Decimal("1.50"), 20,
                            reference_price=Decimal("1.50"), market=MARKET_PHP)
        )
        self.assertEqual(as_dds.status, "ORDER_VALID_COMPLIANT")
        self.assertEqual(as_php.status, "INVALID_BOARD_LOT")


class TestInputGuards(unittest.TestCase):
    """Malformed input is raised, never returned as an exchange-rule status."""

    def setUp(self):
        self.engine = PhilippineStockExchangeEngine()

    def _order(self, **overrides):
        payload = dict(symbol="SM", side="BUY", price=Decimal("900.00"),
                       quantity=10, reference_price=Decimal("890.00"))
        payload.update(overrides)
        return PSEOrderRequest(**payload)

    def test_invalid_side_raises(self):
        for side in ("BYU", "", "LONG", None):
            with self.subTest(side=side), self.assertRaises(ValueError):
                self.engine.validate_pse_order(self._order(side=side))

    def test_boolean_quantity_raises(self):
        """``True`` is an int in Python and would pass as a 1-share order."""
        with self.assertRaises(ValueError):
            self.engine.validate_pse_order(self._order(quantity=True))

    def test_non_integer_or_non_positive_quantity_raises(self):
        for quantity in (0, -10, 10.0, "10"):
            with self.subTest(quantity=quantity), self.assertRaises(ValueError):
                self.engine.validate_pse_order(self._order(quantity=quantity))

    def test_non_positive_price_raises(self):
        for price in (Decimal("0"), Decimal("-900.00")):
            with self.subTest(price=price), self.assertRaises(ValueError):
                self.engine.validate_pse_order(self._order(price=price))

    def test_non_finite_price_raises(self):
        for price in (float("nan"), float("inf")):
            with self.subTest(price=price), self.assertRaises(ValueError):
                self.engine.validate_pse_order(self._order(price=price))

    def test_non_positive_reference_price_raises(self):
        for reference in (Decimal("0"), Decimal("-1")):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                self.engine.validate_pse_order(self._order(reference_price=reference))

    def test_non_finite_reference_price_raises_regression(self):
        """A NaN Reference Price is a data fault, not a rule breach.

        Every ``<=`` against NaN is False, so the pre-2.0.0 engine returned
        PRICE_BAND_BREACH -- indistinguishable from an order the exchange would
        genuinely reject.
        """
        for reference in (float("nan"), float("inf")):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                self.engine.validate_pse_order(self._order(reference_price=reference))

    def test_unknown_market_raises(self):
        with self.assertRaises(ValueError):
            self.engine.validate_pse_order(self._order(market="SGX"))

    def test_implausibly_large_price_raises_value_error(self):
        """A mis-scaled feed must not escape as a raw ``DecimalException``.

        Decimal floor division raises ``DivisionImpossible`` once the quotient
        exceeds the context precision. That would propagate uncaught through the
        routing path instead of being reported as the input fault it is.
        """
        huge = Decimal("123456789012345678901234567890")
        with self.assertRaises(ValueError):
            self.engine.get_static_threshold_bounds(huge)
        with self.assertRaises(ValueError):
            self.engine.validate_pse_order(self._order(price=huge, reference_price=huge))

    def test_constructor_rejects_impossible_thresholds(self):
        for kwargs in ({"upper_static_threshold_pct": Decimal("0")},
                       {"lower_static_threshold_pct": Decimal("100")},
                       {"lower_static_threshold_pct": Decimal("0")}):
            with self.subTest(**kwargs), self.assertRaises(ValueError):
                PhilippineStockExchangeEngine(**kwargs)


if __name__ == "__main__":
    unittest.main()
