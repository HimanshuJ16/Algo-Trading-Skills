"""Unit tests for the JPX / TSE arrowhead4.0 pre-trade order validator.

Expected values are taken from the JPX published tables (tick size page updated
6 August 2026; daily price limits page updated 24 August 2026), not re-derived
from the module's own schedules.
"""
import logging
import unittest
from decimal import Decimal

from japan_exchange_group_jpx_api_integration import (
    JpxOrderPayload,
    JpxStockExchangeApiEngine,
    TICK_TABLE_ETF_SINGLE_UNIT,
    TICK_TABLE_OTHER,
    TICK_TABLE_TOPIX500,
)


MODULE_LOGGER = "japan_exchange_group_jpx_api_integration"


def setUpModule():
    """Keep expected rejection warnings out of the test output.

    assertLogs installs its own handler and level, so the tests that assert on
    logging still work.
    """
    logging.getLogger(MODULE_LOGGER).setLevel(logging.CRITICAL)


class TestTseLocalCodeValidation(unittest.TestCase):

    def setUp(self):
        self.engine = JpxStockExchangeApiEngine()

    def test_legacy_numeric_codes_accepted(self):
        for code in ("7203", "6758", "9984", "1300", "9999"):
            self.assertEqual(self.engine.validate_tse_local_code(code), code)

    def test_alphanumeric_codes_assigned_from_january_2024_accepted(self):
        # SICC assigns letters in the 2nd and/or 4th position from 1 Jan 2024.
        # '130A' was the first such code. Rejecting these blocks every issue
        # listed since that date -- the pre-fix behaviour of this validator.
        self.assertEqual(self.engine.validate_tse_local_code("130A"), "130A")
        self.assertEqual(self.engine.validate_tse_local_code("9A76"), "9A76")
        self.assertEqual(self.engine.validate_tse_local_code("9A7A"), "9A7A")
        self.assertEqual(self.engine.validate_tse_local_code(" 285a "), "285A")

    def test_excluded_letters_rejected(self):
        # SICC uses 19 uppercase letters; B, E, I, O, Q, V and Z are excluded.
        for code in ("130B", "130E", "130I", "130O", "130Q", "130V", "130Z"):
            with self.assertRaises(ValueError):
                self.engine.validate_tse_local_code(code)

    def test_letters_only_allowed_in_second_and_fourth_positions(self):
        for code in ("A130", "13A0"):
            with self.assertRaises(ValueError):
                self.engine.validate_tse_local_code(code)

    def test_malformed_codes_rejected(self):
        for code in ("720", "72031", "JP3633400001", "", "72-3"):
            with self.assertRaises(ValueError):
                self.engine.validate_tse_local_code(code)
        with self.assertRaises(ValueError):
            self.engine.validate_tse_local_code(7203)  # type: ignore[arg-type]


