import logging
import unittest
from decimal import Decimal

from ice_futures_us_eu_integration import (
    IceContractSpec,
    IceFuturesIntegrationEngine,
    IceOrderPayload,
    NCR_AUTO_CANCELLATION,
    NCR_EXCHANGE_DISCRETION,
    NCR_PRICE_ADJUSTMENT,
    NCR_UNKNOWN,
    NCR_WITHIN,
    QUARTERLY_MONTH_CODES,
    STATUS_INVALID_TICK_SIZE,
    STATUS_NO_ANCHOR_PRICE,
    STATUS_PASSED,
    STATUS_REASONABILITY_LIMIT_BREACH,
    default_catalog,
    to_decimal,
)


MODULE_LOGGER = "ice_futures_us_eu_integration"


def setUpModule():
    """Keep expected rejection warnings out of the test output.

    assertLogs installs its own handler and level, so the tests that assert on
    logging still work.
    """
    logging.getLogger(MODULE_LOGGER).setLevel(logging.CRITICAL)


def brent(**overrides):
    """A Brent Dec 2026 order, on tick and inside the reasonability limit."""
    kwargs = dict(
        root_symbol="B", month_code="Z", year=2026, side="BUY",
        price="75.50", quantity=10, anchor_price="75.40",
    )
    kwargs.update(overrides)
    return IceOrderPayload(**kwargs)


class TestSymbolFormatting(unittest.TestCase):

    def setUp(self):
        self.engine = IceFuturesIntegrationEngine()

    def test_display_code_and_maturity(self):
        self.assertEqual(self.engine.format_ice_symbol("B", "Z", 2026), ("BZ26", "202612"))
        self.assertEqual(self.engine.format_ice_symbol("SB", "V", 2026), ("SBV26", "202610"))

    def test_single_digit_year_pads_the_display_code(self):
        # 2005 -> '05', not '5'.
        self.assertEqual(self.engine.format_ice_symbol("B", "F", 2005), ("BF05", "200501"))

    def test_display_code_collides_across_brents_listed_curve(self):
        # Brent lists up to 156 consecutive months, so a two-digit year is not a
        # unique identifier. Tag 200 is what separates them.
        near, near_maturity = self.engine.format_ice_symbol("B", "Z", 2026)
        far, far_maturity = self.engine.format_ice_symbol("B", "Z", 2126)
        self.assertEqual(near, far)
        self.assertNotEqual(near_maturity, far_maturity)

    def test_two_digit_year_is_rejected_rather_than_producing_bad_tag_200(self):
        # The pre-fix behaviour returned ('BZ26', '2612') - a malformed
        # MaturityMonthYear that no gateway would accept.
        with self.assertRaises(ValueError):
            self.engine.format_ice_symbol("B", "Z", 26)

    def test_negative_and_non_integer_years_are_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.format_ice_symbol("B", "Z", -5)
        with self.assertRaises(TypeError):
            self.engine.format_ice_symbol("B", "Z", 2026.0)

    def test_unknown_month_code_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.format_ice_symbol("B", "A", 2026)

    def test_lowercase_input_is_normalised(self):
        self.assertEqual(self.engine.format_ice_symbol("b", "z", 2026), ("BZ26", "202612"))


