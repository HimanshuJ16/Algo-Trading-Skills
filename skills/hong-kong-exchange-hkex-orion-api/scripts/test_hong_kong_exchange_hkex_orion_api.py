"""Unit tests for the HKEX securities-market pre-trade order validator.

Expected tick sizes are transcribed independently from the SEHK Rules of the Exchange,
Second Schedule (Spread Table) - not derived from the implementation's own band table.
"""
import unittest
from decimal import Decimal

from hong_kong_exchange_hkex_orion_api import (
    MAX_ORDER_SIZE_BOARD_LOTS,
    CounterType,
    HkexOrderError,
    HkexOrderPayload,
    HkexOrionApiEngine,
    InvalidStockCodeError,
    LotClassification,
    PriceOutOfRangeError,
    SpreadTable,
    SpreadTableUnavailableError,
)

D = Decimal


def _order(**overrides):
    """A Part A order that validates cleanly, so each test varies exactly one thing."""
    kwargs = dict(
        raw_stock_code="700",
        side="BUY",
        order_type="LIMIT",
        price="300.20",
        quantity=200,
        board_lot_size=100,
        currency="HKD",
        spread_table=SpreadTable.PART_A,
    )
    kwargs.update(overrides)
    return HkexOrderPayload(**kwargs)


class TestStockCodeFormatting(unittest.TestCase):

    def setUp(self):
        self.engine = HkexOrionApiEngine()

    def test_zero_pads_to_five_digits(self):
        self.assertEqual(self.engine.format_hkex_stock_code("700"), "00700")
        self.assertEqual(self.engine.format_hkex_stock_code("5"), "00005")
        self.assertEqual(self.engine.format_hkex_stock_code("80700"), "80700")
        self.assertEqual(self.engine.format_hkex_stock_code(" 00700 "), "00700")

    def test_rejects_code_longer_than_five_digits(self):
        # Regression: bare zfill(5) passed '123456' through unchanged, so an OMS bug
        # that concatenated two codes reached the gateway as a "formatted" code.
        with self.assertRaises(InvalidStockCodeError):
            self.engine.format_hkex_stock_code("123456")

    def test_rejects_non_numeric_and_empty_and_all_zero_codes(self):
        for bad in ["", "   ", "70O", "700A", "-700", "7.00", "٧٠٠"]:
            with self.subTest(code=bad), self.assertRaises(InvalidStockCodeError):
                self.engine.format_hkex_stock_code(bad)
        with self.assertRaises(InvalidStockCodeError):
            self.engine.format_hkex_stock_code("0")

    def test_dual_counter_classification(self):
        # HKD counter 0XXXX / RMB counter 8XXXX share the last four digits.
        self.assertIs(self.engine.classify_counter("00700"), CounterType.HKD_COUNTER)
        self.assertIs(self.engine.classify_counter("80700"), CounterType.RMB_COUNTER)
        self.assertIs(self.engine.classify_counter("12345"), CounterType.OTHER)