class TestTseTickSize(unittest.TestCase):

    def setUp(self):
        self.engine = JpxStockExchangeApiEngine()

    def test_topix500_table(self):
        # JPX TOPIX500 table: <=1,000 -> 0.1; <=3,000 -> 0.5; <=10,000 -> 1;
        # <=30,000 -> 5; <=100,000 -> 10; <=300,000 -> 50; <=1,000,000 -> 100.
        cases = [
            (500, "0.1"),
            (1_000, "0.1"),
            (2_500, "0.5"),
            (3_000, "0.5"),
            (9_999, "1"),
            (30_000, "5"),
            (100_000, "10"),
            (300_000, "50"),
            (1_000_000, "100"),
            (50_000_000, "10000"),
        ]
        for price, expected in cases:
            with self.subTest(price=price):
                self.assertEqual(
                    self.engine.get_tse_tick_size(price, TICK_TABLE_TOPIX500),
                    Decimal(expected),
                )

    def test_other_issues_table(self):
        # JPX "Other Issues" table: <=3,000 -> 1; <=5,000 -> 5; <=30,000 -> 10;
        # <=50,000 -> 50; <=300,000 -> 100; over 50,000,000 -> 100,000.
        cases = [
            (2_500, "1"),
            (3_000, "1"),
            (4_000, "5"),
            (5_000, "5"),
            (8_000, "10"),
            (30_000, "10"),
            (50_000, "50"),
            (300_000, "100"),
            (60_000_000, "100000"),
        ]
        for price, expected in cases:
            with self.subTest(price=price):
                self.assertEqual(
                    self.engine.get_tse_tick_size(price, TICK_TABLE_OTHER),
                    Decimal(expected),
                )

    def test_regression_high_price_ticks_are_not_a_flat_50_yen(self):
        # The pre-fix schedule returned JPY 50 for every price at or above
        # JPY 10,000. TSE's published tables do not: an ordinary issue at
        # JPY 15,000 ticks at JPY 10, and a TOPIX500 issue at JPY 15,000 ticks
        # at JPY 5.
        self.assertEqual(self.engine.get_tse_tick_size(15_000, TICK_TABLE_OTHER), Decimal("10"))
        self.assertEqual(
            self.engine.get_tse_tick_size(15_000, TICK_TABLE_TOPIX500), Decimal("5")
        )

    def test_regression_band_bounds_are_inclusive(self):
        # TSE publishes bands as "5,000 yen or less" (5,000円以下). A price of
        # exactly JPY 5,000 therefore takes the finer JPY 5 tick, not JPY 10.
        self.assertEqual(self.engine.get_tse_tick_size(5_000, TICK_TABLE_OTHER), Decimal("5"))
        self.assertEqual(self.engine.get_tse_tick_size(5_000.5, TICK_TABLE_OTHER), Decimal("10"))
        self.assertEqual(
            self.engine.get_tse_tick_size(10_000, TICK_TABLE_TOPIX500), Decimal("1")
        )
        self.assertEqual(
            self.engine.get_tse_tick_size(10_000.5, TICK_TABLE_TOPIX500), Decimal("5")
        )

    def test_etf_single_unit_table(self):
        # Single-unit ETFs/ETNs tick at JPY 1 up to JPY 10,000 even though an
        # ordinary issue at JPY 8,000 ticks at JPY 10.
        self.assertEqual(
            self.engine.get_tse_tick_size(8_000, TICK_TABLE_ETF_SINGLE_UNIT), Decimal("1")
        )
        self.assertEqual(self.engine.get_tse_tick_size(8_000, TICK_TABLE_OTHER), Decimal("10"))

    def test_invalid_tick_inputs_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.get_tse_tick_size(0)
        with self.assertRaises(ValueError):
            self.engine.get_tse_tick_size(-100)
        with self.assertRaises(ValueError):
            self.engine.get_tse_tick_size(float("nan"))
        with self.assertRaises(ValueError):
            self.engine.get_tse_tick_size(float("inf"))
        with self.assertRaises(ValueError):
            self.engine.get_tse_tick_size(2_500, "TOPIX100")

    def test_sub_yen_prices_are_valid_for_topix500_issues(self):
        # A TOPIX500 constituent at or below JPY 1,000 ticks at JPY 0.1, so
        # JPY 0.5 is not a floating-point artefact -- it is a real TSE price.
        self.assertEqual(self.engine.get_tse_tick_size(0.5, TICK_TABLE_TOPIX500), Decimal("0.1"))


