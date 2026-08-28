"""Behavioural tests for the pegged-order pricing engine.

Expected prices are derived independently of the implementation: from the
worked example in the Nasdaq Rule 4703(d) filing, from the arithmetic
definition of each peg reference, and from the tick lattice.
"""

import unittest
from decimal import Decimal

from peg_order_types_for_passive_execution import (
    NBBOQuote,
    PegOrder,
    PegOrderTypesForPassiveExecutionEngine,
    PegPricingConfig,
    PegSpecError,
    PegStatus,
    PegType,
    RoundDirection,
    Side,
    SuspendReason,
)

D = Decimal


def quote(bid="100.00", ask="100.10", **kwargs):
    return NBBOQuote("AAPL", D(bid), D(ask), **kwargs)


class TestReferencePrice(unittest.TestCase):
    """Peg references per Nasdaq Rule 4703(d) / FIX PegPriceType(1094)."""

    def setUp(self):
        self.engine = PegOrderTypesForPassiveExecutionEngine()
        self.nbbo = quote()

    def _price(self, side, peg_type, **kwargs):
        return self.engine.calculate_pegged_price(
            PegOrder("REF", "AAPL", side, peg_type, **kwargs), self.nbbo
        )

    def test_primary_peg_references_the_same_side_of_the_market(self):
        self.assertEqual(self._price("BUY", "PRIMARY").reference_price, D("100.00"))
        self.assertEqual(self._price("SELL", "PRIMARY").reference_price, D("100.10"))

    def test_market_peg_references_the_opposite_side_of_the_market(self):
        self.assertEqual(self._price("BUY", "MARKET").reference_price, D("100.10"))
        self.assertEqual(self._price("SELL", "MARKET").reference_price, D("100.00"))

    def test_midpoint_peg_is_the_arithmetic_mean_of_the_inside_quotes(self):
        # (100.00 + 100.10) / 2 = 100.05, computed by hand.
        self.assertEqual(self._price("BUY", "MIDPOINT").reference_price, D("100.05"))
        self.assertEqual(self._price("SELL", "MIDPOINT").reference_price, D("100.05"))

    def test_enum_and_string_inputs_are_equivalent(self):
        from_string = self._price("BUY", "PRIMARY")
        from_enum = self.engine.calculate_pegged_price(
            PegOrder("REF", "AAPL", Side.BUY, PegType.PRIMARY), self.nbbo
        )
        self.assertEqual(from_string.effective_limit_price, from_enum.effective_limit_price)


class TestOffsetConvention(unittest.TestCase):
    """Offsets are side-relative and aggressive-positive.

    Expected values are the worked example in the Nasdaq rule filing: a buy with
    Primary Pegging against an $11.00 inside bid prices at $11.02 with an
    aggressive $0.02 offset and at $10.95 with a passive $0.05 offset.
    """

    def setUp(self):
        self.engine = PegOrderTypesForPassiveExecutionEngine()
        self.nbbo = NBBOQuote("AAPL", D("11.00"), D("11.20"))

    def test_aggressive_buy_offset_prices_above_the_inside_bid(self):
        report = self.engine.calculate_pegged_price(
            PegOrder("O1", "AAPL", "BUY", "PRIMARY", offset=D("0.02")), self.nbbo
        )
        self.assertEqual(report.effective_limit_price, D("11.02"))

    def test_passive_buy_offset_prices_below_the_inside_bid(self):
        report = self.engine.calculate_pegged_price(
            PegOrder("O2", "AAPL", "BUY", "PRIMARY", offset=D("-0.05")), self.nbbo
        )
        self.assertEqual(report.effective_limit_price, D("10.95"))

    def test_sell_offset_direction_is_mirrored(self):
        aggressive = self.engine.calculate_pegged_price(
            PegOrder("O3", "AAPL", "SELL", "PRIMARY", offset=D("0.02")), self.nbbo
        )
        passive = self.engine.calculate_pegged_price(
            PegOrder("O4", "AAPL", "SELL", "PRIMARY", offset=D("-0.05")), self.nbbo
        )
        self.assertEqual(aggressive.effective_limit_price, D("11.18"))
        self.assertEqual(passive.effective_limit_price, D("11.25"))