class TestSecondScheduleSpreadTables(unittest.TestCase):
    """Tick sizes transcribed from the Second Schedule, Parts A, B, D and E."""

    def setUp(self):
        self.engine = HkexOrionApiEngine()

    def test_part_a_every_band_upper_bound_is_inclusive(self):
        # The Schedule reads "Over X to Y": Y belongs to the LOWER band. An
        # upper-exclusive comparison gets every one of these eleven edges wrong.
        edges = [
            ("0.25", "0.001"),
            ("10.00", "0.005"),
            ("20.00", "0.010"),
            ("50.00", "0.020"),
            ("100.00", "0.050"),
            ("200.00", "0.100"),
            ("500.00", "0.200"),
            ("1000.00", "0.500"),
            ("2000.00", "1.000"),
            ("5000.00", "2.000"),
            ("9995.00", "5.000"),
        ]
        for price, tick in edges:
            with self.subTest(price=price):
                self.assertEqual(
                    self.engine.get_hkex_spread_table_tick_size(price), D(tick)
                )

    def test_part_a_just_above_each_edge_moves_to_the_next_band(self):
        cases = [
            ("0.251", "0.005"),
            ("10.005", "0.010"),
            ("20.01", "0.020"),
            ("50.02", "0.050"),
            ("100.05", "0.100"),
            ("200.10", "0.200"),
            ("500.20", "0.500"),
            ("1000.50", "1.000"),
            ("2001.00", "2.000"),
            ("5002.00", "5.000"),
        ]
        for price, tick in cases:
            with self.subTest(price=price):
                self.assertEqual(
                    self.engine.get_hkex_spread_table_tick_size(price), D(tick)
                )

    def test_part_a_reflects_minimum_spread_reduction_phases_1_and_2(self):
        # Regression against the pre-2025 table: these four prices all returned a
        # coarser tick before Phase 1 (2025-08-04) and Phase 2 (2026-08-03).
        self.assertEqual(self.engine.get_hkex_spread_table_tick_size("5.00"), D("0.005"))
        self.assertEqual(self.engine.get_hkex_spread_table_tick_size("0.40"), D("0.005"))
        self.assertEqual(self.engine.get_hkex_spread_table_tick_size("15.00"), D("0.010"))
        self.assertEqual(self.engine.get_hkex_spread_table_tick_size("30.00"), D("0.020"))

    def test_part_a_bands_above_one_thousand_are_not_flattened_to_half_a_dollar(self):
        # Regression: the old table stopped at "P >= 500 -> 0.500", so a HK$1,500.50
        # price was reported as a legal tick when the Schedule requires 1.000.
        self.assertEqual(self.engine.get_hkex_spread_table_tick_size("1500.00"), D("1.000"))
        self.assertEqual(self.engine.get_hkex_spread_table_tick_size("3000.00"), D("2.000"))
        self.assertEqual(self.engine.get_hkex_spread_table_tick_size("7000.00"), D("5.000"))

        report = self.engine.validate_and_prepare_order(_order(price="1500.50"))
        self.assertFalse(report.is_price_tick_valid)
        self.assertEqual(report.applicable_tick_size, D("1.000"))

    def test_price_outside_the_published_bands_raises(self):
        # Part A runs 0.01 to 9,995.00. Outside it there is no minimum spread at all,
        # so returning the nearest band's tick would be an invention.
        for bad in ["0.009", "0", "9995.01", "10000.00"]:
            with self.subTest(price=bad), self.assertRaises(PriceOutOfRangeError):
                self.engine.get_hkex_spread_table_tick_size(bad)

    def test_part_e_structured_products_differ_from_part_a(self):
        # Structured Products were carved out into spread table code 06 at Phase 1 and
        # keep the pre-reduction bands: HK$5.00 ticks at 0.010, not Part A's 0.005.
        self.assertEqual(
            self.engine.get_hkex_spread_table_tick_size("5.00", SpreadTable.PART_E),
            D("0.010"),
        )
        self.assertEqual(
            self.engine.get_hkex_spread_table_tick_size("15.00", SpreadTable.PART_E),
            D("0.020"),
        )
        self.assertEqual(
            self.engine.get_hkex_spread_table_tick_size("0.50", SpreadTable.PART_E),
            D("0.005"),
        )

    def test_part_d_exchange_traded_funds(self):
        cases = [("1.00", "0.001"), ("3.00", "0.002"), ("10.00", "0.005"),
                 ("50.00", "0.020"), ("9999.00", "1.000")]
        for price, tick in cases:
            with self.subTest(price=price):
                self.assertEqual(
                    self.engine.get_hkex_spread_table_tick_size(price, SpreadTable.PART_D),
                    D(tick),
                )

    def test_part_b_is_flat_and_starts_at_fifty_cents(self):
        self.assertEqual(
            self.engine.get_hkex_spread_table_tick_size("0.50", SpreadTable.PART_B),
            D("0.050"),
        )
        self.assertEqual(
            self.engine.get_hkex_spread_table_tick_size("9999.95", SpreadTable.PART_B),
            D("0.050"),
        )
        with self.assertRaises(PriceOutOfRangeError):
            self.engine.get_hkex_spread_table_tick_size("0.40", SpreadTable.PART_B)

    def test_part_c_options_scale_is_not_invented(self):
        with self.assertRaises(SpreadTableUnavailableError):
            self.engine.get_hkex_spread_table_tick_size("50.00", SpreadTable.PART_C)

    def test_omd_c_spread_table_code_mapping(self):
        self.assertIs(SpreadTable.from_omd_c_code("01"), SpreadTable.PART_A)
        self.assertIs(SpreadTable.from_omd_c_code("1"), SpreadTable.PART_A)
        self.assertIs(SpreadTable.from_omd_c_code("06"), SpreadTable.PART_E)
        # 03/04/05 are in use but HKEX does not publish which is which.
        for unmapped in ["03", "04", "05", "99"]:
            with self.subTest(code=unmapped), self.assertRaises(SpreadTableUnavailableError):
                SpreadTable.from_omd_c_code(unmapped)


