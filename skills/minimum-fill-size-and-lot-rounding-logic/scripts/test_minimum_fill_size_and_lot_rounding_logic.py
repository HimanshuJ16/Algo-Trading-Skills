import unittest
from decimal import Decimal

from minimum_fill_size_and_lot_rounding_logic import (
    MinimumFillSizeAndLotRoundingEngine,
    OrderRoundingConfig,
    RawOrderRequest,
    STATUS_ODD_LOT_ADJUSTED,
    STATUS_ODD_LOT_PRESERVED,
    STATUS_REJECTED_ABOVE_MAX_QTY,
    STATUS_REJECTED_BELOW_MIN_NOTIONAL,
    STATUS_REJECTED_BELOW_MIN_QTY,
    STATUS_SUCCESS,
    WARN_DEPTH_UNSATISFIED,
    WARN_LOT_SIZE_UNSOURCED,
    WARN_MIN_EXEC_NOT_LOT_MULTIPLE,
    WARN_MIN_EXEC_SUPPRESSES_DISPLAY,
    WARN_NOTIONAL_UNCHECKED,
    WARN_ROUNDED_UP,
    WARN_VENUE_MIN_NOT_LOT_MULTIPLE,
    to_quantity,
)

SOURCE = "unit-test-reference-data"


def equity_config(**overrides):
    """A sourced US-equity config: 100-share lot, 100-share venue minimum."""
    params = dict(
        symbol="AAPL",
        lot_size="100",
        min_order_quantity="100",
        rounding_mode="FLOOR",
        lot_size_source=SOURCE,
        lot_size_as_of="2026-08-26",
    )
    params.update(overrides)
    return OrderRoundingConfig(**params)


def order(**overrides):
    params = dict(order_id="ORD_1", symbol="AAPL", side="BUY", raw_quantity="275", limit_price="150")
    params.update(overrides)
    return RawOrderRequest(**params)