class TestContractCatalog(unittest.TestCase):

    def setUp(self):
        self.engine = IceFuturesIntegrationEngine()

    def test_product_code_t_is_wti_not_dutch_ttf(self):
        # ICE publishes 'T' as ICE WTI Futures; Dutch TTF is 'TFN'. The pre-fix
        # catalog mapped 'T' to TTF, which routes an order to the wrong contract.
        self.assertEqual(self.engine.get_contract("T").name, "ICE WTI Crude Futures")
        self.assertEqual(self.engine.get_contract("T").currency, "USD")
        self.assertEqual(self.engine.get_contract("TFN").currency, "EUR")

    def test_mic_routing(self):
        self.assertEqual(self.engine.get_contract("B").operating_mic, "IFEU")
        self.assertEqual(self.engine.get_contract("TFN").operating_mic, "IFEU")
        self.assertEqual(self.engine.get_contract("SB").operating_mic, "IFUS")
        self.assertEqual(self.engine.get_contract("DX").operating_mic, "IFUS")

    def test_unknown_code_names_the_supported_set(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.get_contract("ZZZ")
        self.assertIn("Supported", str(ctx.exception))

    def test_catalog_is_copied_so_callers_cannot_mutate_engine_state(self):
        catalog = default_catalog()
        engine = IceFuturesIntegrationEngine(catalog)
        catalog.pop("B")
        self.assertEqual(engine.get_contract("B").product_contract_code, "B")

    def test_every_catalog_entry_carries_limit_provenance(self):
        for code in self.engine.supported_codes:
            spec = self.engine.get_contract(code)
            self.assertTrue(spec.limits_source, f"{code} has no limits_source")
            self.assertTrue(spec.limits_as_of, f"{code} has no limits_as_of")

    def test_spec_rejects_non_positive_limits(self):
        with self.assertRaises(ValueError):
            IceContractSpec(
                product_contract_code="X", ice_product_id=1, name="X", operating_mic="IFUS",
                currency="USD", price_unit="USD", tick_size=Decimal("0"),
                currency_per_price_unit=Decimal("1"), reasonability_limit=Decimal("1"),
                no_cancellation_range=Decimal("1"), listed_month_codes=QUARTERLY_MONTH_CODES,
                limits_source="test", limits_as_of="2026-08-25",
            )


class TestValuation(unittest.TestCase):

    def setUp(self):
        self.engine = IceFuturesIntegrationEngine()

    def test_brent_notional(self):
        # 1,000 barrels per lot at USD 75.50 = USD 75,500 per lot; 10 lots = 755,000.
        report = self.engine.process_and_route_order(brent())
        self.assertEqual(report.contract_value, Decimal("75500.00"))
        self.assertEqual(report.notional_value, Decimal("755000.00"))
        self.assertEqual(report.currency, "USD")
        # One cent per barrel on 1,000 barrels is USD 10 per tick.
        self.assertEqual(report.tick_value, Decimal("10.0000"))

    def test_sugar_notional_uses_the_exchange_cents_per_pound_quotation(self):
        # 22.50 cents/lb on 112,000 lb = USD 25,200 per lot; 5 lots = 126,000.
        report = self.engine.process_and_route_order(
            IceOrderPayload("SB", "V", 2026, "SELL", price="22.50", quantity=5,
                            anchor_price="22.50")
        )
        self.assertEqual(report.status, STATUS_PASSED)
        self.assertEqual(report.contract_value, Decimal("25200.0000"))
        self.assertEqual(report.notional_value, Decimal("126000.0000"))
        # 1/100 cent per lb on 112,000 lb is USD 11.20 per tick.
        self.assertEqual(report.tick_value, Decimal("11.200000"))

    def test_sugar_priced_in_dollars_per_pound_fails_the_tick_check(self):
        # 0.2250 is the dollars-per-pound form of 22.50 cents. Under the pre-fix
        # catalog it routed silently; against the exchange's own tick it is not a
        # whole number of 1/100-cent increments, so it is caught.
        report = self.engine.process_and_route_order(
            IceOrderPayload("SB", "V", 2026, "SELL", price="0.2250", quantity=5,
                            anchor_price="22.50")
        )
        self.assertEqual(report.status, STATUS_INVALID_TICK_SIZE)

    def test_dollar_index_notional_and_tick_value(self):
        # USD 1,000 x index; 0.005 index points is USD 5.00.
        report = self.engine.process_and_route_order(
            IceOrderPayload("DX", "Z", 2026, "BUY", price="98.500", quantity=1,
                            anchor_price="98.480")
        )
        self.assertEqual(report.contract_value, Decimal("98500.000"))
        self.assertEqual(report.tick_value, Decimal("5.000"))

    def test_ttf_requires_an_explicit_lot_size(self):
        # A TTF lot is 1 MW x the hours in the delivery period, so there is no
        # single multiplier to hard-code.
        with self.assertRaises(ValueError) as ctx:
            self.engine.process_and_route_order(
                IceOrderPayload("TFN", "Z", 2026, "BUY", price="35.000", quantity=1,
                                anchor_price="35.000")
            )
        self.assertIn("contract_size", str(ctx.exception))

    def test_ttf_lot_size_varies_with_the_delivery_month(self):
        # December 2026 is 31 days x 24 h = 744 MWh; November is 30 days = 720 MWh,
        # and the same price is worth proportionally less.
        december = self.engine.process_and_route_order(
            IceOrderPayload("TFN", "Z", 2026, "BUY", price="35.000", quantity=1,
                            anchor_price="35.000", contract_size=744)
        )
        november = self.engine.process_and_route_order(
            IceOrderPayload("TFN", "X", 2026, "BUY", price="35.000", quantity=1,
                            anchor_price="35.000", contract_size=720)
        )
        self.assertEqual(december.contract_value, Decimal("26040.000"))
        self.assertEqual(november.contract_value, Decimal("25200.000"))
        self.assertEqual(december.currency, "EUR")

    def test_notional_is_exact_where_float_arithmetic_is_not(self):
        # 0.1 + 0.2 style error: 3 lots at 10.10 on 1,000 barrels is exactly 30,300.
        report = self.engine.process_and_route_order(
            brent(price="10.10", quantity=3, anchor_price="10.10")
        )
        self.assertEqual(report.notional_value, Decimal("30300.00"))


class TestTickAlignment(unittest.TestCase):

    def setUp(self):
        self.engine = IceFuturesIntegrationEngine()

    def test_on_tick_price_passes(self):
        self.assertTrue(self.engine.process_and_route_order(brent()).is_price_tick_valid)

    def test_half_tick_price_is_rejected(self):
        report = self.engine.process_and_route_order(brent(price="75.505"))
        self.assertEqual(report.status, STATUS_INVALID_TICK_SIZE)
        self.assertFalse(report.is_price_tick_valid)

    def test_float_price_does_not_produce_a_spurious_tick_failure(self):
        # 75.505 / 0.01 is 7550.499999999999 in binary floating point; 75.50 / 0.01
        # is 7549.999999999999. Both must classify correctly.
        self.assertTrue(self.engine.process_and_route_order(brent(price=75.50)).is_price_tick_valid)
        self.assertFalse(self.engine.process_and_route_order(brent(price=75.505)).is_price_tick_valid)

    def test_negative_price_is_rejected_rather_than_passing_the_tick_check(self):
        # Decimal('-75.50') % Decimal('0.01') is zero, so positivity is its own check.
        self.assertEqual(Decimal("-75.50") % Decimal("0.01"), 0)
        with self.assertRaises(ValueError):
            self.engine.process_and_route_order(brent(price="-75.50"))

    def test_tick_failure_takes_precedence_over_the_reasonability_verdict(self):
        report = self.engine.process_and_route_order(brent(price="75.505", anchor_price="10.00"))
        self.assertEqual(report.status, STATUS_INVALID_TICK_SIZE)


class TestReasonabilityLimit(unittest.TestCase):
    """Brent RL is USD 0.75 from the anchor price, so 75.40 gives 74.65 - 76.15."""

    def setUp(self):
        self.engine = IceFuturesIntegrationEngine()

    def test_buy_at_the_upper_limit_is_accepted(self):
        report = self.engine.process_and_route_order(brent(price="76.15"))
        self.assertEqual(report.status, STATUS_PASSED)
        self.assertEqual(report.reasonability_upper, Decimal("76.15"))
        self.assertEqual(report.reasonability_lower, Decimal("74.65"))

    def test_buy_one_tick_above_the_upper_limit_is_refused(self):
        report = self.engine.process_and_route_order(brent(price="76.16"))
        self.assertEqual(report.status, STATUS_REASONABILITY_LIMIT_BREACH)
        self.assertFalse(report.passes_reasonability_limit)

    def test_deep_passive_buy_is_accepted(self):
        # The regression a symmetric abs(price - reference) check introduces: a bid
        # far below the market breaches no limit, and rejecting it would suppress
        # exactly the resting liquidity a market maker means to post.
        report = self.engine.process_and_route_order(brent(price="60.00"))
        self.assertEqual(report.status, STATUS_PASSED)
        self.assertEqual(report.distance_from_anchor, Decimal("15.40"))

    def test_sell_at_the_lower_limit_is_accepted(self):
        report = self.engine.process_and_route_order(brent(side="SELL", price="74.65"))
        self.assertEqual(report.status, STATUS_PASSED)

    def test_sell_one_tick_below_the_lower_limit_is_refused(self):
        report = self.engine.process_and_route_order(brent(side="SELL", price="74.64"))
        self.assertEqual(report.status, STATUS_REASONABILITY_LIMIT_BREACH)

    def test_far_offer_above_the_market_is_accepted(self):
        report = self.engine.process_and_route_order(brent(side="SELL", price="90.00"))
        self.assertEqual(report.status, STATUS_PASSED)

    def test_the_same_price_resolves_differently_by_side(self):
        # 76.16 is refused as a buy and accepted as a sell. A side-blind check
        # cannot express this.
        self.assertEqual(
            self.engine.process_and_route_order(brent(price="76.16")).status,
            STATUS_REASONABILITY_LIMIT_BREACH,
        )
        self.assertEqual(
            self.engine.process_and_route_order(brent(side="SELL", price="76.16")).status,
            STATUS_PASSED,
        )

    def test_missing_anchor_price_fails_closed(self):
        report = self.engine.process_and_route_order(brent(anchor_price=None))
        self.assertEqual(report.status, STATUS_NO_ANCHOR_PRICE)
        self.assertFalse(report.passes_reasonability_limit)
        self.assertFalse(report.ready_to_send)
        self.assertEqual(report.error_trade_exposure, NCR_UNKNOWN)
        self.assertIsNone(report.reasonability_upper)

    def test_non_positive_anchor_price_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.process_and_route_order(brent(anchor_price="0"))

    def test_limit_multiplier_widens_the_band(self):
        # Market Supervision may widen these levels without notice; 76.16 is
        # outside the published band and inside a doubled one.
        self.assertEqual(
            self.engine.process_and_route_order(brent(price="76.16")).status,
            STATUS_REASONABILITY_LIMIT_BREACH,
        )
        report = self.engine.process_and_route_order(brent(price="76.16", limit_multiplier=2))
        self.assertEqual(report.status, STATUS_PASSED)
        self.assertEqual(report.reasonability_upper, Decimal("76.90"))

    def test_non_positive_limit_multiplier_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.process_and_route_order(brent(limit_multiplier=0))


class TestErrorTradeExposure(unittest.TestCase):
    """Brent NCR is USD 0.50 from the anchor; beyond 3 x NCR is automatic cancellation."""

    def setUp(self):
        self.engine = IceFuturesIntegrationEngine()

    def test_inside_the_ncr_the_trade_stands(self):
        report = self.engine.process_and_route_order(brent(price="75.50", anchor_price="75.40"))
        self.assertEqual(report.error_trade_exposure, NCR_WITHIN)

    def test_exactly_at_the_ncr_boundary_still_stands(self):
        report = self.engine.process_and_route_order(brent(price="75.90", anchor_price="75.40"))
        self.assertEqual(report.distance_from_anchor, Decimal("0.50"))
        self.assertEqual(report.error_trade_exposure, NCR_WITHIN)

    def test_outside_the_ncr_is_the_price_adjustment_zone(self):
        # 0.60 from the anchor: past the 0.50 NCR, inside 3 x NCR. Still accepted
        # by the reasonability limit, so exposure is reported alongside a pass.
        report = self.engine.process_and_route_order(brent(price="74.80", anchor_price="75.40"))
        self.assertEqual(report.status, STATUS_PASSED)
        self.assertEqual(report.error_trade_exposure, NCR_PRICE_ADJUSTMENT)

    def test_beyond_three_times_the_ncr_is_the_auto_cancellation_zone(self):
        report = self.engine.process_and_route_order(brent(price="60.00", anchor_price="75.40"))
        self.assertEqual(report.error_trade_exposure, NCR_AUTO_CANCELLATION)

    def test_ifus_futures_report_exchange_discretion_not_an_invented_multiple(self):
        # ICE Futures U.S. states a 3 x NCR cancellation preference for options,
        # not for futures, so no multiple is asserted for SB.
        report = self.engine.process_and_route_order(
            IceOrderPayload("SB", "V", 2026, "BUY", price="22.00", quantity=1,
                            anchor_price="22.50")
        )
        self.assertEqual(report.error_trade_exposure, NCR_EXCHANGE_DISCRETION)
        self.assertIsNone(self.engine.get_contract("SB").auto_cancellation_ncr_multiple)


class TestContractSeries(unittest.TestCase):

    def setUp(self):
        self.engine = IceFuturesIntegrationEngine()

    def test_dollar_index_lists_only_the_quarterly_cycle(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.process_and_route_order(
                IceOrderPayload("DX", "F", 2026, "BUY", price="98.500", quantity=1,
                                anchor_price="98.500")
            )
        self.assertIn("does not list delivery month", str(ctx.exception))

    def test_sugar_lists_march_may_july_october_only(self):
        for code in ("H", "K", "N", "V"):
            payload = IceOrderPayload("SB", code, 2026, "BUY", price="22.50", quantity=1,
                                      anchor_price="22.50")
            self.assertEqual(self.engine.process_and_route_order(payload).status, STATUS_PASSED)
        with self.assertRaises(ValueError):
            self.engine.process_and_route_order(
                IceOrderPayload("SB", "Z", 2026, "BUY", price="22.50", quantity=1,
                                anchor_price="22.50")
            )

    def test_brent_lists_every_month(self):
        for code in "FGHJKMNQUVXZ":
            payload = brent(month_code=code)
            self.assertEqual(self.engine.process_and_route_order(payload).status, STATUS_PASSED)


class TestOrderValidation(unittest.TestCase):

    def setUp(self):
        self.engine = IceFuturesIntegrationEngine()

    def test_non_positive_quantity_is_rejected(self):
        # The pre-fix engine approved quantity=-10 and reported a negative notional.
        for qty in (0, -10):
            with self.assertRaises(ValueError):
                self.engine.process_and_route_order(brent(quantity=qty))

    def test_non_integer_quantity_is_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.process_and_route_order(brent(quantity=1.5))
        with self.assertRaises(TypeError):
            self.engine.process_and_route_order(brent(quantity=True))

    def test_unknown_side_is_rejected(self):
        # side is load-bearing: the reasonability check is directional.
        with self.assertRaises(ValueError):
            self.engine.process_and_route_order(brent(side="BANANA"))

    def test_side_is_emitted_as_the_fix_tag_54_enum(self):
        self.assertEqual(self.engine.process_and_route_order(brent()).fix_tag_54_side, "1")
        self.assertEqual(
            self.engine.process_and_route_order(brent(side="sell")).fix_tag_54_side, "2"
        )

    def test_non_numeric_price_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.process_and_route_order(brent(price="abc"))

    def test_nan_and_infinity_are_rejected(self):
        for bad in ("NaN", "Infinity", float("inf")):
            with self.assertRaises(ValueError):
                self.engine.process_and_route_order(brent(price=bad))

    def test_to_decimal_rejects_bool(self):
        with self.assertRaises(TypeError):
            to_decimal(True, "price")


class TestReportContents(unittest.TestCase):

    def setUp(self):
        self.engine = IceFuturesIntegrationEngine()

    def test_fix_tags_on_a_passing_brent_order(self):
        report = self.engine.process_and_route_order(brent())
        self.assertEqual(report.ice_display_code, "BZ26")
        self.assertEqual(report.fix_tag_55_symbol, "B")
        self.assertEqual(report.fix_tag_48_security_id, "254")
        self.assertEqual(report.fix_tag_207_security_exchange, "IFEU")
        self.assertEqual(report.fix_tag_200_maturity_month_year, "202612")
        self.assertTrue(report.ready_to_send)

    def test_ready_to_send_is_false_for_every_non_passing_status(self):
        for payload in (brent(price="75.505"), brent(price="76.16"), brent(anchor_price=None)):
            self.assertFalse(self.engine.process_and_route_order(payload).ready_to_send)

    def test_rejections_are_logged_at_warning_with_the_contract_in_the_message(self):
        with self.assertLogs(MODULE_LOGGER, level=logging.WARNING) as logs:
            self.engine.process_and_route_order(brent(price="76.16"))
        self.assertIn("BZ26", logs.output[0])
        self.assertIn("reasonability limit", logs.output[0])

    def test_report_is_immutable(self):
        report = self.engine.process_and_route_order(brent())
        with self.assertRaises(Exception):
            report.status = STATUS_PASSED


if __name__ == "__main__":
    unittest.main()