class TestPassivityEnforcement(unittest.TestCase):
    """A pegged order for passive execution must not cross or lock the contra."""

    def setUp(self):
        self.engine = PegOrderTypesForPassiveExecutionEngine()
        self.nbbo = quote()

    def test_market_peg_buy_is_clamped_below_the_offer(self):
        # Regression: an unclamped Market peg buy prices AT the offer (100.10)
        # and takes liquidity on arrival.
        report = self.engine.calculate_pegged_price(
            PegOrder("P1", "AAPL", "BUY", "MARKET"), self.nbbo
        )
        self.assertEqual(report.calculated_price, D("100.10"))
        self.assertEqual(report.effective_limit_price, D("100.09"))
        self.assertIn("PASSIVITY", report.clamps)
        self.assertFalse(report.is_marketable)

    def test_market_peg_sell_is_clamped_above_the_bid(self):
        report = self.engine.calculate_pegged_price(
            PegOrder("P2", "AAPL", "SELL", "MARKET"), self.nbbo
        )
        self.assertEqual(report.effective_limit_price, D("100.01"))
        self.assertFalse(report.is_marketable)

    def test_runaway_aggressive_offset_cannot_cross_the_spread(self):
        # Regression: offset large enough to price through the whole book.
        report = self.engine.calculate_pegged_price(
            PegOrder("P3", "AAPL", "BUY", "PRIMARY", offset=D("0.50")), self.nbbo
        )
        self.assertEqual(report.calculated_price, D("100.50"))
        self.assertEqual(report.effective_limit_price, D("100.09"))

    def test_passivity_can_be_disabled_and_marketability_is_reported(self):
        engine = PegOrderTypesForPassiveExecutionEngine(
            PegPricingConfig(enforce_non_marketable=False)
        )
        report = engine.calculate_pegged_price(
            PegOrder("P4", "AAPL", "BUY", "MARKET"), self.nbbo
        )
        self.assertEqual(report.effective_limit_price, D("100.10"))
        self.assertTrue(report.is_marketable)
        self.assertEqual(report.status, PegStatus.PRICED)

    def test_locked_market_midpoint_is_the_locking_price_and_is_clamped(self):
        report = self.engine.calculate_pegged_price(
            PegOrder("P5", "AAPL", "BUY", "MIDPOINT"), quote("50.00", "50.00")
        )
        self.assertEqual(report.reference_price, D("50.00"))
        self.assertEqual(report.effective_limit_price, D("49.99"))
        self.assertIn("PASSIVITY", report.clamps)


class TestLimitCap(unittest.TestCase):
    def setUp(self):
        self.engine = PegOrderTypesForPassiveExecutionEngine(
            PegPricingConfig(enforce_non_marketable=False)
        )
        self.nbbo = quote()

    def test_buy_cap_is_a_ceiling(self):
        report = self.engine.calculate_pegged_price(
            PegOrder("C1", "AAPL", "BUY", "MARKET", offset=D("0.05"), limit_cap=D("100.12")),
            self.nbbo,
        )
        self.assertEqual(report.calculated_price, D("100.15"))
        self.assertEqual(report.effective_limit_price, D("100.12"))
        self.assertTrue(report.is_cap_active)
        self.assertEqual(report.binding_constraint, "LIMIT_CAP")

    def test_sell_cap_is_a_floor(self):
        report = self.engine.calculate_pegged_price(
            PegOrder("C2", "AAPL", "SELL", "MARKET", offset=D("0.05"), limit_cap=D("100.03")),
            self.nbbo,
        )
        self.assertEqual(report.calculated_price, D("99.95"))
        self.assertEqual(report.effective_limit_price, D("100.03"))
        self.assertTrue(report.is_cap_active)

    def test_cap_exactly_equal_to_the_pegged_price_does_not_clamp(self):
        report = self.engine.calculate_pegged_price(
            PegOrder("C3", "AAPL", "BUY", "PRIMARY", offset=D("0.01"), limit_cap=D("100.01")),
            self.nbbo,
        )
        self.assertEqual(report.effective_limit_price, D("100.01"))
        self.assertFalse(report.is_cap_active)
        self.assertEqual(report.status, PegStatus.PRICED)

    def test_cap_far_from_the_market_leaves_the_peg_untouched(self):
        report = self.engine.calculate_pegged_price(
            PegOrder("C4", "AAPL", "BUY", "PRIMARY", limit_cap=D("200.00")), self.nbbo
        )
        self.assertEqual(report.effective_limit_price, D("100.00"))
        self.assertFalse(report.is_cap_active)