class LotRoundingArithmeticTests(unittest.TestCase):
    """Rounding must be exact decimal arithmetic, not binary float arithmetic."""

    def setUp(self):
        self.engine = MinimumFillSizeAndLotRoundingEngine()

    def test_fractional_step_size_is_not_corrupted_by_float_error(self):
        # Regression: math.floor(0.29 / 0.01) * 0.01 == 0.28 in binary floats, which
        # silently drops 0.01 BTC. 0.29 is an exact multiple of 0.01 and must survive.
        cfg = OrderRoundingConfig(
            "BTCUSDT", lot_size="0.01", min_order_quantity="0.01",
            rounding_mode="FLOOR", lot_size_source=SOURCE,
        )
        report = self.engine.apply_lot_rounding_and_min_qty_rules(
            cfg, RawOrderRequest("O", "BTCUSDT", "BUY", "0.29", limit_price="60000")
        )
        self.assertEqual(report.rounded_quantity, Decimal("0.29"))
        self.assertEqual(report.quantity_delta, Decimal("0"))
        self.assertFalse(report.is_odd_lot_request)
        self.assertEqual(report.status, STATUS_SUCCESS)

    def test_fractional_step_size_floors_a_genuine_remainder(self):
        # 0.2949 / 0.01 = 29.49 lots -> 29 lots -> 0.29 (independently derived).
        cfg = OrderRoundingConfig(
            "BTCUSDT", lot_size="0.01", min_order_quantity="0.01",
            rounding_mode="FLOOR", lot_size_source=SOURCE,
        )
        report = self.engine.apply_lot_rounding_and_min_qty_rules(
            cfg, RawOrderRequest("O", "BTCUSDT", "BUY", "0.2949", limit_price="60000")
        )
        self.assertEqual(report.rounded_quantity, Decimal("0.29"))
        self.assertEqual(report.quantity_delta, Decimal("-0.0049"))
        self.assertTrue(report.is_odd_lot_request)

    def test_float_inputs_are_read_as_their_decimal_literal(self):
        cfg = OrderRoundingConfig(
            "BTCUSDT", lot_size=0.01, min_order_quantity=0.01,
            rounding_mode="FLOOR", lot_size_source=SOURCE,
        )
        report = self.engine.apply_lot_rounding_and_min_qty_rules(
            cfg, RawOrderRequest("O", "BTCUSDT", "BUY", 0.29, limit_price=60000.0)
        )
        self.assertEqual(report.rounded_quantity, Decimal("0.29"))

    def test_floor_rounds_down_and_reports_a_negative_delta(self):
        # 275 / 100 = 2.75 lots -> 2 lots -> 200.
        report = self.engine.apply_lot_rounding_and_min_qty_rules(equity_config(), order())
        self.assertEqual(report.rounded_quantity, Decimal("200"))
        self.assertEqual(report.quantity_delta, Decimal("-75"))
        self.assertEqual(report.status, STATUS_ODD_LOT_ADJUSTED)
        self.assertTrue(report.is_odd_lot_request)
        self.assertFalse(report.routes_odd_lot)
        self.assertNotIn(WARN_ROUNDED_UP, report.warnings)

    def test_ceil_rounds_up_and_flags_the_overshoot(self):
        # 275 / 100 = 2.75 lots -> 3 lots -> 300, i.e. 25 shares MORE than requested.
        report = self.engine.apply_lot_rounding_and_min_qty_rules(
            equity_config(rounding_mode="CEIL"), order()
        )
        self.assertEqual(report.rounded_quantity, Decimal("300"))
        self.assertEqual(report.quantity_delta, Decimal("25"))
        self.assertIn(WARN_ROUNDED_UP, report.warnings)

    def test_half_up_rounds_a_tie_away_from_zero(self):
        # Regression: Python's round() is banker's rounding, so the old engine turned
        # 250 into 200 while turning 350 into 400. Half-up gives 300 and 400.
        cfg = equity_config(rounding_mode="ROUND_HALF_UP")
        for raw, expected in (("250", "300"), ("350", "400"), ("150", "200"), ("249", "200")):
            with self.subTest(raw=raw):
                report = self.engine.apply_lot_rounding_and_min_qty_rules(
                    cfg, order(raw_quantity=raw)
                )
                self.assertEqual(report.rounded_quantity, Decimal(expected))

    def test_price_tiered_round_lot_is_applied_as_configured(self):
        # Reg NMS round lots have been price-tiered since 3 Nov 2025: a $600 stock
        # has a 40-share round lot, so 100 shares floors to 80, not to 100.
        cfg = equity_config(lot_size="40", min_order_quantity="40")
        report = self.engine.apply_lot_rounding_and_min_qty_rules(
            cfg, order(raw_quantity="100", limit_price="600")
        )
        self.assertEqual(report.rounded_quantity, Decimal("80"))

    def test_large_per_security_board_lot(self):
        # HKEX board lots are per security and can be far larger than 100.
        cfg = OrderRoundingConfig(
            "0700.HK", lot_size="100", min_order_quantity="100",
            rounding_mode="FLOOR", lot_size_source=SOURCE,
        )
        report = self.engine.apply_lot_rounding_and_min_qty_rules(
            cfg, RawOrderRequest("O", "0700.HK", "BUY", "250", limit_price="400")
        )
        self.assertEqual(report.rounded_quantity, Decimal("200"))

    def test_scientific_notation_lot_size_is_reported_in_plain_digits(self):
        cfg = equity_config(lot_size="1E+2", min_order_quantity="1E+2")
        report = self.engine.apply_lot_rounding_and_min_qty_rules(cfg, order(raw_quantity="250"))
        self.assertEqual(report.rounded_quantity, Decimal("200"))
        self.assertEqual(str(report.rounded_quantity), "200")

    def test_tiny_step_size_against_a_large_quantity_is_evaluated_exactly(self):
        cfg = OrderRoundingConfig(
            "X", lot_size="0.00000001", min_order_quantity="0.00000001",
            rounding_mode="FLOOR", lot_size_source=SOURCE,
        )
        report = self.engine.apply_lot_rounding_and_min_qty_rules(
            cfg, RawOrderRequest("O", "X", "BUY", "1E+30")
        )
        self.assertEqual(report.rounded_quantity, Decimal("1E+30"))
        self.assertEqual(report.status, STATUS_SUCCESS)

    def test_implausible_quantity_to_lot_ratio_raises_a_clear_error(self):
        # Must surface as ValueError, not as a bare decimal.InvalidOperation from
        # deep inside the arithmetic.
        cfg = OrderRoundingConfig(
            "X", lot_size="1E-40", min_order_quantity="1E-40",
            rounding_mode="FLOOR", lot_size_source=SOURCE,
        )
        with self.assertRaises(ValueError):
            self.engine.apply_lot_rounding_and_min_qty_rules(
                cfg, RawOrderRequest("O", "X", "BUY", "1E+40")
            )

    def test_exact_multiple_is_left_alone(self):
        report = self.engine.apply_lot_rounding_and_min_qty_rules(
            equity_config(), order(raw_quantity="300")
        )
        self.assertEqual(report.rounded_quantity, Decimal("300"))
        self.assertEqual(report.status, STATUS_SUCCESS)
        self.assertFalse(report.is_odd_lot_request)


