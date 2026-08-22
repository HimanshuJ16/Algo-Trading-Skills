"""
Tests for the CME Globex pre-trade validation gate.

Expected prices are derived by hand from the CME rules, not by re-running the
module's own arithmetic:

  - a Market-with-Protection buy limit is best offer + protection points,
  - price banding constrains buys only above BRP + PBV and sells only below
    BRP - PBV,
  - tick conformance is exact divisibility, checked here against integer tick
    counts (5000.10 / 0.05 = 100002) rather than float modulo.

The contract parameters below are illustrative. Real tick, Price Band Variation
and protection point values come from CME's product reference files and are not
inferable from each other.
"""
import logging
import unittest

from cme_globex_futures_api_integration import (
    OPERATOR_ID_MAX_LEN,
    OPERATOR_ID_MIN_LEN,
    CmeGlobexOrderEngine,
    CmeOrder,
    CmeOrderValidationError,
    ContractSpec,
    ContractSpecError,
    ManualOrderIndicatorError,
    OperatorIdError,
    PriceBandingError,
    TickConformanceError,
    is_on_tick,
    round_toward_market,
)

REF = 5000.00
PBV = 12.00
PROTECTION = 6.00


def make_order(**overrides):
    kwargs = dict(
        symbol="ES",
        side="BUY",
        quantity=5,
        order_type="LIMIT",
        operator_id="ALGO_TEAM_1",
        account="ACCT1",
        price=5000.00,
        manual_order_indicator=False,
    )
    kwargs.update(overrides)
    return CmeOrder(**kwargs)


class EngineTestCase(unittest.TestCase):
    def setUp(self):
        self.spec = ContractSpec(
            symbol="ES", tick_size=0.25, price_band_points=PBV, protection_points=PROTECTION)
        self.engine = CmeGlobexOrderEngine(contract_specs={"ES": self.spec})


class TestTickArithmetic(unittest.TestCase):
    """Divisibility must survive binary floating point."""

    def test_on_tick_prices_that_float_modulo_gets_wrong(self):
        # 5000.10 / 0.05 = 100002 exactly. float modulo returns ~0.0499999.
        self.assertTrue(is_on_tick(5000.10, 0.05))
        self.assertTrue(is_on_tick(0.3, 0.1))          # 0.3 % 0.1 == 0.0999... in float
        self.assertTrue(is_on_tick(5000.25, 0.25))

    def test_off_tick_prices_rejected(self):
        self.assertFalse(is_on_tick(5000.12, 0.05))    # 100002.4 ticks
        self.assertFalse(is_on_tick(5000.10, 0.25))    # 20000.4 ticks

    def test_rounding_is_toward_the_market(self):
        # BUY protection limits round down: 5000.13 / 0.25 = 20000.52 -> 20000 ticks.
        self.assertEqual(round_toward_market(5000.13, 0.25, "BUY"), 5000.00)
        # SELL protection limits round up: 4999.87 / 0.25 = 19999.48 -> 20000 ticks.
        self.assertEqual(round_toward_market(4999.87, 0.25, "SELL"), 5000.00)

    def test_already_on_tick_prices_are_unchanged(self):
        self.assertEqual(round_toward_market(5000.25, 0.25, "BUY"), 5000.25)
        self.assertEqual(round_toward_market(5000.25, 0.25, "SELL"), 5000.25)


class TestContractSpec(unittest.TestCase):
    def test_non_positive_tick_size_rejected(self):
        for bad in (0, -0.25, float("nan"), float("inf")):
            with self.assertRaises(ContractSpecError):
                ContractSpec(symbol="ES", tick_size=bad, price_band_points=12.0, protection_points=6.0)

    def test_negative_band_or_protection_rejected(self):
        with self.assertRaises(ContractSpecError):
            ContractSpec(symbol="ES", tick_size=0.25, price_band_points=-1.0, protection_points=6.0)
        with self.assertRaises(ContractSpecError):
            ContractSpec(symbol="ES", tick_size=0.25, price_band_points=12.0, protection_points=-6.0)

    def test_empty_symbol_rejected(self):
        with self.assertRaises(ContractSpecError):
            ContractSpec(symbol="", tick_size=0.25, price_band_points=12.0, protection_points=6.0)


