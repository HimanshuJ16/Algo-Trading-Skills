"""
Unit and regression tests for CboeComplexOrderEngine.

Covers GCD ratio normalization and OrderQty scaling, FIX 35=AB long-form
serialization against the Cboe Titanium field list, Rule 5.33 stock-option
conformance (measured on the smallest option leg), venue-specific validation,
Execution Report parsing, and per-leg fill reconciliation.
"""

import datetime
import unittest
from decimal import Decimal

from cboe_complex_order_engine import (
    CboeComplexOrderEngine,
    CboeExchange,
    CboeValidationError,
    LegCFICode,
    LegPositionEffect,
    LegSide,
    MultilegReportingType,
    OptionLeg,
    OrdType,
    OrderCapacity,
    RoutingBookInst,
    RoutingCoaInst,
    TimeInForce,
    reconcile_leg_fills,
)

TS = datetime.datetime(2026, 8, 20, 14, 30, 0, 123000, tzinfo=datetime.timezone.utc)


def call_leg(symbol="SPY", ratio=1, side=LegSide.BUY, strike="400", **kwargs):
    """A conventional OSI-root option leg with all Cboe-required leg fields."""
    params = dict(
        symbol=symbol,
        ratio=ratio,
        side=side,
        cfi_code=LegCFICode.OPTION_CALL,
        maturity_date="20260918",
        strike_price=strike,
        position_effect=LegPositionEffect.OPEN,
    )
    params.update(kwargs)
    return OptionLeg(**params)


def engine(**kwargs):
    params = dict(
        cl_ord_id="CL12345",
        total_quantity=50,
        order_capacity=OrderCapacity.CUSTOMER,
        net_price="1.25",
        transact_time=TS,
    )
    params.update(kwargs)
    return CboeComplexOrderEngine(**params)


class TestRatioNormalization(unittest.TestCase):

    def test_gcd_reduction_scales_quantity(self):
        """10:20 reduces to 1:2 with OrderQty multiplied by GCD=10 (100 -> 1000)."""
        eng = engine(total_quantity=100)
        eng.add_leg(call_leg(ratio=10, strike="4800"))
        eng.add_leg(call_leg(ratio=20, side=LegSide.SELL, strike="4850"))

        qty, legs = eng._normalize_ratios()

        self.assertEqual(qty, 1000)
        self.assertEqual([leg.ratio for leg in legs], [1, 2])
        # Exposure is preserved exactly: 100 packages x 10 = 1000 x 1.
        self.assertEqual(100 * 10, qty * legs[0].ratio)
        self.assertEqual(100 * 20, qty * legs[1].ratio)

    def test_butterfly_reduces_to_1_2_1(self):
        eng = engine(total_quantity=5, net_price="0.85")
        eng.add_leg(call_leg(ratio=10, strike="470"))
        eng.add_leg(call_leg(ratio=20, side=LegSide.SELL, strike="475"))
        eng.add_leg(call_leg(ratio=10, strike="480"))

        qty, legs = eng._normalize_ratios()
        self.assertEqual(qty, 50)
        self.assertEqual([leg.ratio for leg in legs], [1, 2, 1])

    def test_coprime_ratios_are_not_inflated(self):
        eng = engine(total_quantity=25, net_price="0.50")
        eng.add_leg(call_leg(ratio=1, strike="400"))
        eng.add_leg(call_leg(ratio=3, side=LegSide.SELL, strike="410"))

        qty, legs = eng._normalize_ratios()
        self.assertEqual(qty, 25)
        self.assertEqual([leg.ratio for leg in legs], [1, 3])

    def test_scaled_quantity_over_cboe_maximum_is_rejected(self):
        """
        Regression: GCD scaling can push OrderQty (38) past the documented Cboe
        ceiling of 999,999 contracts. That must be a hard reject, not a silent
        oversized order.
        """
        eng = engine(total_quantity=200_000, net_price="1.00")
        eng.add_leg(call_leg(ratio=10, strike="400"))
        eng.add_leg(call_leg(ratio=20, side=LegSide.SELL, strike="410"))

        with self.assertRaises(CboeValidationError) as ctx:
            eng.validate()
        self.assertIn("999999", str(ctx.exception).replace(",", ""))

    def test_reduced_leg_ratios_keyed_by_leg_ref_id(self):
        eng = engine(total_quantity=10, net_price="1.00")
        eng.add_leg(call_leg(ratio=2, strike="400", leg_ref_id="A"))
        eng.add_leg(call_leg(ratio=4, side=LegSide.SELL, strike="410", leg_ref_id="B"))
        self.assertEqual(eng.reduced_leg_ratios(), {"A": 1, "B": 2})