class TestTickAlignment(unittest.TestCase):

    def setUp(self):
        self.engine = HkexOrionApiEngine()

    def test_valid_order(self):
        report = self.engine.validate_and_prepare_order(_order())
        self.assertEqual(report.formatted_stock_code, "00700")
        self.assertEqual(report.status, "ORDER_VALIDATED")
        self.assertEqual(report.violations, ())
        self.assertEqual(report.applicable_tick_size, D("0.200"))
        self.assertIs(report.lot_classification, LotClassification.BOARD_LOT)
        self.assertIs(report.counter_type, CounterType.HKD_COUNTER)

    def test_off_tick_price_rejected(self):
        report = self.engine.validate_and_prepare_order(_order(price="300.15"))
        self.assertEqual(report.status, "INVALID_TICK_SIZE")
        self.assertFalse(report.is_price_tick_valid)
        self.assertIn("INVALID_TICK_SIZE", report.violations)

    def test_three_decimal_price_is_exact_under_decimal_arithmetic(self):
        # 8.615 is a legal Part A price (0.005 tick). Binary float division makes
        # 8.615 / 0.005 = 1722.9999999999998, which a tolerance-based check can only
        # accept by also accepting genuinely off-tick prices.
        report = self.engine.validate_and_prepare_order(_order(price="8.615", quantity=1000))
        self.assertTrue(report.is_price_tick_valid)
        self.assertEqual(report.applicable_tick_size, D("0.005"))

        off = self.engine.validate_and_prepare_order(_order(price="8.6155", quantity=1000))
        self.assertFalse(off.is_price_tick_valid)

    def test_float_price_is_read_through_its_repr_not_its_binary_value(self):
        report = self.engine.validate_and_prepare_order(_order(price=300.20))
        self.assertEqual(report.price, D("300.20"))
        self.assertTrue(report.is_price_tick_valid)

    def test_non_numeric_and_non_finite_prices_raise(self):
        for bad in ["abc", None, float("nan"), float("inf")]:
            with self.subTest(price=bad), self.assertRaises(HkexOrderError):
                self.engine.validate_and_prepare_order(_order(price=bad))