class TestOperatorIdRule576(EngineTestCase):
    def test_length_boundaries(self):
        self.assertFalse(self.engine.validate_operator_id("A" * (OPERATOR_ID_MIN_LEN - 1)))
        self.assertTrue(self.engine.validate_operator_id("A" * OPERATOR_ID_MIN_LEN))
        self.assertTrue(self.engine.validate_operator_id("A" * OPERATOR_ID_MAX_LEN))
        self.assertFalse(self.engine.validate_operator_id("A" * (OPERATOR_ID_MAX_LEN + 1)))

    def test_empty_and_non_string_rejected(self):
        self.assertFalse(self.engine.validate_operator_id(""))
        self.assertFalse(self.engine.validate_operator_id(None))
        self.assertFalse(self.engine.validate_operator_id(12345))

    def test_permitted_symbols_accepted(self):
        for oid in ("OP_123", "OP-123", "OP:123", "OP@123", "DESK_ALGO_01"):
            self.assertTrue(self.engine.validate_operator_id(oid), oid)

    def test_disallowed_characters_rejected(self):
        # Whitespace never matches an EFS registration; '#' and '/' are outside
        # the permitted symbol list; non-ASCII is not an alphanumeric CME accepts.
        for oid in ("OP 123", " OP123", "OP123 ", "OP#123", "OP/123", "OPé12"):
            self.assertFalse(self.engine.validate_operator_id(oid), oid)

    def test_permitted_symbol_set_is_configurable(self):
        strict = CmeGlobexOrderEngine(
            contract_specs={"ES": self.spec}, permitted_operator_id_symbols="")
        self.assertTrue(strict.validate_operator_id("OP123"))
        self.assertFalse(strict.validate_operator_id("OP_123"))

    def test_invalid_operator_id_blocks_submission(self):
        with self.assertRaises(OperatorIdError) as ctx:
            self.engine.process_order(
                make_order(operator_id="X"), "ORD1", reference_price=REF)
        self.assertIn("Rule 576 Violation", str(ctx.exception))

    def test_whitespace_padded_operator_id_blocks_submission(self):
        # A padded ID does not exactly match the registered ID, so it must not be
        # silently trimmed and sent.
        with self.assertRaises(OperatorIdError):
            self.engine.process_order(
                make_order(operator_id=" ALGO_TEAM_1 "), "ORD1", reference_price=REF)

    def test_valid_operator_id_is_transmitted_verbatim(self):
        msg = self.engine.process_order(make_order(operator_id="Desk_01"), "ORD1", reference_price=REF)
        self.assertEqual(msg.operator_id, "Desk_01")


class TestManualOrderIndicatorTag1028(EngineTestCase):
    def test_missing_indicator_is_rejected(self):
        with self.assertRaises(ManualOrderIndicatorError) as ctx:
            self.engine.process_order(
                make_order(manual_order_indicator=None), "ORD1", reference_price=REF)
        self.assertIn("1028", str(ctx.exception))

    def test_indicator_reaches_the_message(self):
        for manual in (True, False):
            msg = self.engine.process_order(
                make_order(manual_order_indicator=manual), "ORD1", reference_price=REF)
            self.assertIs(msg.manual_order_indicator, manual)

    def test_team_operator_id_may_not_send_a_manual_order(self):
        engine = CmeGlobexOrderEngine(
            contract_specs={"ES": self.spec}, team_operator_ids=["ALGO_TEAM_1"])
        with self.assertRaises(ManualOrderIndicatorError):
            engine.process_order(
                make_order(manual_order_indicator=True), "ORD1", reference_price=REF)

    def test_team_operator_id_may_send_an_automated_order(self):
        engine = CmeGlobexOrderEngine(
            contract_specs={"ES": self.spec}, team_operator_ids=["ALGO_TEAM_1"])
        msg = engine.process_order(
            make_order(manual_order_indicator=False), "ORD1", reference_price=REF)
        self.assertFalse(msg.manual_order_indicator)

    def test_team_matching_is_case_insensitive(self):
        # CME Operator IDs are not case sensitive, so 'algo_team_1' is the same
        # registration as 'ALGO_TEAM_1' and must not slip past the check.
        engine = CmeGlobexOrderEngine(
            contract_specs={"ES": self.spec}, team_operator_ids=["algo_team_1"])
        with self.assertRaises(ManualOrderIndicatorError):
            engine.process_order(
                make_order(manual_order_indicator=True), "ORD1", reference_price=REF)

    def test_check_is_skipped_when_no_registrations_supplied(self):
        msg = self.engine.process_order(
            make_order(manual_order_indicator=True), "ORD1", reference_price=REF)
        self.assertTrue(msg.manual_order_indicator)