class TestTickQuantization(unittest.TestCase):
    def test_passive_rounding_moves_away_from_the_contra_side(self):
        engine = PegOrderTypesForPassiveExecutionEngine(
            PegPricingConfig(enforce_non_marketable=False)
        )
        buy = engine.calculate_pegged_price(
            PegOrder("T1", "AAPL", "BUY", "PRIMARY", offset=D("0.007")), quote()
        )
        sell = engine.calculate_pegged_price(
            PegOrder("T2", "AAPL", "SELL", "PRIMARY", offset=D("0.007")), quote()
        )
        self.assertEqual(buy.effective_limit_price, D("100.00"))  # 100.007 floored
        self.assertEqual(sell.effective_limit_price, D("100.10"))  # 100.093 ceiled

    def test_aggressive_rounding_moves_toward_the_contra_side(self):
        engine = PegOrderTypesForPassiveExecutionEngine(
            PegPricingConfig(
                round_direction=RoundDirection.AGGRESSIVE, enforce_non_marketable=False
            )
        )
        report = engine.calculate_pegged_price(
            PegOrder("T3", "AAPL", "BUY", "PRIMARY", offset=D("0.003")), quote()
        )
        self.assertEqual(report.effective_limit_price, D("100.01"))

    def test_aggressive_rounding_cannot_round_through_the_cap(self):
        # Regression: rounding 100.0031 up to 100.01 would breach a 100.006 cap.
        engine = PegOrderTypesForPassiveExecutionEngine(
            PegPricingConfig(
                round_direction=RoundDirection.AGGRESSIVE, enforce_non_marketable=False
            )
        )
        report = engine.calculate_pegged_price(
            PegOrder("T4", "AAPL", "BUY", "PRIMARY", offset=D("0.0031"), limit_cap=D("100.006")),
            quote(),
        )
        self.assertEqual(report.effective_limit_price, D("100.00"))
        self.assertLessEqual(report.effective_limit_price, D("100.006"))
        self.assertIn("LIMIT_CAP", report.clamps)

    def test_sub_dollar_tick_size_is_honoured(self):
        engine = PegOrderTypesForPassiveExecutionEngine()
        report = engine.calculate_pegged_price(
            PegOrder("T5", "SUB", "BUY", "MIDPOINT"),
            NBBOQuote("SUB", D("0.5000"), D("0.5003"), tick_size=D("0.0001")),
        )
        self.assertEqual(report.price_increment, D("0.0001"))
        self.assertEqual(report.effective_limit_price, D("0.5001"))  # 0.50015 floored

    def test_float_inputs_do_not_leak_binary_dust(self):
        engine = PegOrderTypesForPassiveExecutionEngine()
        report = engine.calculate_pegged_price(
            PegOrder("T6", "AAPL", "BUY", "PRIMARY", offset=0.07),
            NBBOQuote("AAPL", 10.10, 10.30),
        )
        self.assertEqual(report.effective_limit_price, D("10.17"))


class TestSubPennyMidpoint(unittest.TestCase):
    """Rule 612 bars sub-penny orders; a non-displayed midpoint peg is the
    recognised exception and prices on the half-tick lattice."""

    def setUp(self):
        self.engine = PegOrderTypesForPassiveExecutionEngine()
        self.nbbo = quote("100.00", "100.01")

    def test_non_displayed_midpoint_may_rest_on_the_half_tick(self):
        report = self.engine.calculate_pegged_price(
            PegOrder("S1", "AAPL", "BUY", "MIDPOINT", is_displayed=False), self.nbbo
        )
        self.assertEqual(report.price_increment, D("0.005"))
        self.assertEqual(report.effective_limit_price, D("100.005"))
        self.assertEqual(report.status, PegStatus.PRICED)

    def test_displayed_midpoint_is_forced_back_onto_the_penny_lattice(self):
        report = self.engine.calculate_pegged_price(
            PegOrder("S2", "AAPL", "BUY", "MIDPOINT", is_displayed=True), self.nbbo
        )
        self.assertEqual(report.price_increment, D("0.01"))
        self.assertEqual(report.effective_limit_price, D("100.00"))

    def test_sub_penny_midpoint_can_be_disabled_by_policy(self):
        engine = PegOrderTypesForPassiveExecutionEngine(
            PegPricingConfig(allow_subpenny_midpoint=False)
        )
        report = engine.calculate_pegged_price(
            PegOrder("S3", "AAPL", "BUY", "MIDPOINT", is_displayed=False), self.nbbo
        )
        self.assertEqual(report.effective_limit_price, D("100.00"))