class TestLegValidation(unittest.TestCase):

    def test_non_positive_ratio_rejected(self):
        for bad in (0, -5):
            with self.assertRaises(CboeValidationError):
                call_leg(ratio=bad)

    def test_ratio_above_cboe_maximum_rejected(self):
        with self.assertRaises(CboeValidationError):
            call_leg(ratio=1_000_000)

    def test_osi_root_requires_cfi_maturity_and_strike(self):
        """LegCFICode/LegMaturityDate/LegStrikePrice are required for an OSI root."""
        with self.assertRaises(CboeValidationError):
            OptionLeg(symbol="SPY", ratio=1, side=LegSide.BUY)
        with self.assertRaises(CboeValidationError):
            OptionLeg(symbol="SPY", ratio=1, side=LegSide.BUY,
                      cfi_code=LegCFICode.OPTION_CALL, strike_price="400")

    def test_cboe_format_symbol_does_not_require_osi_components(self):
        leg = OptionLeg(symbol="SPY 260918C00400000", ratio=1, side=LegSide.BUY)
        self.assertFalse(leg.symbol_is_osi_root)

    def test_short_sale_sides_are_stock_leg_only(self):
        """LegSide 5/6 are documented for the stock leg only."""
        with self.assertRaises(CboeValidationError):
            call_leg(side=LegSide.SELL_SHORT)

        equity = OptionLeg(symbol="TSLA", ratio=100, side=LegSide.SELL_SHORT,
                           cfi_code=LegCFICode.EQUITY,
                           position_effect=LegPositionEffect.OPEN)
        self.assertEqual(equity.side, "5")
        self.assertTrue(equity.is_equity_leg)
        # Equity legs count in shares, so the option multiplier must not apply.
        self.assertEqual(equity.multiplier, 1)
        self.assertEqual(equity.underlying_units, 100)

    def test_invalid_maturity_date_rejected(self):
        with self.assertRaises(CboeValidationError):
            call_leg(maturity_date="2026-09-18")
        with self.assertRaises(CboeValidationError):
            call_leg(maturity_date="20260931")   # September has 30 days

    def test_leg_ref_id_length_and_charset_enforced(self):
        with self.assertRaises(CboeValidationError):
            call_leg(leg_ref_id="LEG_A")     # underscore not allowed
        with self.assertRaises(CboeValidationError):
            call_leg(leg_ref_id="ABCDEF")    # six characters
        self.assertEqual(call_leg(leg_ref_id="LEGA").leg_ref_id, "LEGA")

    def test_strike_price_bounds(self):
        with self.assertRaises(CboeValidationError):
            call_leg(strike="1000000")
        with self.assertRaises(CboeValidationError):
            call_leg(strike="-1")