class OddLotPolicyTests(unittest.TestCase):
    def setUp(self):
        self.engine = MinimumFillSizeAndLotRoundingEngine()

    def test_allow_odd_lots_actually_preserves_the_odd_lot(self):
        # Regression: allow_odd_lots was inert -- 275 was still rounded to 200.
        cfg = equity_config(allow_odd_lots=True, min_order_quantity="1")
        report = self.engine.apply_lot_rounding_and_min_qty_rules(cfg, order())
        self.assertEqual(report.rounded_quantity, Decimal("275"))
        self.assertEqual(report.quantity_delta, Decimal("0"))
        self.assertEqual(report.status, STATUS_ODD_LOT_PRESERVED)
        self.assertTrue(report.routes_odd_lot)

    def test_allow_odd_lots_still_enforces_the_venue_minimum(self):
        cfg = equity_config(allow_odd_lots=True, min_order_quantity="100")
        report = self.engine.apply_lot_rounding_and_min_qty_rules(cfg, order(raw_quantity="37"))
        self.assertFalse(report.is_compliant)
        self.assertEqual(report.status, STATUS_REJECTED_BELOW_MIN_QTY)

    def test_allow_odd_lots_does_not_change_an_exact_multiple(self):
        cfg = equity_config(allow_odd_lots=True)
        report = self.engine.apply_lot_rounding_and_min_qty_rules(cfg, order(raw_quantity="200"))
        self.assertEqual(report.rounded_quantity, Decimal("200"))
        self.assertEqual(report.status, STATUS_SUCCESS)
        self.assertFalse(report.routes_odd_lot)


class TerminalOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.engine = MinimumFillSizeAndLotRoundingEngine()

    def test_below_venue_minimum_is_rejected_with_a_zeroed_quantity(self):
        report = self.engine.apply_lot_rounding_and_min_qty_rules(
            equity_config(), order(raw_quantity="50")
        )
        self.assertFalse(report.is_compliant)
        self.assertEqual(report.status, STATUS_REJECTED_BELOW_MIN_QTY)
        self.assertEqual(report.rounded_quantity, Decimal("0"))

    def test_exactly_the_venue_minimum_is_accepted(self):
        report = self.engine.apply_lot_rounding_and_min_qty_rules(
            equity_config(), order(raw_quantity="100")
        )
        self.assertTrue(report.is_compliant)
        self.assertEqual(report.rounded_quantity, Decimal("100"))

    def test_above_venue_maximum_is_rejected(self):
        cfg = equity_config(max_order_quantity="1000")
        report = self.engine.apply_lot_rounding_and_min_qty_rules(
            cfg, order(raw_quantity="1500")
        )
        self.assertFalse(report.is_compliant)
        self.assertEqual(report.status, STATUS_REJECTED_ABOVE_MAX_QTY)

    def test_exactly_the_venue_maximum_is_accepted(self):
        cfg = equity_config(max_order_quantity="1000")
        report = self.engine.apply_lot_rounding_and_min_qty_rules(
            cfg, order(raw_quantity="1000")
        )
        self.assertTrue(report.is_compliant)

    def test_rounding_down_below_the_minimum_notional_is_rejected(self):
        # 0.00002 BTC floors to 0.00002; 0.00002 * 60000 = 1.2 < 5 minimum notional.
        cfg = OrderRoundingConfig(
            "BTCUSDT", lot_size="0.00001", min_order_quantity="0.00001",
            min_notional="5", rounding_mode="FLOOR", lot_size_source=SOURCE,
        )
        report = self.engine.apply_lot_rounding_and_min_qty_rules(
            cfg, RawOrderRequest("O", "BTCUSDT", "BUY", "0.00002", limit_price="60000")
        )
        self.assertFalse(report.is_compliant)
        self.assertEqual(report.status, STATUS_REJECTED_BELOW_MIN_NOTIONAL)
        self.assertEqual(report.notional, Decimal("1.2"))

    def test_notional_is_reported_for_a_compliant_order(self):
        report = self.engine.apply_lot_rounding_and_min_qty_rules(equity_config(), order())
        self.assertEqual(report.notional, Decimal("30000"))

    def test_market_order_without_a_price_cannot_be_notional_checked(self):
        cfg = equity_config(min_notional="1000000")
        report = self.engine.apply_lot_rounding_and_min_qty_rules(
            cfg, order(limit_price=None)
        )
        self.assertIsNone(report.notional)
        self.assertIn(WARN_NOTIONAL_UNCHECKED, report.warnings)
        self.assertTrue(report.is_compliant)


class WarningReportingTests(unittest.TestCase):
    def setUp(self):
        self.engine = MinimumFillSizeAndLotRoundingEngine()

    def test_depth_warning_survives_a_later_rounding_outcome(self):
        # Regression: the depth status used to be overwritten by the odd-lot status,
        # so MIN_QTY_DEPTH_UNSATISFIED never reached the caller.
        report = self.engine.apply_lot_rounding_and_min_qty_rules(
            equity_config(), order(available_liquidity_depth="50")
        )
        self.assertEqual(report.status, STATUS_ODD_LOT_ADJUSTED)
        self.assertIn(WARN_DEPTH_UNSATISFIED, report.warnings)

    def test_depth_warning_is_measured_against_min_execution_quantity_when_set(self):
        # 250 shares of depth would partially fill a plain 300-share order, but a
        # MinQty of 300 needs all 300 available at once, so the fill is unlikely.
        report = self.engine.apply_lot_rounding_and_min_qty_rules(
            equity_config(rounding_mode="CEIL"),
            order(available_liquidity_depth="250", min_execution_quantity="300"),
        )
        self.assertEqual(report.rounded_quantity, Decimal("300"))
        self.assertIn(WARN_DEPTH_UNSATISFIED, report.warnings)

    def test_no_depth_warning_when_depth_is_sufficient(self):
        report = self.engine.apply_lot_rounding_and_min_qty_rules(
            equity_config(), order(available_liquidity_depth="5000")
        )
        self.assertNotIn(WARN_DEPTH_UNSATISFIED, report.warnings)

    def test_unmeasured_depth_produces_no_false_reassurance(self):
        report = self.engine.apply_lot_rounding_and_min_qty_rules(equity_config(), order())
        self.assertNotIn(WARN_DEPTH_UNSATISFIED, report.warnings)

    def test_warnings_are_reported_on_a_rejected_order_too(self):
        cfg = equity_config(lot_size_source=None, lot_size_as_of=None)
        report = self.engine.apply_lot_rounding_and_min_qty_rules(
            cfg, order(raw_quantity="50", available_liquidity_depth="0")
        )
        self.assertFalse(report.is_compliant)
        self.assertIn(WARN_LOT_SIZE_UNSOURCED, report.warnings)

    def test_unsourced_lot_size_is_flagged(self):
        cfg = equity_config(lot_size_source=None, lot_size_as_of=None)
        report = self.engine.apply_lot_rounding_and_min_qty_rules(cfg, order())
        self.assertIn(WARN_LOT_SIZE_UNSOURCED, report.warnings)

    def test_sourced_lot_size_is_not_flagged(self):
        report = self.engine.apply_lot_rounding_and_min_qty_rules(equity_config(), order())
        self.assertNotIn(WARN_LOT_SIZE_UNSOURCED, report.warnings)

    def test_venue_minimum_that_is_not_a_lot_multiple_is_flagged(self):
        cfg = equity_config(min_order_quantity="150")
        report = self.engine.apply_lot_rounding_and_min_qty_rules(
            cfg, order(raw_quantity="500")
        )
        self.assertIn(WARN_VENUE_MIN_NOT_LOT_MULTIPLE, report.warnings)

    def test_reports_do_not_share_a_warnings_list(self):
        first = self.engine.apply_lot_rounding_and_min_qty_rules(
            equity_config(), order(available_liquidity_depth="1")
        )
        second = self.engine.apply_lot_rounding_and_min_qty_rules(equity_config(), order())
        self.assertIn(WARN_DEPTH_UNSATISFIED, first.warnings)
        self.assertEqual(second.warnings, [])