class TestPriceBanding(EngineTestCase):
    """CME rejects buys above BRP + PBV and sells below BRP - PBV, and nothing else."""

    def test_buy_above_the_band_is_rejected(self):
        with self.assertRaises(PriceBandingError) as ctx:
            self.engine.process_order(
                make_order(side="BUY", price=REF + PBV + 0.25), "ORD1", reference_price=REF)
        self.assertIn("Price Banding Violation", str(ctx.exception))

    def test_sell_below_the_band_is_rejected(self):
        with self.assertRaises(PriceBandingError):
            self.engine.process_order(
                make_order(side="SELL", price=REF - PBV - 0.25), "ORD1", reference_price=REF)

    def test_buy_far_below_the_band_is_accepted(self):
        # Regression: a deep passive bid is an ordinary resting order. A
        # two-sided band check rejects it; CME does not.
        msg = self.engine.process_order(
            make_order(side="BUY", price=REF - 500.00), "ORD1", reference_price=REF)
        self.assertEqual(msg.price, REF - 500.00)

    def test_sell_far_above_the_band_is_accepted(self):
        msg = self.engine.process_order(
            make_order(side="SELL", price=REF + 500.00), "ORD1", reference_price=REF)
        self.assertEqual(msg.price, REF + 500.00)

    def test_exact_band_edge_is_accepted(self):
        # PBVR is inclusive of its edge: BRP + PBV = 5012.00 is on-tick and inside.
        msg = self.engine.process_order(
            make_order(side="BUY", price=REF + PBV), "ORD1", reference_price=REF)
        self.assertEqual(msg.price, REF + PBV)
        msg = self.engine.process_order(
            make_order(side="SELL", price=REF - PBV), "ORD1", reference_price=REF)
        self.assertEqual(msg.price, REF - PBV)

    def test_limit_order_requires_a_reference_price(self):
        with self.assertRaises(CmeOrderValidationError):
            self.engine.process_order(make_order(), "ORD1")

    def test_non_finite_reference_price_is_not_reported_as_a_band_breach(self):
        # NaN comparisons are always False, so a naive band check turns a data
        # fault into a misleading "Price Banding Violation".
        with self.assertRaises(CmeOrderValidationError) as ctx:
            self.engine.process_order(
                make_order(), "ORD1", reference_price=float("nan"))
        self.assertNotIsInstance(ctx.exception, PriceBandingError)


class TestTickConformance(EngineTestCase):
    def test_off_tick_limit_price_is_rejected(self):
        with self.assertRaises(TickConformanceError) as ctx:
            self.engine.process_order(make_order(price=5000.10), "ORD1", reference_price=REF)
        self.assertIn("minimum price increment", str(ctx.exception))

    def test_on_tick_limit_price_is_accepted(self):
        msg = self.engine.process_order(make_order(price=5000.25), "ORD1", reference_price=REF)
        self.assertEqual(msg.price, 5000.25)

    def test_fractional_tick_product_is_not_falsely_rejected(self):
        spec = ContractSpec(
            symbol="ZQ", tick_size=0.005, price_band_points=0.5, protection_points=0.02)
        engine = CmeGlobexOrderEngine(contract_specs={"ZQ": spec})
        msg = engine.process_order(
            make_order(symbol="ZQ", price=95.615), "ORD1", reference_price=95.60)
        self.assertEqual(msg.price, 95.615)