class TestOrderValidation(unittest.TestCase):

    def test_minimum_two_legs(self):
        eng = engine()
        with self.assertRaises(CboeValidationError):
            eng.build_fix_message()
        eng.add_leg(call_leg())
        with self.assertRaises(CboeValidationError) as ctx:
            eng.build_fix_message()
        self.assertIn("MsgType=D", str(ctx.exception))

    def test_sixteen_leg_ceiling(self):
        eng = engine()
        for i in range(16):
            eng.add_leg(call_leg(strike=str(400 + i)))
        with self.assertRaises(CboeValidationError):
            eng.add_leg(call_leg(strike="999"))

    def test_floor_routed_order_may_exceed_sixteen_legs(self):
        eng = engine(max_legs=100)
        for i in range(20):
            eng.add_leg(call_leg(strike=str(400 + i)))
        self.assertEqual(len(eng.legs), 20)

    def test_max_legs_out_of_range_rejected(self):
        with self.assertRaises(CboeValidationError):
            engine(max_legs=101)

    def test_cl_ord_id_constraints(self):
        with self.assertRaises(CboeValidationError):
            engine(cl_ord_id="")
        with self.assertRaises(CboeValidationError):
            engine(cl_ord_id="A" * 21)
        with self.assertRaises(CboeValidationError):
            engine(cl_ord_id="ORD|1")          # pipe is forbidden
        with self.assertRaises(CboeValidationError) as ctx:
            engine(cl_ord_id="~ORD1")          # reserved by Cboe
        self.assertIn("tilde", str(ctx.exception))

    def test_invalid_quantity_rejected(self):
        for bad in (0, -5):
            with self.assertRaises(CboeValidationError):
                engine(total_quantity=bad)

    def test_fok_time_in_force_is_not_a_cboe_value(self):
        """Regression: TimeInForce '4' (FOK) is not documented for this message."""
        with self.assertRaises(CboeValidationError):
            engine(time_in_force="4")

    def test_gtd_requires_expire_time(self):
        eng = engine(time_in_force=TimeInForce.GTD)
        eng.add_leg(call_leg())
        eng.add_leg(call_leg(side=LegSide.SELL, strike="410"))
        with self.assertRaises(CboeValidationError):
            eng.build_fix_message()

        eng.expire_time = TS + datetime.timedelta(days=1)
        self.assertIn("126=20260821-14:30:00.123|", eng.build_fix_message())

    def test_position_effect_required_unless_market_maker(self):
        eng = engine()
        eng.add_leg(call_leg(position_effect=None))
        eng.add_leg(call_leg(side=LegSide.SELL, strike="410"))
        with self.assertRaises(CboeValidationError) as ctx:
            eng.validate()
        self.assertIn("LegPositionEffect", str(ctx.exception))

        mm = engine(order_capacity=OrderCapacity.MARKET_MAKER)
        mm.add_leg(call_leg(position_effect=None))
        mm.add_leg(call_leg(side=LegSide.SELL, strike="410", position_effect=None))
        mm.validate()

    def test_duplicate_leg_ref_ids_rejected(self):
        """
        LegRefID (654) is the only key joining a 442=2 leg fill to a leg; duplicates
        would merge two legs' fills during reconciliation.
        """
        eng = engine()
        eng.add_leg(call_leg(strike="400", leg_ref_id="A"))
        eng.add_leg(call_leg(side=LegSide.SELL, strike="410", leg_ref_id="A"))
        with self.assertRaises(CboeValidationError) as ctx:
            eng.validate()
        self.assertIn("unique", str(ctx.exception))

    def test_explicit_leg_ref_id_colliding_with_auto_assignment_rejected(self):
        eng = engine()
        eng.add_leg(call_leg(strike="400", leg_ref_id="L2"))
        eng.add_leg(call_leg(side=LegSide.SELL, strike="410"))  # auto-assigned 'L2'
        with self.assertRaises(CboeValidationError):
            eng.validate()

    def test_naive_transact_time_rejected(self):
        eng = engine(transact_time=datetime.datetime(2026, 8, 20, 14, 30))
        eng.add_leg(call_leg())
        eng.add_leg(call_leg(side=LegSide.SELL, strike="410"))
        with self.assertRaises(CboeValidationError) as ctx:
            eng.build_fix_message()
        self.assertIn("timezone-aware", str(ctx.exception))

    def test_non_utc_transact_time_is_converted(self):
        eastern = datetime.timezone(datetime.timedelta(hours=-4))
        eng = engine(transact_time=TS.astimezone(eastern))
        eng.add_leg(call_leg())
        eng.add_leg(call_leg(side=LegSide.SELL, strike="410"))
        self.assertIn("60=20260820-14:30:00.123|", eng.build_fix_message())