class TestRegulatoryBounds(unittest.TestCase):
    def setUp(self):
        self.engine = PegOrderTypesForPassiveExecutionEngine()

    def test_short_sale_floor_applies_only_when_the_price_test_is_active(self):
        restricted = self.engine.calculate_pegged_price(
            PegOrder("R1", "AAPL", "SELL", "MARKET", is_short_sale=True),
            quote(short_sale_restricted=True),
        )
        self.assertEqual(restricted.effective_limit_price, D("100.01"))
        self.assertIn("SHORT_SALE_201", restricted.clamps)
        self.assertEqual(restricted.binding_constraint, "SHORT_SALE_201")
        self.assertGreater(restricted.effective_limit_price, D("100.00"))

        unrestricted = self.engine.calculate_pegged_price(
            PegOrder("R2", "AAPL", "SELL", "MARKET", is_short_sale=True), quote()
        )
        self.assertNotIn("SHORT_SALE_201", unrestricted.clamps)

    def test_long_sale_is_unaffected_by_an_active_price_test(self):
        report = self.engine.calculate_pegged_price(
            PegOrder("R3", "AAPL", "SELL", "MARKET", is_short_sale=False),
            quote(short_sale_restricted=True),
        )
        self.assertNotIn("SHORT_SALE_201", report.clamps)

    def test_short_sale_floor_survives_a_conflicting_limit_cap(self):
        # The cap is a floor on a sell, so the tightest floor wins and the
        # order can never be priced at or below the NBB.
        report = self.engine.calculate_pegged_price(
            PegOrder(
                "R4", "AAPL", "SELL", "MARKET", limit_cap=D("99.00"), is_short_sale=True
            ),
            quote(short_sale_restricted=True),
        )
        self.assertEqual(report.effective_limit_price, D("100.01"))

    def test_buy_is_repriced_to_the_luld_upper_band(self):
        report = self.engine.calculate_pegged_price(
            PegOrder("R5", "AAPL", "BUY", "PRIMARY", offset=D("0.05")),
            quote(luld_upper_band=D("100.02")),
        )
        self.assertEqual(report.calculated_price, D("100.05"))
        self.assertEqual(report.effective_limit_price, D("100.02"))
        self.assertEqual(report.binding_constraint, "LULD_BAND")

    def test_sell_is_repriced_to_the_luld_lower_band(self):
        # A compressed band can sit inside the spread; it is then tighter than
        # the passivity floor (bid + 1 tick = 100.01) and becomes binding.
        report = self.engine.calculate_pegged_price(
            PegOrder("R6", "AAPL", "SELL", "PRIMARY", offset=D("0.09")),
            quote(luld_lower_band=D("100.05")),
        )
        self.assertEqual(report.calculated_price, D("100.01"))
        self.assertEqual(report.effective_limit_price, D("100.05"))
        self.assertEqual(report.binding_constraint, "LULD_BAND")

    def test_band_on_the_far_side_is_ignored(self):
        report = self.engine.calculate_pegged_price(
            PegOrder("R7", "AAPL", "BUY", "PRIMARY"),
            quote(luld_lower_band=D("100.05")),
        )
        self.assertEqual(report.effective_limit_price, D("100.00"))
        self.assertEqual(report.clamps, ())

    def test_tied_bounds_attribute_the_clamp_to_the_regulatory_rule(self):
        # Passivity floor (bid + 1 tick) and the Rule 201 floor coincide here.
        report = self.engine.calculate_pegged_price(
            PegOrder("R8", "AAPL", "SELL", "MARKET", is_short_sale=True),
            quote(short_sale_restricted=True),
        )
        self.assertEqual(report.binding_constraint, "SHORT_SALE_201")
        self.assertEqual(report.clamps, ("SHORT_SALE_201", "PASSIVITY"))