class FixExecutionConstraintTests(unittest.TestCase):
    """FIX Tag 110 / 1089 are client instructions, not venue reference data."""

    def setUp(self):
        self.engine = MinimumFillSizeAndLotRoundingEngine()

    def test_tag_110_is_absent_unless_the_caller_asks_for_it(self):
        # Regression: the old engine stamped Tag 110 with the venue minimum on every
        # order, which on Nasdaq would suppress display of an order meant to rest lit.
        report = self.engine.apply_lot_rounding_and_min_qty_rules(equity_config(), order())
        self.assertIsNone(report.fix_tag_110_min_qty)
        self.assertIsNone(report.fix_tag_1089_match_increment)
        self.assertNotIn(WARN_MIN_EXEC_SUPPRESSES_DISPLAY, report.warnings)

    def test_tag_110_is_carried_through_with_its_display_side_effect(self):
        report = self.engine.apply_lot_rounding_and_min_qty_rules(
            equity_config(), order(raw_quantity="500", min_execution_quantity="200")
        )
        self.assertEqual(report.fix_tag_110_min_qty, Decimal("200"))
        self.assertIn(WARN_MIN_EXEC_SUPPRESSES_DISPLAY, report.warnings)
        self.assertNotIn(WARN_MIN_EXEC_NOT_LOT_MULTIPLE, report.warnings)

    def test_mixed_lot_min_execution_quantity_is_flagged(self):
        # Nasdaq rounds a mixed-lot minimum quantity condition down to the nearest
        # round lot, so 150 silently becomes 100 at the venue.
        report = self.engine.apply_lot_rounding_and_min_qty_rules(
            equity_config(), order(raw_quantity="500", min_execution_quantity="150")
        )
        self.assertIn(WARN_MIN_EXEC_NOT_LOT_MULTIPLE, report.warnings)

    def test_min_execution_quantity_above_the_routed_quantity_raises(self):
        with self.assertRaises(ValueError):
            self.engine.apply_lot_rounding_and_min_qty_rules(
                equity_config(), order(raw_quantity="275", min_execution_quantity="300")
            )

    def test_match_increment_above_the_routed_quantity_raises(self):
        with self.assertRaises(ValueError):
            self.engine.apply_lot_rounding_and_min_qty_rules(
                equity_config(), order(raw_quantity="275", match_increment="500")
            )

    def test_match_increment_is_carried_through(self):
        report = self.engine.apply_lot_rounding_and_min_qty_rules(
            equity_config(), order(raw_quantity="500", match_increment="100")
        )
        self.assertEqual(report.fix_tag_1089_match_increment, Decimal("100"))