class TestDailyPriceLimits(unittest.TestCase):

    def setUp(self):
        self.engine = JpxStockExchangeApiEngine()

    def test_absolute_yen_schedule(self):
        # JPX daily price limits table, transcribed independently.
        cases = [
            (50, "30"),
            (99, "30"),
            (100, "50"),
            (150, "50"),
            (450, "80"),
            (699, "100"),
            (999, "150"),
            (1_400, "300"),
            (2_500, "500"),
            (4_999, "700"),
            (9_000, "1500"),
            (120_000, "30000"),
            (60_000_000, "10000000"),
        ]
        for base_price, expected in cases:
            with self.subTest(base_price=base_price):
                self.assertEqual(
                    self.engine.get_daily_price_limit(base_price), Decimal(expected)
                )

    def test_regression_limits_are_not_twenty_percent(self):
        # The pre-fix engine used a flat +/-20% band. TSE's absolute-yen table
        # is materially different at almost every price level:
        #   base JPY 150     -> +/-JPY 50     (33.3%, not 20%)
        #   base JPY 9,000   -> +/-JPY 1,500  (16.7%, not 20%)
        #   base JPY 120,000 -> +/-JPY 30,000 (25.0%, not 20%)
        self.assertEqual(self.engine.get_daily_price_limit(150), Decimal("50"))
        self.assertEqual(self.engine.get_daily_price_limit(9_000), Decimal("1500"))
        self.assertEqual(self.engine.get_daily_price_limit(120_000), Decimal("30000"))

    def test_band_bounds_are_exclusive_on_the_upper_side(self):
        # TSE publishes the base-price bands as "less than 100 yen" (100円未満),
        # so JPY 100 falls into the NEXT band -- the opposite convention to the
        # tick size table.
        self.assertEqual(self.engine.get_daily_price_limit(99.9), Decimal("30"))
        self.assertEqual(self.engine.get_daily_price_limit(100), Decimal("50"))

    def test_bounds_are_symmetric_and_inclusive(self):
        limit, lower, upper = self.engine.get_daily_price_limit_bounds(2_500)
        self.assertEqual(limit, Decimal("500"))
        self.assertEqual(lower, Decimal("2000"))
        self.assertEqual(upper, Decimal("3000"))

    def test_broadened_limit_override(self):
        limit, lower, upper = self.engine.get_daily_price_limit_bounds(
            1_042, limit_override_jpy=1_200
        )
        self.assertEqual(limit, Decimal("1200"))
        self.assertEqual(lower, Decimal("-158"))
        self.assertEqual(upper, Decimal("2242"))

    def test_invalid_base_price_rejected(self):
        for bad in (0, -1, float("nan"), float("inf")):
            with self.subTest(base_price=bad):
                with self.assertRaises(ValueError):
                    self.engine.get_daily_price_limit(bad)
        with self.assertRaises(ValueError):
            self.engine.get_daily_price_limit_bounds(1_000, limit_override_jpy=0)