class TestRoutingInst(unittest.TestCase):
    """
    COA participation is controlled by RoutingInst (9303), not ExecInst (18):
    2nd character 'S' exposes the order via COA, 'L' suppresses it.
    """

    def _two_leg(self, **kwargs):
        eng = engine(**kwargs)
        eng.add_leg(call_leg())
        eng.add_leg(call_leg(side=LegSide.SELL, strike="410"))
        return eng

    def test_coa_exposure_emits_routing_inst_bs(self):
        msg = self._two_leg(routing_coa_inst=RoutingCoaInst.EXPOSE_COA).build_fix_message()
        self.assertIn("9303=BS|", msg)
        self.assertNotIn("18=", msg)

    def test_coa_suppression_emits_routing_inst_bl(self):
        self.assertIn("9303=BL|", self._two_leg(
            routing_coa_inst=RoutingCoaInst.NO_COA).build_fix_message())

    def test_routing_inst_omitted_when_unset(self):
        self.assertNotIn("9303=", self._two_leg().build_fix_message())

    def test_post_only_coa_combination_rejected(self):
        eng = self._two_leg(routing_book_inst=RoutingBookInst.POST_ONLY,
                            routing_coa_inst=RoutingCoaInst.EXPOSE_COA)
        with self.assertRaises(CboeValidationError) as ctx:
            eng.validate()
        self.assertIn("'PS' is not supported", str(ctx.exception))

    def test_complex_book_only_requires_market_maker_and_day_or_ioc(self):
        eng = self._two_leg(routing_book_inst=RoutingBookInst.COMPLEX_BOOK_ONLY,
                            order_capacity=OrderCapacity.CUSTOMER)
        with self.assertRaises(CboeValidationError):
            eng.validate()

        eng = self._two_leg(routing_book_inst=RoutingBookInst.COMPLEX_BOOK_ONLY,
                            order_capacity=OrderCapacity.MARKET_MAKER,
                            time_in_force=TimeInForce.GTC)
        with self.assertRaises(CboeValidationError):
            eng.validate()