class InputValidationTests(unittest.TestCase):
    def setUp(self):
        self.engine = MinimumFillSizeAndLotRoundingEngine()

    def test_lot_size_and_min_order_quantity_are_required(self):
        with self.assertRaises(TypeError):
            OrderRoundingConfig("AAPL")

    def test_non_positive_venue_constraints_raise(self):
        for kwargs in ({"lot_size": "0"}, {"lot_size": "-100"}, {"min_order_quantity": "0"}):
            with self.subTest(**kwargs), self.assertRaises(ValueError):
                equity_config(**kwargs)

    def test_removed_round_nearest_mode_raises_with_migration_guidance(self):
        with self.assertRaises(ValueError) as ctx:
            equity_config(rounding_mode="ROUND_NEAREST")
        self.assertIn("ROUND_HALF_UP", str(ctx.exception))

    def test_unknown_rounding_mode_raises(self):
        with self.assertRaises(ValueError):
            equity_config(rounding_mode="TRUNCATE")

    def test_max_below_min_raises(self):
        with self.assertRaises(ValueError):
            equity_config(min_order_quantity="1000", max_order_quantity="100")

    def test_non_finite_quantities_raise(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                order(raw_quantity=bad)

    def test_non_positive_quantity_raises(self):
        for bad in ("0", "-100"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                order(raw_quantity=bad)

    def test_unparseable_quantity_raises(self):
        with self.assertRaises(ValueError):
            order(raw_quantity="two hundred")

    def test_bool_quantity_raises(self):
        with self.assertRaises(TypeError):
            order(raw_quantity=True)

    def test_unsupported_quantity_type_raises(self):
        with self.assertRaises(TypeError):
            order(raw_quantity=[275])

    def test_invalid_side_raises(self):
        with self.assertRaises(ValueError):
            order(side="LONG")

    def test_side_is_normalised_to_upper_case(self):
        self.assertEqual(order(side=" buy ").side, "BUY")

    def test_blank_identifiers_raise(self):
        with self.assertRaises(ValueError):
            order(order_id="   ")
        with self.assertRaises(ValueError):
            equity_config(symbol="")

    def test_non_positive_limit_price_raises(self):
        with self.assertRaises(ValueError):
            order(limit_price="0")

    def test_negative_depth_raises(self):
        with self.assertRaises(ValueError):
            order(available_liquidity_depth="-1")

    def test_zero_depth_is_allowed_and_warns(self):
        report = self.engine.apply_lot_rounding_and_min_qty_rules(
            equity_config(), order(available_liquidity_depth="0")
        )
        self.assertIn(WARN_DEPTH_UNSATISFIED, report.warnings)

    def test_symbol_mismatch_between_config_and_order_raises(self):
        with self.assertRaises(ValueError):
            self.engine.apply_lot_rounding_and_min_qty_rules(
                equity_config(), order(symbol="MSFT")
            )

    def test_to_quantity_rejects_nan_that_slips_past_a_naive_positive_check(self):
        nan = float("nan")
        self.assertFalse(nan <= 0)  # the guard the old implementation relied on
        with self.assertRaises(ValueError):
            to_quantity(nan, "raw_quantity")


class DeterminismTests(unittest.TestCase):
    def test_repeated_calls_produce_identical_reports(self):
        engine = MinimumFillSizeAndLotRoundingEngine()
        first = engine.apply_lot_rounding_and_min_qty_rules(equity_config(), order())
        second = engine.apply_lot_rounding_and_min_qty_rules(equity_config(), order())
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