class TestMarketWithProtection(EngineTestCase):
    def test_buy_protection_limit_is_best_offer_plus_protection_points(self):
        msg = self.engine.process_order(
            make_order(side="BUY", order_type="MARKET", price=None),
            "ORD2", current_bid=4999.75, current_ask=5000.00, reference_price=REF)
        self.assertEqual(msg.protection_price_limit, 5006.00)  # 5000.00 + 6.00
        self.assertEqual(msg.ord_type, "MARKET")
        self.assertTrue(msg.is_mwp_converted)

    def test_sell_protection_limit_is_best_bid_minus_protection_points(self):
        msg = self.engine.process_order(
            make_order(side="SELL", order_type="MARKET", price=None),
            "ORD2", current_bid=4999.75, current_ask=5000.00, reference_price=REF)
        self.assertEqual(msg.protection_price_limit, 4993.75)  # 4999.75 - 6.00

    def test_market_order_carries_no_tag_44_price(self):
        # Encoding the protection limit as tag 44 would send a priced order.
        msg = self.engine.process_order(
            make_order(side="BUY", order_type="MARKET", price=None),
            "ORD2", current_bid=4999.75, current_ask=5000.00, reference_price=REF)
        self.assertIsNone(msg.price)

    def test_off_tick_protection_points_round_toward_the_market(self):
        spec = ContractSpec(
            symbol="ES", tick_size=0.25, price_band_points=PBV, protection_points=6.10)
        engine = CmeGlobexOrderEngine(contract_specs={"ES": spec})
        # BUY: 5000.00 + 6.10 = 5006.10 -> 20024.4 ticks -> 20024 ticks = 5006.00
        buy = engine.process_order(
            make_order(side="BUY", order_type="MARKET", price=None),
            "ORD2", current_ask=5000.00, reference_price=REF)
        self.assertEqual(buy.protection_price_limit, 5006.00)
        # SELL: 4999.75 - 6.10 = 4993.65 -> 19974.6 ticks -> 19975 ticks = 4993.75
        sell = engine.process_order(
            make_order(side="SELL", order_type="MARKET", price=None),
            "ORD2", current_bid=4999.75, reference_price=REF)
        self.assertEqual(sell.protection_price_limit, 4993.75)

    def test_market_buy_without_an_offer_is_rejected(self):
        with self.assertRaises(CmeOrderValidationError) as ctx:
            self.engine.process_order(
                make_order(side="BUY", order_type="MARKET", price=None),
                "ORD2", current_bid=4999.75, reference_price=REF)
        self.assertIn("current_ask", str(ctx.exception))

    def test_market_sell_without_a_bid_is_rejected(self):
        with self.assertRaises(CmeOrderValidationError) as ctx:
            self.engine.process_order(
                make_order(side="SELL", order_type="MARKET", price=None),
                "ORD2", current_ask=5000.00, reference_price=REF)
        self.assertIn("current_bid", str(ctx.exception))

    def test_protection_limit_outside_band_is_flagged_not_rejected(self):
        # Protection points wider than the PBV: the residual would rest outside
        # the band. Banding applies to price-based orders, so this is advisory.
        spec = ContractSpec(
            symbol="ES", tick_size=0.25, price_band_points=2.00, protection_points=6.00)
        engine = CmeGlobexOrderEngine(contract_specs={"ES": spec})
        with self.assertLogs("cme_globex_futures_api_integration", level=logging.WARNING):
            msg = engine.process_order(
                make_order(side="BUY", order_type="MARKET", price=None),
                "ORD2", current_ask=5000.00, reference_price=REF)
        self.assertTrue(msg.protection_limit_outside_band)
        self.assertEqual(msg.protection_price_limit, 5006.00)

    def test_market_order_without_reference_price_is_allowed(self):
        msg = self.engine.process_order(
            make_order(side="BUY", order_type="MARKET", price=None),
            "ORD2", current_ask=5000.00)
        self.assertFalse(msg.protection_limit_outside_band)

    def test_protection_points_exceeding_the_market_are_rejected(self):
        spec = ContractSpec(
            symbol="ES", tick_size=0.25, price_band_points=PBV, protection_points=10.00)
        engine = CmeGlobexOrderEngine(contract_specs={"ES": spec})
        with self.assertRaises(CmeOrderValidationError):
            engine.process_order(
                make_order(side="SELL", order_type="MARKET", price=None),
                "ORD2", current_bid=5.00, reference_price=5.00)

    def test_price_on_a_market_order_is_ignored_with_a_warning(self):
        with self.assertLogs("cme_globex_futures_api_integration", level=logging.WARNING) as logs:
            msg = self.engine.process_order(
                make_order(side="BUY", order_type="MARKET", price=4000.00),
                "ORD2", current_ask=5000.00, reference_price=REF)
        self.assertTrue(any("without tag 44" in line for line in logs.output))
        self.assertIsNone(msg.price)

    def test_crossed_book_warns_but_still_prices(self):
        with self.assertLogs("cme_globex_futures_api_integration", level=logging.WARNING) as logs:
            msg = self.engine.process_order(
                make_order(side="BUY", order_type="MARKET", price=None),
                "ORD2", current_bid=5001.00, current_ask=5000.00, reference_price=REF)
        self.assertTrue(any("Crossed top of book" in line for line in logs.output))
        self.assertEqual(msg.protection_price_limit, 5006.00)