class TestSerialization(unittest.TestCase):

    def _debit_spread(self):
        eng = engine(account="ACCT99", routing_coa_inst=RoutingCoaInst.EXPOSE_COA)
        eng.add_leg(call_leg(strike="400", leg_ref_id="A"))
        eng.add_leg(call_leg(side=LegSide.SELL, strike="410", leg_ref_id="B"))
        return eng

    def test_envelope_and_leg_tags(self):
        msg = self._debit_spread().build_fix_message()

        self.assertTrue(msg.startswith("35=AB|"))
        self.assertIn("1=ACCT99|", msg)
        self.assertIn("11=CL12345|", msg)
        self.assertIn("60=20260820-14:30:00.123|", msg)
        self.assertIn("167=MLEG|", msg)      # required SecurityType
        self.assertIn("47=C|", msg)          # required OrderCapacity
        self.assertIn("555=2|", msg)
        self.assertIn("38=50|", msg)
        self.assertIn("40=2|", msg)
        self.assertIn("44=1.25|", msg)
        self.assertIn("59=0|", msg)

        for tag in ("654=A|600=SPY|608=OC|611=20260918|612=400|623=1|624=1|564=O|",
                    "654=B|600=SPY|608=OC|611=20260918|612=410|623=1|624=2|564=O|"):
            self.assertIn(tag, msg)

    def test_leg_group_starts_with_leg_ref_id(self):
        """
        Cboe: LegRefID (654) is the "required tag to start each repeated group".
        A group that opens on LegSymbol (600) is not a valid repeating group.
        """
        msg = self._debit_spread().build_fix_message()
        after_count = msg.split("555=2|", 1)[1]
        self.assertTrue(after_count.startswith("654="))

    def test_symbol_and_side_are_not_emitted_in_long_form(self):
        """
        Symbol (55) and Side (54) are short-form fields; Symbol (55) carries the COB
        strategy symbol, not the underlying root, so emitting the root would be wrong.
        """
        msg = engine(underlying_symbol="SPY")
        msg.add_leg(call_leg())
        msg.add_leg(call_leg(side=LegSide.SELL, strike="410"))
        wire = msg.build_fix_message()
        self.assertNotIn("|55=", wire)
        self.assertNotIn("|54=", wire)

    def test_leg_ref_ids_auto_assigned_and_within_length_limit(self):
        eng = engine()
        eng.add_leg(call_leg(strike="400"))
        eng.add_leg(call_leg(side=LegSide.SELL, strike="410"))
        msg = eng.build_fix_message()
        self.assertIn("654=L1|", msg)
        self.assertIn("654=L2|", msg)

    def test_credit_price_is_negative_in_long_form(self):
        eng = engine(net_price="-0.75")
        eng.add_leg(call_leg(side=LegSide.SELL, strike="380", cfi_code=LegCFICode.OPTION_PUT))
        eng.add_leg(call_leg(strike="375", cfi_code=LegCFICode.OPTION_PUT))
        self.assertIn("44=-0.75|", eng.build_fix_message())

    def test_even_money_spread_prices_at_zero(self):
        eng = engine(net_price="0.00")
        eng.add_leg(call_leg())
        eng.add_leg(call_leg(side=LegSide.SELL, strike="410"))
        self.assertIn("44=0|", eng.build_fix_message())

    def test_float_price_serializes_without_binary_artifacts(self):
        """Regression: 0.1 + 0.2 must not reach the wire as 0.30000000000000004."""
        eng = engine(net_price=0.1 + 0.2)
        eng.add_leg(call_leg())
        eng.add_leg(call_leg(side=LegSide.SELL, strike="410"))
        with self.assertRaises(CboeValidationError):
            eng.build_fix_message()

        eng = engine(net_price=Decimal("0.30"))
        eng.add_leg(call_leg())
        eng.add_leg(call_leg(side=LegSide.SELL, strike="410"))
        self.assertIn("44=0.3|", eng.build_fix_message())

    def test_sub_penny_price_rejected_for_option_only_spread(self):
        eng = engine(net_price="1.255")
        eng.add_leg(call_leg())
        eng.add_leg(call_leg(side=LegSide.SELL, strike="410"))
        with self.assertRaises(CboeValidationError) as ctx:
            eng.build_fix_message()
        self.assertIn("whole pennies", str(ctx.exception))

    def test_sub_penny_price_allowed_with_stock_leg(self):
        eng = engine(net_price="175.5025", exchange=CboeExchange.C1)
        eng.add_leg(OptionLeg(symbol="AAPL", ratio=100, side=LegSide.BUY,
                              cfi_code=LegCFICode.EQUITY,
                              position_effect=LegPositionEffect.OPEN))
        eng.add_leg(call_leg(symbol="AAPL", side=LegSide.SELL, strike="180"))
        self.assertIn("44=175.5025|", eng.build_fix_message())

    def test_limit_order_requires_price_and_market_order_forbids_it(self):
        eng = engine(net_price=None)
        eng.add_leg(call_leg())
        eng.add_leg(call_leg(side=LegSide.SELL, strike="410"))
        with self.assertRaises(CboeValidationError):
            eng.build_fix_message()

        mkt = engine(ord_type=OrdType.MARKET, net_price="1.25")
        mkt.add_leg(call_leg())
        mkt.add_leg(call_leg(side=LegSide.SELL, strike="410"))
        with self.assertRaises(CboeValidationError):
            mkt.build_fix_message()

        mkt = engine(ord_type=OrdType.MARKET, net_price=None)
        mkt.add_leg(call_leg())
        mkt.add_leg(call_leg(side=LegSide.SELL, strike="410"))
        wire = mkt.build_fix_message()
        self.assertIn("40=1|", wire)
        self.assertNotIn("|44=", wire)

    def test_soh_delimiter(self):
        eng = engine()
        eng.add_leg(call_leg())
        eng.add_leg(call_leg(side=LegSide.SELL, strike="410"))
        wire = eng.build_fix_message(delimiter="\x01")
        self.assertTrue(wire.startswith("35=AB\x01"))
        self.assertTrue(wire.endswith("\x01"))
        self.assertNotIn("|", wire)

    def test_empty_delimiter_rejected(self):
        eng = engine()
        eng.add_leg(call_leg())
        eng.add_leg(call_leg(side=LegSide.SELL, strike="410"))
        with self.assertRaises(CboeValidationError):
            eng.build_fix_message(delimiter="")