class TestSuspension(unittest.TestCase):
    """Adverse market state yields a report with no price, never an exception
    and never a fabricated price."""

    def setUp(self):
        self.engine = PegOrderTypesForPassiveExecutionEngine()
        self.order = PegOrder("X1", "AAPL", "BUY", "MIDPOINT")

    def _assert_suspended(self, nbbo, reason):
        report = self.engine.calculate_pegged_price(self.order, nbbo)
        self.assertEqual(report.status, PegStatus.SUSPENDED)
        self.assertEqual(report.suspend_reason, reason)
        self.assertIsNone(report.effective_limit_price)
        return report

    def test_symbol_mismatch_is_refused(self):
        self._assert_suspended(NBBOQuote("MSFT", D("1"), D("2")), SuspendReason.SYMBOL_MISMATCH)

    def test_nan_quote_does_not_propagate_into_the_price(self):
        # Regression: NaN passes a naive `bid <= 0` check and yields a NaN price.
        self._assert_suspended(
            NBBOQuote("AAPL", float("nan"), D("100.10")), SuspendReason.NON_FINITE_QUOTE
        )

    def test_infinite_quote_is_refused(self):
        self._assert_suspended(
            NBBOQuote("AAPL", float("inf"), D("100.10")), SuspendReason.NON_FINITE_QUOTE
        )

    def test_non_positive_quote_is_refused(self):
        self._assert_suspended(NBBOQuote("AAPL", D("0"), D("100.10")), SuspendReason.NON_POSITIVE_QUOTE)

    def test_crossed_market_suspends_rather_than_prices(self):
        self._assert_suspended(quote("100.20", "100.10"), SuspendReason.CROSSED_MARKET)

    def test_constraints_resolving_below_a_tick_are_unpriceable(self):
        report = self.engine.calculate_pegged_price(
            PegOrder("X2", "PENNY", "BUY", "MARKET"),
            NBBOQuote("PENNY", D("0.005"), D("0.01"), tick_size=D("0.01")),
        )
        self.assertEqual(report.status, PegStatus.SUSPENDED)
        self.assertEqual(report.suspend_reason, SuspendReason.UNPRICEABLE)
        self.assertIsNone(report.effective_limit_price)


class TestSpecValidation(unittest.TestCase):
    """Caller-side mistakes raise; they are never silently reinterpreted."""

    def test_unknown_side_is_rejected(self):
        # Regression: a naive `if side == "BUY" else ...` treats "B" as a SELL.
        with self.assertRaises(PegSpecError):
            PegOrder("V1", "AAPL", "B", "PRIMARY")

    def test_unknown_peg_type_is_rejected(self):
        with self.assertRaises(PegSpecError):
            PegOrder("V2", "AAPL", "BUY", "LAST")

    def test_side_and_peg_type_are_case_insensitive(self):
        order = PegOrder("V3", "AAPL", "buy", "midpoint")
        self.assertEqual(order.side, Side.BUY)
        self.assertEqual(order.peg_type, PegType.MIDPOINT)

    def test_non_positive_quantity_is_rejected(self):
        for bad in (D("0"), D("-1")):
            with self.assertRaises(PegSpecError):
                PegOrder("V4", "AAPL", "BUY", "PRIMARY", quantity=bad)

    def test_non_finite_offset_is_rejected(self):
        for bad in (float("nan"), float("inf")):
            with self.assertRaises(PegSpecError):
                PegOrder("V5", "AAPL", "BUY", "PRIMARY", offset=bad)

    def test_non_positive_limit_cap_is_rejected(self):
        with self.assertRaises(PegSpecError):
            PegOrder("V6", "AAPL", "BUY", "PRIMARY", limit_cap=D("0"))

    def test_short_sale_flag_on_a_buy_is_rejected(self):
        with self.assertRaises(PegSpecError):
            PegOrder("V7", "AAPL", "BUY", "PRIMARY", is_short_sale=True)

    def test_blank_identifiers_are_rejected(self):
        with self.assertRaises(PegSpecError):
            PegOrder("  ", "AAPL", "BUY", "PRIMARY")
        with self.assertRaises(PegSpecError):
            PegOrder("V8", "", "BUY", "PRIMARY")

    def test_non_positive_tick_size_is_rejected(self):
        with self.assertRaises(PegSpecError):
            NBBOQuote("AAPL", D("1"), D("2"), tick_size=D("0"))

    def test_inverted_luld_bands_are_rejected(self):
        with self.assertRaises(PegSpecError):
            NBBOQuote("AAPL", D("1"), D("2"), luld_upper_band=D("1.5"), luld_lower_band=D("1.9"))

    def test_config_rejects_a_sub_one_reprice_threshold(self):
        with self.assertRaises(PegSpecError):
            PegPricingConfig(reprice_threshold_ticks=0)

    def test_config_rejects_a_non_positive_default_tick(self):
        with self.assertRaises(PegSpecError):
            PegPricingConfig(default_tick_size=D("-0.01"))

    def test_wrong_argument_types_are_rejected(self):
        engine = PegOrderTypesForPassiveExecutionEngine()
        with self.assertRaises(PegSpecError):
            engine.calculate_pegged_price("not-an-order", quote())
        with self.assertRaises(PegSpecError):
            engine.calculate_pegged_price(PegOrder("V9", "AAPL", "BUY", "PRIMARY"), {"bid": 1})