class TestFieldValidation(EngineTestCase):
    def test_unknown_order_type_does_not_fall_through_to_limit(self):
        with self.assertRaises(CmeOrderValidationError) as ctx:
            self.engine.process_order(
                make_order(order_type="STOP", price=5000.00), "ORD1", reference_price=REF)
        self.assertIn("STOP", str(ctx.exception))

    def test_order_type_and_side_are_case_insensitive(self):
        msg = self.engine.process_order(
            make_order(side="buy", order_type="limit"), "ORD1", reference_price=REF)
        self.assertEqual(msg.side, "BUY")
        self.assertEqual(msg.ord_type, "LIMIT")

    def test_invalid_side_rejected(self):
        with self.assertRaises(CmeOrderValidationError):
            self.engine.process_order(make_order(side="SHORT"), "ORD1", reference_price=REF)

    def test_invalid_quantity_rejected(self):
        for bad in (0, -5, 2.5, True, "5"):
            with self.assertRaises(CmeOrderValidationError):
                self.engine.process_order(
                    make_order(quantity=bad), "ORD1", reference_price=REF)

    def test_limit_order_without_price_rejected(self):
        with self.assertRaises(CmeOrderValidationError):
            self.engine.process_order(make_order(price=None), "ORD1", reference_price=REF)

    def test_non_positive_limit_price_rejected(self):
        with self.assertRaises(CmeOrderValidationError):
            self.engine.process_order(make_order(price=0.0), "ORD1", reference_price=REF)

    def test_empty_account_rejected(self):
        with self.assertRaises(CmeOrderValidationError):
            self.engine.process_order(make_order(account=""), "ORD1", reference_price=REF)

    def test_empty_cl_ord_id_rejected(self):
        for bad in ("", "   ", None):
            with self.assertRaises(CmeOrderValidationError):
                self.engine.process_order(make_order(), bad, reference_price=REF)

    def test_unknown_symbol_raises_key_error(self):
        with self.assertRaises(KeyError):
            self.engine.process_order(make_order(symbol="NQ"), "ORD1", reference_price=REF)

    def test_message_carries_the_submitted_fields(self):
        msg = self.engine.process_order(
            make_order(quantity=7, account="ACCT9"), "ORD-42", reference_price=REF)
        self.assertEqual(msg.msg_type, "NewOrderSingle")
        self.assertEqual(msg.cl_ord_id, "ORD-42")
        self.assertEqual(msg.symbol, "ES")
        self.assertEqual(msg.order_qty, 7)
        self.assertEqual(msg.account, "ACCT9")
        self.assertFalse(msg.is_mwp_converted)
        self.assertIsNone(msg.protection_price_limit)


if __name__ == "__main__":
    unittest.main()