class TestStockOptionConformance(unittest.TestCase):
    """
    Cboe Rule 5.33: a stock-option order is conforming when the ratio of option
    underlying units to stock units is <= 8:1, measured on the SMALLEST option leg.
    """

    def _stock_option(self, option_ratios, stock_shares=100, exchange=CboeExchange.C1):
        eng = engine(total_quantity=1, net_price="10.00", exchange=exchange)
        eng.add_leg(OptionLeg(symbol="AAPL", ratio=stock_shares, side=LegSide.BUY,
                              cfi_code=LegCFICode.EQUITY,
                              position_effect=LegPositionEffect.OPEN))
        for i, ratio in enumerate(option_ratios):
            eng.add_leg(call_leg(symbol="AAPL", ratio=ratio, side=LegSide.SELL,
                                 strike=str(180 + i)))
        return eng

    def test_covered_call_one_to_one_conforms(self):
        self._stock_option([1])._validate_stock_option_ratio()

    def test_exactly_eight_to_one_conforms(self):
        """800 option underlying units against 100 shares is exactly at the limit."""
        self._stock_option([8])._validate_stock_option_ratio()

    def test_nine_to_one_is_non_conforming(self):
        with self.assertRaises(CboeValidationError) as ctx:
            self._stock_option([9])._validate_stock_option_ratio()
        self.assertIn("Rule 5.33", str(ctx.exception))

    def test_ratio_uses_smallest_option_leg_not_the_sum(self):
        """
        Regression: summing every option leg produced false rejections. A collar-style
        package of 100 shares plus a 1-contract and an 8-contract option leg has an
        aggregate of 900 underlying units but a smallest leg of 100, so it conforms.
        """
        eng = self._stock_option([1, 8], exchange=CboeExchange.C1)
        eng._validate_stock_option_ratio()

    def test_option_only_package_skips_the_stock_ratio_test(self):
        eng = engine()
        eng.add_leg(call_leg())
        eng.add_leg(call_leg(side=LegSide.SELL, strike="410"))
        eng._validate_stock_option_ratio()

    def test_second_equity_leg_rejected(self):
        eng = self._stock_option([1])
        with self.assertRaises(CboeValidationError):
            eng.add_leg(OptionLeg(symbol="AAPL", ratio=100, side=LegSide.SELL,
                                  cfi_code=LegCFICode.EQUITY,
                                  position_effect=LegPositionEffect.CLOSE))

    def test_equity_leg_rejected_on_c2(self):
        eng = engine(exchange=CboeExchange.C2)
        with self.assertRaises(CboeValidationError) as ctx:
            eng.add_leg(OptionLeg(symbol="AAPL", ratio=100, side=LegSide.BUY,
                                  cfi_code=LegCFICode.EQUITY,
                                  position_effect=LegPositionEffect.OPEN))
        self.assertIn("C1 and EDGX only", str(ctx.exception))