class TestOrderRouting(unittest.TestCase):

    def setUp(self):
        self.engine = JpxStockExchangeApiEngine()

    def test_valid_topix500_order(self):
        # Toyota Motor (7203) is a TOPIX100 -- hence TOPIX500 -- constituent,
        # so at JPY 2,500 its tick is JPY 0.5, not JPY 1. Base price JPY 2,500
        # gives a +/-JPY 500 band (JPY 2,000 - JPY 3,000).
        payload = JpxOrderPayload(
            "7203", "BUY", price_jpy=2_500.5, quantity=500,
            reference_price_jpy=2_500.0, tick_table=TICK_TABLE_TOPIX500,
        )
        report = self.engine.validate_and_route_order(payload)

        self.assertEqual(report.status, "JPX_ORDER_VALIDATED")
        self.assertEqual(report.local_code, "7203")
        self.assertEqual(report.applicable_tick_size_jpy, Decimal("0.5"))
        self.assertEqual(report.board_lots_count, 5)
        self.assertEqual(report.daily_price_limit_jpy, Decimal("500"))
        self.assertEqual(report.lower_limit_price_jpy, Decimal("2000"))
        self.assertEqual(report.upper_limit_price_jpy, Decimal("3000"))
        self.assertTrue(report.is_price_tick_valid)
        self.assertTrue(report.is_board_lot_valid)
        self.assertTrue(report.is_price_limit_valid)

    def test_regression_half_yen_price_rejected_on_other_issues_table(self):
        # The same JPY 2,500.5 price is invalid for a non-TOPIX500 issue, whose
        # tick at that level is JPY 1. Mis-tagging the tick table is the single
        # most likely way to send arrowhead a price it will reject.
        payload = JpxOrderPayload(
            "1301", "BUY", price_jpy=2_500.5, quantity=500,
            reference_price_jpy=2_500.0, tick_table=TICK_TABLE_OTHER,
        )
        report = self.engine.validate_and_route_order(payload)
        self.assertEqual(report.status, "INVALID_TICK_SIZE")
        self.assertFalse(report.is_price_tick_valid)

    def test_decimal_tick_alignment_is_exact(self):
        # 0.1 has no exact binary representation; a float-tolerance check can
        # accept prices arrowhead rejects. JPY 999.3 is a whole number of
        # JPY 0.1 ticks and must pass; JPY 999.25 is not and must fail.
        base = dict(quantity=100, reference_price_jpy=1_000.0, tick_table=TICK_TABLE_TOPIX500)
        ok = self.engine.validate_and_route_order(
            JpxOrderPayload("7203", "BUY", price_jpy=999.3, **base)
        )
        self.assertTrue(ok.is_price_tick_valid)
        bad = self.engine.validate_and_route_order(
            JpxOrderPayload("7203", "BUY", price_jpy=999.25, **base)
        )
        self.assertFalse(bad.is_price_tick_valid)

    def test_odd_lot_rejected(self):
        payload = JpxOrderPayload(
            "6758", "BUY", price_jpy=12_000.0, quantity=150,
            reference_price_jpy=12_000.0, tick_table=TICK_TABLE_TOPIX500,
        )
        report = self.engine.validate_and_route_order(payload)
        self.assertEqual(report.status, "INVALID_BOARD_LOT")
        self.assertFalse(report.is_board_lot_valid)

    def test_single_unit_etf_accepts_one_share(self):
        # An ETF with a trading unit of 1 must not be rejected as an odd lot.
        payload = JpxOrderPayload(
            "1306", "BUY", price_jpy=3_000.0, quantity=1,
            reference_price_jpy=3_000.0, tick_table=TICK_TABLE_ETF_SINGLE_UNIT,
            trading_unit=1,
        )
        report = self.engine.validate_and_route_order(payload)
        self.assertEqual(report.status, "JPX_ORDER_VALIDATED")
        self.assertEqual(report.board_lots_count, 1)

    def test_price_limit_breach_rejected(self):
        # Base price JPY 2,500 -> band JPY 2,000 - JPY 3,000. JPY 3,005 is out.
        payload = JpxOrderPayload(
            "1301", "BUY", price_jpy=3_005.0, quantity=100,
            reference_price_jpy=2_500.0, tick_table=TICK_TABLE_OTHER,
        )
        report = self.engine.validate_and_route_order(payload)
        self.assertEqual(report.status, "PRICE_LIMIT_EXCEEDED")
        self.assertFalse(report.is_price_limit_valid)

    def test_price_exactly_at_the_limit_is_accepted(self):
        # The "stop high" price is tradeable, so the band bound is inclusive.
        payload = JpxOrderPayload(
            "1301", "BUY", price_jpy=3_000.0, quantity=100,
            reference_price_jpy=2_500.0, tick_table=TICK_TABLE_OTHER,
        )
        report = self.engine.validate_and_route_order(payload)
        self.assertEqual(report.status, "JPX_ORDER_VALIDATED")
        self.assertTrue(report.is_price_limit_valid)

    def test_regression_order_within_absolute_band_but_beyond_twenty_percent(self):
        # Base price JPY 150 -> TSE band is +/-JPY 50 (JPY 100 - JPY 200).
        # A JPY 195 order is legitimate; a flat +/-20% rule (JPY 120 - JPY 180)
        # would have falsely rejected it.
        payload = JpxOrderPayload(
            "1301", "BUY", price_jpy=195.0, quantity=100,
            reference_price_jpy=150.0, tick_table=TICK_TABLE_OTHER,
        )
        report = self.engine.validate_and_route_order(payload)
        self.assertEqual(report.status, "JPX_ORDER_VALIDATED")

    def test_regression_order_inside_twenty_percent_but_beyond_absolute_band(self):
        # Base price JPY 9,000 -> TSE band is +/-JPY 1,500 (JPY 7,500 - 10,500).
        # A JPY 10,700 order is +18.9%: a flat +/-20% rule would have waved it
        # through for arrowhead to reject.
        payload = JpxOrderPayload(
            "1301", "BUY", price_jpy=10_700.0, quantity=100,
            reference_price_jpy=9_000.0, tick_table=TICK_TABLE_OTHER,
        )
        report = self.engine.validate_and_route_order(payload)
        self.assertEqual(report.status, "PRICE_LIMIT_EXCEEDED")

    def test_broadened_limit_accepts_an_otherwise_out_of_band_price(self):
        payload = JpxOrderPayload(
            "1301", "BUY", price_jpy=2_000.0, quantity=100,
            reference_price_jpy=1_042.0, tick_table=TICK_TABLE_OTHER,
            daily_price_limit_override_jpy=1_200.0,
        )
        report = self.engine.validate_and_route_order(payload)
        self.assertEqual(report.status, "JPX_ORDER_VALIDATED")
        self.assertEqual(report.upper_limit_price_jpy, Decimal("2242"))

    def test_alphanumeric_code_routes_end_to_end(self):
        payload = JpxOrderPayload(
            "130A", "SELL", price_jpy=1_200.0, quantity=200,
            reference_price_jpy=1_200.0, tick_table=TICK_TABLE_OTHER,
        )
        report = self.engine.validate_and_route_order(payload)
        self.assertEqual(report.status, "JPX_ORDER_VALIDATED")
        self.assertEqual(report.local_code, "130A")

    def test_malformed_input_raises_rather_than_returning_a_status(self):
        good = dict(price_jpy=2_500.0, quantity=100, reference_price_jpy=2_500.0)
        with self.assertRaises(ValueError):  # unknown side
            self.engine.validate_and_route_order(JpxOrderPayload("7203", "HOLD", **good))
        with self.assertRaises(ValueError):  # zero base price -> would divide by zero
            self.engine.validate_and_route_order(
                JpxOrderPayload("7203", "BUY", price_jpy=2_500.0, quantity=100,
                                reference_price_jpy=0.0)
            )
        with self.assertRaises(ValueError):  # NaN base price -> would silently compare False
            self.engine.validate_and_route_order(
                JpxOrderPayload("7203", "BUY", price_jpy=2_500.0, quantity=100,
                                reference_price_jpy=float("nan"))
            )
        with self.assertRaises(ValueError):  # non-integer quantity
            self.engine.validate_and_route_order(
                JpxOrderPayload("7203", "BUY", price_jpy=2_500.0, quantity=100.0,  # type: ignore[arg-type]
                                reference_price_jpy=2_500.0)
            )
        with self.assertRaises(ValueError):  # non-positive trading unit
            self.engine.validate_and_route_order(
                JpxOrderPayload("7203", "BUY", trading_unit=0, **good)
            )

    def test_negative_quantity_is_rejected_as_an_invalid_lot(self):
        payload = JpxOrderPayload(
            "7203", "BUY", price_jpy=2_500.0, quantity=-100, reference_price_jpy=2_500.0
        )
        report = self.engine.validate_and_route_order(payload)
        self.assertEqual(report.status, "INVALID_BOARD_LOT")
        self.assertEqual(report.board_lots_count, 0)

    def test_rejections_are_logged_for_the_audit_trail(self):
        payload = JpxOrderPayload(
            "1301", "BUY", price_jpy=10_700.0, quantity=100,
            reference_price_jpy=9_000.0, tick_table=TICK_TABLE_OTHER,
        )
        with self.assertLogs(MODULE_LOGGER, level=logging.WARNING) as logs:
            report = self.engine.validate_and_route_order(payload)
        self.assertEqual(report.status, "PRICE_LIMIT_EXCEEDED")
        self.assertIn("daily price limit band", logs.output[0])

    def test_side_is_normalised(self):
        payload = JpxOrderPayload(
            "7203", " sell ", price_jpy=2_500.0, quantity=100, reference_price_jpy=2_500.0
        )
        self.assertEqual(self.engine.validate_and_route_order(payload).side, "SELL")


if __name__ == '__main__':
    unittest.main()