class TestBoardLotAndOrderSize(unittest.TestCase):

    def setUp(self):
        self.engine = HkexOrionApiEngine()

    def test_odd_lot_and_special_lot_are_distinguished(self):
        odd = self.engine.validate_and_prepare_order(_order(quantity=50))
        self.assertIs(odd.lot_classification, LotClassification.ODD_LOT)
        self.assertEqual(odd.status, "INVALID_BOARD_LOT")

        special = self.engine.validate_and_prepare_order(_order(quantity=250))
        self.assertIs(special.lot_classification, LotClassification.SPECIAL_LOT)
        self.assertEqual(special.status, "INVALID_BOARD_LOT")

    def test_issuer_board_lot_other_than_one_hundred(self):
        # HKEX board lots are issuer-set (10 to 100,000 shares); 500 and 2,000 are
        # common. 2,000 shares against a 500 lot is 4 lots and must validate.
        report = self.engine.validate_and_prepare_order(_order(quantity=2000, board_lot_size=500))
        self.assertEqual(report.status, "ORDER_VALIDATED")
        self.assertEqual(report.order_size_board_lots, D(4))

    def test_zero_board_lot_size_raises_instead_of_dividing_by_zero(self):
        # Regression: `quantity % board_lot_size` raised an unhandled ZeroDivisionError.
        with self.assertRaises(HkexOrderError):
            self.engine.validate_and_prepare_order(_order(board_lot_size=0))

    def test_negative_board_lot_size_is_not_treated_as_a_clean_multiple(self):
        # Regression: 200 % -100 == 0 in Python, so a negative lot size validated.
        with self.assertRaises(HkexOrderError):
            self.engine.validate_and_prepare_order(_order(board_lot_size=-100))

    def test_classify_lot_guards_its_own_inputs_when_called_directly(self):
        for qty, lot in [(0, 100), (-100, 100), (200, 0), (200, -100)]:
            with self.subTest(quantity=qty, lot=lot), self.assertRaises(HkexOrderError):
                self.engine.classify_lot(qty, lot)

    def test_maximum_automatch_order_size_is_three_thousand_board_lots(self):
        at_cap = self.engine.validate_and_prepare_order(
            _order(price="10.00", quantity=100 * MAX_ORDER_SIZE_BOARD_LOTS)
        )
        self.assertEqual(at_cap.status, "ORDER_VALIDATED")
        self.assertTrue(at_cap.is_order_size_valid)

        over_cap = self.engine.validate_and_prepare_order(
            _order(price="10.00", quantity=100 * (MAX_ORDER_SIZE_BOARD_LOTS + 1))
        )
        self.assertEqual(over_cap.status, "INVALID_ORDER_SIZE")
        self.assertFalse(over_cap.is_order_size_valid)

    def test_non_positive_and_non_integer_quantities_raise(self):
        for bad in [0, -100, 100.5, "100", True]:
            with self.subTest(quantity=bad), self.assertRaises(HkexOrderError):
                self.engine.validate_and_prepare_order(_order(quantity=bad))


class TestMultipleViolationsAndFieldValidation(unittest.TestCase):

    def setUp(self):
        self.engine = HkexOrionApiEngine()

    def test_an_order_breaching_two_rules_reports_both(self):
        # Regression: the old engine returned on the first failure, so an off-tick
        # odd-lot order was fixed for tick size and rejected again for the board lot.
        report = self.engine.validate_and_prepare_order(_order(price="300.15", quantity=50))
        self.assertEqual(report.status, "INVALID_TICK_SIZE")
        self.assertEqual(
            report.violations, ("INVALID_TICK_SIZE", "INVALID_BOARD_LOT")
        )
        self.assertIn("300.15", report.audit_notes)
        self.assertIn("ODD_LOT", report.audit_notes)

    def test_unknown_side_order_type_and_currency_raise(self):
        with self.assertRaises(HkexOrderError):
            self.engine.validate_and_prepare_order(_order(side="SHORT"))
        with self.assertRaises(HkexOrderError):
            self.engine.validate_and_prepare_order(_order(order_type="MARKET"))
        with self.assertRaises(HkexOrderError):
            self.engine.validate_and_prepare_order(_order(currency="GBP"))

    def test_rmb_counter_order_is_classified_and_validated(self):
        report = self.engine.validate_and_prepare_order(
            _order(raw_stock_code="80700", currency="RMB")
        )
        self.assertEqual(report.status, "ORDER_VALIDATED")
        self.assertIs(report.counter_type, CounterType.RMB_COUNTER)
        # The Spread Table applies to all currency units, so the RMB counter of a
        # Part A security uses the same bands as its HKD counter.
        self.assertEqual(report.applicable_tick_size, D("0.200"))


if __name__ == "__main__":
    unittest.main()