class TestVenueRatioSpread(unittest.TestCase):
    """C2 and EDGX cap the reduced smallest:largest leg ratio at 1:3."""

    def _ratio_spread(self, ratios, exchange):
        eng = engine(total_quantity=1, net_price="1.00", exchange=exchange)
        for i, ratio in enumerate(ratios):
            eng.add_leg(call_leg(ratio=ratio, strike=str(400 + i)))
        return eng

    def test_one_to_four_rejected_on_c2(self):
        with self.assertRaises(CboeValidationError) as ctx:
            self._ratio_spread([1, 4], CboeExchange.C2).validate()
        self.assertIn("1:3", str(ctx.exception))

    def test_one_to_three_accepted_on_edgx(self):
        self._ratio_spread([1, 3], CboeExchange.EDGX).validate()

    def test_one_to_four_accepted_on_c1(self):
        self._ratio_spread([1, 4], CboeExchange.C1).validate()

    def test_cap_is_applied_after_reduction(self):
        """2:8 reduces to 1:4 and must still be rejected on C2."""
        with self.assertRaises(CboeValidationError):
            self._ratio_spread([2, 8], CboeExchange.C2).validate()


class TestExecutionReportParsing(unittest.TestCase):

    PACKAGE = (
        "35=8|11=CL12345|37=EX_ORD_99|17=EXEC_001|150=2|39=2|55=SPY 260918C00400000|"
        "167=MLEG|442=3|31=1.25|32=50|14=50|151=0|6=1.25|"
        "555=2|654=A|600=SPY|608=OC|611=20260918|612=400|623=1|624=1|564=O|"
        "654=B|600=SPY|608=OC|611=20260918|612=410|623=1|624=2|564=O|10=123|"
    )
    LEG_A = ("35=8|11=CL12345|37=EX_ORD_99|17=EXEC_002|150=2|39=2|167=OPT|442=2|"
             "654=A|31=4.50|32=50|")
    LEG_B = ("35=8|11=CL12345|37=EX_ORD_99|17=EXEC_003|150=2|39=2|167=OPT|442=2|"
             "654=B|31=3.25|32=50|")

    def test_package_report_fields(self):
        report = CboeComplexOrderEngine.parse_fix_execution_report(self.PACKAGE)
        self.assertEqual(report.cl_ord_id, "CL12345")
        self.assertEqual(report.order_id, "EX_ORD_99")
        self.assertEqual(report.exec_type, "2")
        self.assertEqual(report.ord_status, "2")
        self.assertEqual(report.security_type, "MLEG")
        self.assertEqual(report.multileg_reporting_type,
                         MultilegReportingType.MULTILEG_INSTRUMENT.value)
        self.assertTrue(report.is_package_report)
        self.assertFalse(report.is_leg_report)
        self.assertEqual(report.last_shares, 50)
        self.assertEqual(report.last_px, Decimal("1.25"))
        self.assertEqual(report.cum_qty, 50)
        self.assertEqual(report.leaves_qty, 0)
        self.assertEqual(report.avg_px, Decimal("1.25"))

    def test_echoed_leg_group_parsed(self):
        report = CboeComplexOrderEngine.parse_fix_execution_report(self.PACKAGE)
        self.assertEqual(len(report.legs), 2)
        self.assertEqual(report.legs[0].leg_ref_id, "A")
        self.assertEqual(report.legs[0].leg_cfi_code, "OC")
        self.assertEqual(report.legs[0].leg_strike_price, Decimal("400"))
        self.assertEqual(report.legs[1].leg_side, "2")
        self.assertEqual(report.legs[1].leg_ratio_qty, 1)

    def test_tag_after_repeating_group_is_not_swallowed(self):
        """
        Regression: a trailing order-level tag (here CheckSum 10) placed after the
        NoLegs group must close the group rather than be absorbed into the last leg.
        """
        report = CboeComplexOrderEngine.parse_fix_execution_report(self.PACKAGE)
        self.assertEqual(report.raw_tags.get("10"), "123")
        self.assertEqual(len(report.legs), 2)

    def test_leg_report_carries_fill_at_top_level(self):
        """Cboe reports leg fills as separate 442=2 messages, not tags 637/638."""
        report = CboeComplexOrderEngine.parse_fix_execution_report(self.LEG_A)
        self.assertTrue(report.is_leg_report)
        self.assertEqual(report.leg_ref_id, "A")
        self.assertEqual(report.last_px, Decimal("4.50"))
        self.assertEqual(report.last_shares, 50)
        self.assertEqual(report.legs, [])

    def test_malformed_numeric_field_does_not_discard_the_report(self):
        broken = self.LEG_A.replace("32=50|", "32=fifty|")
        with self.assertLogs("cboe_complex_order_engine", level="WARNING"):
            report = CboeComplexOrderEngine.parse_fix_execution_report(broken)
        self.assertIsNone(report.last_shares)
        self.assertEqual(report.exec_id, "EXEC_002")
        self.assertEqual(report.raw_tags["32"], "fifty")

    def test_empty_message_rejected(self):
        with self.assertRaises(CboeValidationError):
            CboeComplexOrderEngine.parse_fix_execution_report("")

    def test_soh_delimited_report(self):
        report = CboeComplexOrderEngine.parse_fix_execution_report(
            self.PACKAGE.replace("|", "\x01"), delimiter="\x01")
        self.assertEqual(len(report.legs), 2)
        self.assertEqual(report.cl_ord_id, "CL12345")