class TestRepriceDecision(unittest.TestCase):
    def setUp(self):
        self.engine = PegOrderTypesForPassiveExecutionEngine()
        self.report = self.engine.calculate_pegged_price(
            PegOrder("D1", "AAPL", "BUY", "PRIMARY"), quote()
        )
        self.assertEqual(self.report.effective_limit_price, D("100.00"))

    def test_sub_tick_drift_does_not_trigger_a_replace(self):
        decision = self.engine.should_reprice(D("100.00"), self.report)
        self.assertFalse(decision.should_reprice)
        self.assertEqual(decision.reason, "BELOW_THRESHOLD")
        self.assertEqual(decision.delta_ticks, D("0"))

    def test_exactly_one_tick_meets_the_default_threshold(self):
        decision = self.engine.should_reprice(D("99.99"), self.report)
        self.assertTrue(decision.should_reprice)
        self.assertEqual(decision.delta_ticks, D("1"))

    def test_threshold_can_be_raised_to_damp_message_rate(self):
        decision = self.engine.should_reprice(D("99.98"), self.report, threshold_ticks=3)
        self.assertFalse(decision.should_reprice)
        self.assertEqual(decision.delta_ticks, D("2"))

    def test_absent_active_order_always_warrants_submission(self):
        decision = self.engine.should_reprice(None, self.report)
        self.assertTrue(decision.should_reprice)
        self.assertEqual(decision.reason, "NO_ACTIVE_ORDER")

    def test_a_suspended_report_never_authorises_a_replace(self):
        suspended = self.engine.calculate_pegged_price(
            PegOrder("D2", "AAPL", "BUY", "PRIMARY"), quote("100.20", "100.10")
        )
        decision = self.engine.should_reprice(D("100.00"), suspended)
        self.assertFalse(decision.should_reprice)
        self.assertEqual(decision.reason, "NO_VALID_PRICE")

    def test_invalid_threshold_is_rejected(self):
        for bad in (0, -1, 1.5, True):
            with self.assertRaises(PegSpecError):
                self.engine.should_reprice(D("100.00"), self.report, threshold_ticks=bad)


class TestDeterminism(unittest.TestCase):
    def test_repeated_evaluation_is_identical_and_carries_no_state(self):
        engine = PegOrderTypesForPassiveExecutionEngine()
        order = PegOrder("Z1", "AAPL", "BUY", "MIDPOINT", offset=D("0.01"))
        first = engine.calculate_pegged_price(order, quote())
        engine.calculate_pegged_price(PegOrder("Z2", "AAPL", "SELL", "MARKET"), quote())
        second = engine.calculate_pegged_price(order, quote())
        self.assertEqual(first.effective_limit_price, second.effective_limit_price)
        self.assertEqual(first.clamps, second.clamps)

    def test_evaluation_does_not_mutate_its_inputs(self):
        engine = PegOrderTypesForPassiveExecutionEngine()
        order = PegOrder("Z3", "AAPL", "BUY", "PRIMARY", offset=D("0.01"))
        nbbo = quote()
        engine.calculate_pegged_price(order, nbbo)
        self.assertEqual(order.offset, D("0.01"))
        self.assertEqual(nbbo.best_bid, D("100.00"))


if __name__ == "__main__":
    unittest.main()