class TestLegFillReconciliation(unittest.TestCase):

    def setUp(self):
        parse = CboeComplexOrderEngine.parse_fix_execution_report
        self.package = parse(TestExecutionReportParsing.PACKAGE)
        self.leg_a = parse(TestExecutionReportParsing.LEG_A)
        self.leg_b = parse(TestExecutionReportParsing.LEG_B)
        self.ratios = {"A": 1, "B": 1}

    def test_matching_fills_reconcile(self):
        self.assertEqual(
            reconcile_leg_fills(self.package, [self.leg_a, self.leg_b], self.ratios),
            {"A": 50, "B": 50},
        )

    def test_ratio_weighted_fills_reconcile(self):
        leg_b = CboeComplexOrderEngine.parse_fix_execution_report(
            TestExecutionReportParsing.LEG_B.replace("32=50|", "32=100|"))
        self.assertEqual(
            reconcile_leg_fills(self.package, [self.leg_a, leg_b], {"A": 1, "B": 2}),
            {"A": 50, "B": 100},
        )

    def test_short_leg_fill_is_a_hard_error(self):
        """A leg that filled less than ratio x packages means the package was not atomic."""
        short = CboeComplexOrderEngine.parse_fix_execution_report(
            TestExecutionReportParsing.LEG_B.replace("32=50|", "32=40|"))
        with self.assertRaises(CboeValidationError) as ctx:
            reconcile_leg_fills(self.package, [self.leg_a, short], self.ratios)
        self.assertIn("did not execute atomically", str(ctx.exception))

    def test_missing_leg_report_is_an_error(self):
        with self.assertRaises(CboeValidationError) as ctx:
            reconcile_leg_fills(self.package, [self.leg_a], self.ratios)
        self.assertIn("No leg fill report", str(ctx.exception))

    def test_unknown_leg_ref_id_is_an_error(self):
        stray = CboeComplexOrderEngine.parse_fix_execution_report(
            TestExecutionReportParsing.LEG_B.replace("654=B|", "654=Z|"))
        with self.assertRaises(CboeValidationError):
            reconcile_leg_fills(self.package, [self.leg_a, stray], self.ratios)

    def test_partial_fills_of_one_leg_are_aggregated(self):
        half_a = CboeComplexOrderEngine.parse_fix_execution_report(
            TestExecutionReportParsing.LEG_A.replace("32=50|", "32=20|"))
        rest_a = CboeComplexOrderEngine.parse_fix_execution_report(
            TestExecutionReportParsing.LEG_A.replace("32=50|", "32=30|"))
        self.assertEqual(
            reconcile_leg_fills(self.package, [half_a, rest_a, self.leg_b], self.ratios),
            {"A": 50, "B": 50},
        )

    def test_package_report_required(self):
        with self.assertRaises(CboeValidationError):
            reconcile_leg_fills(self.leg_a, [self.leg_a], self.ratios)

    def test_leg_reports_must_be_leg_reports(self):
        with self.assertRaises(CboeValidationError):
            reconcile_leg_fills(self.package, [self.package], self.ratios)


if __name__ == "__main__":
    unittest.main()
