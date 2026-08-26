"""Behaviour tests for the LME pre-dispatch validation engine.

Expected values are derived independently of the implementation: tonnages from
the published lot size times the lot count, notionals from tonnage times price
computed by hand, and Daily Price Limit bands from the published percentage
applied to the reference price.
"""

import logging
import unittest
from datetime import date, datetime
from decimal import Decimal

from lme_london_metal_exchange_integration import (
    LME_SPECS,
    PROMPT_CLASS_DAILY,
    PROMPT_CLASS_MONTHLY,
    PROMPT_CLASS_ROLLING,
    PROMPT_CLASS_WEEKLY,
    STATUS_DAILY_PRICE_LIMIT_BREACH,
    STATUS_INVALID_METAL_CODE,
    STATUS_INVALID_PROMPT_DATE,
    STATUS_INVALID_TICK_SIZE,
    STATUS_NO_DPL_REFERENCE_PRICE,
    STATUS_PASSED,
    LmeContractSpec,
    LmeExchangeApiEngine,
    LmeOrderPayload,
    is_third_wednesday,
    months_forward,
    to_decimal,
)

TRADE_DATE = date(2026, 8, 25)
MODULE_LOGGER = "lme_london_metal_exchange_integration"

# Rejections are logged by design; keep the test output readable without
# disabling logging for the process.
logging.getLogger(MODULE_LOGGER).setLevel(logging.CRITICAL)


def order(**kwargs):
    """An otherwise-valid copper order, with the named fields overridden."""
    params = dict(
        metal_code="CA",
        prompt_date="3M",
        side="BUY",
        price_usd_per_mt="9250.50",
        lots=10,
        previous_close_3m_usd="9200.00",
        trade_date=TRADE_DATE,
    )
    params.update(kwargs)
    return LmeOrderPayload(**params)


class TestContractCatalog(unittest.TestCase):
    """The published specifications, asserted as literals.

    These are the constants a wrong value silently mis-sizes every order
    against, so they are pinned rather than derived from the module.
    """

    def test_lot_sizes_are_per_metal(self):
        expected = {
            "AH": Decimal("25"), "AA": Decimal("20"), "NA": Decimal("20"),
            "CA": Decimal("25"), "PB": Decimal("25"), "NI": Decimal("6"),
            "SN": Decimal("5"), "ZS": Decimal("25"),
        }
        self.assertEqual({c: s.lot_size_mt for c, s in LME_SPECS.items()}, expected)

    def test_nickel_and_tin_outright_tick_is_five_dollars(self):
        # The single most expensive constant in this module. Nickel and Tin
        # outrights are $5.00/MT on LMEselect and in the Ring; everything else
        # is $0.50/MT. A universal $0.50 accepts prices LMEselect refuses.
        self.assertEqual(LME_SPECS["NI"].outright_tick_usd, Decimal("5.00"))
        self.assertEqual(LME_SPECS["SN"].outright_tick_usd, Decimal("5.00"))
        for code in ("AH", "AA", "NA", "CA", "PB", "ZS"):
            self.assertEqual(LME_SPECS[code].outright_tick_usd, Decimal("0.50"), code)

    def test_daily_price_limits_are_twelve_or_fifteen_percent(self):
        # LME Notice 26/138, effective 8 June 2026.
        for code in ("AH", "CA", "PB", "ZS"):
            self.assertEqual(LME_SPECS[code].daily_price_limit_pct,
                             Decimal("0.12"), code)
        for code in ("NI", "SN", "AA", "NA"):
            self.assertEqual(LME_SPECS[code].daily_price_limit_pct,
                             Decimal("0.15"), code)

    def test_max_monthly_tenor_differs_per_metal(self):
        self.assertEqual(LME_SPECS["CA"].max_monthly_tenor_months, 123)
        self.assertEqual(LME_SPECS["AH"].max_monthly_tenor_months, 123)
        self.assertEqual(LME_SPECS["ZS"].max_monthly_tenor_months, 63)
        self.assertEqual(LME_SPECS["SN"].max_monthly_tenor_months, 15)

    def test_every_spec_carries_its_source_and_date(self):
        for code, spec in LME_SPECS.items():
            self.assertTrue(spec.specs_source, code)
            self.assertTrue(spec.specs_as_of, code)

    def test_spec_rejects_non_positive_values(self):
        with self.assertRaises(ValueError):
            LmeContractSpec("XX", "Bad", Decimal("0"), Decimal("0.50"),
                            Decimal("0.01"), Decimal("0.12"), 63, "src", "2026-08-25")
        with self.assertRaises(ValueError):
            LmeContractSpec("XX", "Bad", Decimal("25"), Decimal("0.50"),
                            Decimal("0.01"), Decimal("0.12"), 0, "src", "2026-08-25")


class TestTonnageAndNotional(unittest.TestCase):

    def setUp(self):
        self.engine = LmeExchangeApiEngine()

    def test_copper_tonnage_and_notional(self):
        # 10 lots x 25 MT = 250 MT. 250 x 9,250.50 = 2,312,625.00 by hand.
        report = self.engine.validate_and_route_order(order())
        self.assertEqual(report.status, STATUS_PASSED)
        self.assertTrue(report.ready_to_send)
        self.assertEqual(report.metal_name, "Copper Grade A")
        self.assertEqual(report.total_tonnage_mt, Decimal("250"))
        self.assertEqual(report.total_notional_usd, Decimal("2312625.00"))

    def test_nickel_uses_six_tonne_lots_not_twenty_five(self):
        # 10 lots x 6 MT = 60 MT. Reading Nickel as a 25 MT lot would give
        # 250 MT — a 4.17x over-position.
        report = self.engine.validate_and_route_order(
            order(metal_code="NI", price_usd_per_mt="16500.00",
                  previous_close_3m_usd="16400.00"))
        self.assertEqual(report.status, STATUS_PASSED)
        self.assertEqual(report.total_tonnage_mt, Decimal("60"))
        self.assertEqual(report.total_notional_usd, Decimal("990000.00"))

    def test_tin_uses_five_tonne_lots(self):
        # 7 lots x 5 MT = 35 MT. 35 x 33,000 = 1,155,000 by hand.
        report = self.engine.validate_and_route_order(
            order(metal_code="SN", lots=7, price_usd_per_mt="33000.00",
                  previous_close_3m_usd="33000.00"))
        self.assertEqual(report.total_tonnage_mt, Decimal("35"))
        self.assertEqual(report.total_notional_usd, Decimal("1155000.00"))

    def test_aluminium_alloy_uses_twenty_tonne_lots(self):
        # AA is not AH. 3 lots x 20 MT = 60 MT; 60 x 2,000 = 120,000.
        report = self.engine.validate_and_route_order(
            order(metal_code="AA", lots=3, price_usd_per_mt="2000.00",
                  previous_close_3m_usd="2000.00"))
        self.assertEqual(report.total_tonnage_mt, Decimal("60"))
        self.assertEqual(report.total_notional_usd, Decimal("120000.00"))

    def test_notional_is_exact_decimal_money(self):
        report = self.engine.validate_and_route_order(order())
        self.assertIsInstance(report.total_notional_usd, Decimal)
        self.assertEqual(report.total_notional_usd.as_tuple().exponent, -2)

    def test_float_and_string_prices_agree(self):
        # A caller passing 9250.50 as a float must get the same answer as one
        # passing it as a string; floats are routed through str, so the price
        # is 9250.5 and not 9250.4999999999995.
        from_str = self.engine.validate_and_route_order(
            order(price_usd_per_mt="9250.50"))
        from_float = self.engine.validate_and_route_order(
            order(price_usd_per_mt=9250.50))
        self.assertEqual(from_float.price_usd_per_mt, Decimal("9250.5"))
        self.assertEqual(from_float.total_notional_usd, from_str.total_notional_usd)

    def test_tick_check_is_exact_for_a_cent_denominated_tick(self):
        # In binary float, 0.03 % 0.01 is not zero, so a float tick check
        # misreads almost every cent-denominated price as off-tick. The catalog
        # is injectable, so a refreshed spec may carry one.
        cent_tick = LmeContractSpec(
            "CA", "Copper Grade A", Decimal("25"), Decimal("0.01"),
            Decimal("0.01"), Decimal("0.12"), 123, "test", "2026-08-25")
        engine = LmeExchangeApiEngine(specs={"CA": cent_tick})
        report = engine.validate_and_route_order(order(price_usd_per_mt="9250.03"))
        self.assertEqual(report.status, STATUS_PASSED)


class TestTickSize(unittest.TestCase):

    def setUp(self):
        self.engine = LmeExchangeApiEngine()

    def test_sub_tick_copper_price_is_rejected(self):
        report = self.engine.validate_and_route_order(
            order(price_usd_per_mt="9250.23"))
        self.assertEqual(report.status, STATUS_INVALID_TICK_SIZE)
        self.assertFalse(report.is_price_tick_valid)
        self.assertFalse(report.ready_to_send)

    def test_nickel_rejects_a_price_valid_only_on_a_fifty_cent_tick(self):
        # Regression: $16,500.50 is a whole number of $0.50 steps and was
        # accepted while the module assumed a universal $0.50 tick. LMEselect
        # rejects it — Nickel is $5.00.
        report = self.engine.validate_and_route_order(
            order(metal_code="NI", price_usd_per_mt="16500.50",
                  previous_close_3m_usd="16400.00"))
        self.assertEqual(report.status, STATUS_INVALID_TICK_SIZE)
        self.assertEqual(report.tick_size_usd, Decimal("5.00"))

    def test_tin_rejects_a_price_valid_only_on_a_fifty_cent_tick(self):
        report = self.engine.validate_and_route_order(
            order(metal_code="SN", price_usd_per_mt="33000.50",
                  previous_close_3m_usd="33000.00"))
        self.assertEqual(report.status, STATUS_INVALID_TICK_SIZE)

    def test_nickel_accepts_a_whole_five_dollar_step(self):
        report = self.engine.validate_and_route_order(
            order(metal_code="NI", price_usd_per_mt="16505.00",
                  previous_close_3m_usd="16400.00"))
        self.assertEqual(report.status, STATUS_PASSED)

    def test_tick_check_is_reported_per_metal(self):
        copper = self.engine.validate_and_route_order(order())
        nickel = self.engine.validate_and_route_order(
            order(metal_code="NI", price_usd_per_mt="16500.00",
                  previous_close_3m_usd="16400.00"))
        self.assertEqual(copper.tick_size_usd, Decimal("0.50"))
        self.assertEqual(nickel.tick_size_usd, Decimal("5.00"))


class TestDailyPriceLimit(unittest.TestCase):
    """DPL band is the reference price +/- the published percentage.

    Copper at 12% on a 9,200.00 reference: 9,200 x 0.12 = 1,104.00, so the band
    is 8,096.00 to 10,304.00 — computed by hand, not from the module.
    """

    def setUp(self):
        self.engine = LmeExchangeApiEngine()

    def test_band_matches_hand_computed_values(self):
        report = self.engine.validate_and_route_order(order())
        self.assertEqual(report.dpl_lower_usd, Decimal("8096.00"))
        self.assertEqual(report.dpl_upper_usd, Decimal("10304.00"))

    def test_price_at_the_upper_limit_is_accepted(self):
        report = self.engine.validate_and_route_order(
            order(price_usd_per_mt="10304.00"))
        self.assertEqual(report.status, STATUS_PASSED)

    def test_price_one_tick_above_the_upper_limit_is_rejected(self):
        report = self.engine.validate_and_route_order(
            order(price_usd_per_mt="10304.50"))
        self.assertEqual(report.status, STATUS_DAILY_PRICE_LIMIT_BREACH)

    def test_price_at_the_lower_limit_is_accepted(self):
        report = self.engine.validate_and_route_order(
            order(side="SELL", price_usd_per_mt="8096.00"))
        self.assertEqual(report.status, STATUS_PASSED)

    def test_price_one_tick_below_the_lower_limit_is_rejected(self):
        report = self.engine.validate_and_route_order(
            order(side="SELL", price_usd_per_mt="8095.50"))
        self.assertEqual(report.status, STATUS_DAILY_PRICE_LIMIT_BREACH)

    def test_band_is_symmetric_not_directional(self):
        # Unlike ICE's Reasonability Limit, the LME accepts neither a bid above
        # the upper limit nor an offer below the lower one — and a deep passive
        # bid below the lower limit is refused too, which a directional check
        # would wrongly allow.
        low = "8000.00"
        for side in ("BUY", "SELL"):
            report = self.engine.validate_and_route_order(
                order(side=side, price_usd_per_mt=low))
            self.assertEqual(report.status, STATUS_DAILY_PRICE_LIMIT_BREACH, side)
        high = "10400.00"
        for side in ("BUY", "SELL"):
            report = self.engine.validate_and_route_order(
                order(side=side, price_usd_per_mt=high))
            self.assertEqual(report.status, STATUS_DAILY_PRICE_LIMIT_BREACH, side)

    def test_nickel_uses_a_fifteen_percent_band(self):
        # 16,400 x 0.15 = 2,460 -> 13,940.00 to 18,860.00.
        report = self.engine.validate_and_route_order(
            order(metal_code="NI", price_usd_per_mt="16500.00",
                  previous_close_3m_usd="16400.00"))
        self.assertEqual(report.dpl_lower_usd, Decimal("13940.00"))
        self.assertEqual(report.dpl_upper_usd, Decimal("18860.00"))

    def test_missing_reference_price_fails_closed(self):
        # The DPL is a real order-entry rejection, so an unchecked one is not
        # a pass.
        report = self.engine.validate_and_route_order(
            order(previous_close_3m_usd=None))
        self.assertEqual(report.status, STATUS_NO_DPL_REFERENCE_PRICE)
        self.assertFalse(report.ready_to_send)
        self.assertIsNone(report.dpl_upper_usd)

    def test_non_positive_reference_price_raises(self):
        with self.assertRaises(ValueError):
            self.engine.validate_and_route_order(order(previous_close_3m_usd="0"))


class TestPromptDate(unittest.TestCase):

    def setUp(self):
        self.engine = LmeExchangeApiEngine()

    def test_rolling_keywords_are_accepted(self):
        for keyword in ("3M", "CASH", "TOM", "cash", " 3m "):
            report = self.engine.validate_and_route_order(
                order(prompt_date=keyword))
            self.assertEqual(report.status, STATUS_PASSED, keyword)
            self.assertEqual(report.prompt_class, PROMPT_CLASS_ROLLING)
            self.assertTrue(report.prompt_date_confirmed)

    def test_unparseable_prompt_date_raises(self):
        # Regression: this string was previously uppercased, echoed back, and
        # the order approved.
        with self.assertRaises(ValueError):
            self.engine.validate_and_route_order(order(prompt_date="not-a-date"))
        with self.assertRaises(ValueError):
            self.engine.validate_and_route_order(order(prompt_date="2026-13-45"))

    def test_prompt_date_on_or_before_trade_date_is_rejected(self):
        for day in (TRADE_DATE, date(2026, 1, 1)):
            report = self.engine.validate_and_route_order(order(prompt_date=day))
            self.assertEqual(report.status, STATUS_INVALID_PROMPT_DATE, day)

    def test_prompt_beyond_the_contracts_listed_tenor_is_rejected(self):
        # Tin lists monthly prompts out to 15 months. Copper lists 123, so the
        # same date is fine there.
        far = date(2031, 8, 20)
        tin = self.engine.validate_and_route_order(
            order(metal_code="SN", prompt_date=far, price_usd_per_mt="33000.00",
                  previous_close_3m_usd="33000.00"))
        self.assertEqual(tin.status, STATUS_INVALID_PROMPT_DATE)
        copper = self.engine.validate_and_route_order(order(prompt_date=far))
        self.assertEqual(copper.status, STATUS_PASSED)

    def test_prompt_classification_bands(self):
        cases = [
            (date(2026, 9, 15), PROMPT_CLASS_DAILY),     # ~0 months out
            (date(2026, 12, 16), PROMPT_CLASS_WEEKLY),   # ~3 months out
            (date(2027, 8, 18), PROMPT_CLASS_MONTHLY),   # ~12 months out
        ]
        for day, expected in cases:
            report = self.engine.validate_and_route_order(order(prompt_date=day))
            self.assertEqual(report.prompt_class, expected, day)

    def test_datetime_is_narrowed_to_its_date(self):
        # datetime subclasses date, so it reaches the comparison and raises
        # "can't compare datetime.datetime to datetime.date" unless narrowed.
        report = self.engine.validate_and_route_order(
            order(prompt_date=datetime(2027, 8, 18, 14, 30)))
        self.assertEqual(report.status, STATUS_PASSED)
        self.assertEqual(report.prompt_date, "2027-08-18")

    def test_datetime_trade_date_is_narrowed_to_its_date(self):
        report = self.engine.validate_and_route_order(
            order(prompt_date=date(2027, 8, 18),
                  trade_date=datetime(2026, 8, 25, 9, 0)))
        self.assertEqual(report.status, STATUS_PASSED)

    def test_non_date_trade_date_raises(self):
        with self.assertRaises(TypeError):
            self.engine.validate_and_route_order(
                order(prompt_date=date(2027, 8, 18), trade_date="2026-08-25"))

    def test_iso_string_and_date_object_agree(self):
        day = date(2027, 8, 18)
        from_date = self.engine.validate_and_route_order(order(prompt_date=day))
        from_str = self.engine.validate_and_route_order(
            order(prompt_date="2027-08-18"))
        self.assertEqual(from_str.prompt_date, from_date.prompt_date)
        self.assertEqual(from_str.prompt_class, from_date.prompt_class)

    def test_non_third_wednesday_monthly_prompt_warns_but_does_not_reject(self):
        # The LME publishes substitute prompt dates around holidays, so a
        # structural mismatch is a flag to check the calendar, not a refusal.
        report = self.engine.validate_and_route_order(
            order(prompt_date=date(2027, 8, 17)))  # a Tuesday
        self.assertEqual(report.status, STATUS_PASSED)
        self.assertFalse(report.prompt_date_confirmed)
        self.assertTrue(any("third Wednesday" in w for w in report.warnings))

    def test_supplied_calendar_is_authoritative(self):
        listed = date(2027, 8, 18)
        calendar = frozenset({listed})
        good = self.engine.validate_and_route_order(
            order(prompt_date=listed), valid_prompt_dates=calendar)
        self.assertEqual(good.status, STATUS_PASSED)
        self.assertTrue(good.prompt_date_confirmed)
        self.assertEqual(good.warnings, ())

        bad = self.engine.validate_and_route_order(
            order(prompt_date=date(2027, 8, 19)), valid_prompt_dates=calendar)
        self.assertEqual(bad.status, STATUS_INVALID_PROMPT_DATE)

    def test_unconfirmed_prompt_is_flagged_on_an_otherwise_clean_order(self):
        report = self.engine.validate_and_route_order(
            order(prompt_date=date(2027, 8, 18)))
        self.assertEqual(report.status, STATUS_PASSED)
        self.assertFalse(report.prompt_date_confirmed)
        self.assertTrue(any("not confirmed" in w for w in report.warnings))


class TestPayloadValidation(unittest.TestCase):
    """Structurally invalid payloads raise; they never come back approved."""

    def setUp(self):
        self.engine = LmeExchangeApiEngine()

    def test_non_positive_lots_raise(self):
        # Regression: -10 lots previously returned LME_ORDER_VALIDATED with a
        # tonnage of -250 MT and a negative notional.
        for lots in (0, -1, -10):
            with self.assertRaises(ValueError, msg=lots):
                self.engine.validate_and_route_order(order(lots=lots))

    def test_fractional_or_non_int_lots_raise(self):
        for lots in (1.5, 10.0, "10", True, None):
            with self.assertRaises(TypeError, msg=lots):
                self.engine.validate_and_route_order(order(lots=lots))

    def test_non_positive_or_non_finite_price_raises(self):
        for price in ("-9250.50", "0"):
            with self.assertRaises(ValueError, msg=price):
                self.engine.validate_and_route_order(order(price_usd_per_mt=price))
        for price in (float("nan"), float("inf"), "NaN"):
            with self.assertRaises(ValueError, msg=price):
                self.engine.validate_and_route_order(order(price_usd_per_mt=price))

    def test_unknown_side_raises(self):
        # Regression: 'BANANA' was uppercased, echoed, and approved.
        for side in ("BANANA", "", "   ", "B"):
            with self.assertRaises((ValueError, TypeError), msg=side):
                self.engine.validate_and_route_order(order(side=side))

    def test_side_is_case_and_whitespace_insensitive(self):
        report = self.engine.validate_and_route_order(order(side=" buy "))
        self.assertEqual(report.side, "BUY")

    def test_unknown_metal_code_is_a_verdict_not_an_exception(self):
        report = self.engine.validate_and_route_order(order(metal_code="XX"))
        self.assertEqual(report.status, STATUS_INVALID_METAL_CODE)
        self.assertFalse(report.ready_to_send)
        self.assertEqual(report.total_tonnage_mt, Decimal("0"))
        self.assertEqual(report.total_notional_usd, Decimal("0"))

    def test_metal_code_is_case_and_whitespace_insensitive(self):
        report = self.engine.validate_and_route_order(order(metal_code=" ca "))
        self.assertEqual(report.metal_code, "CA")
        self.assertEqual(report.status, STATUS_PASSED)

    def test_non_string_metal_code_raises(self):
        with self.assertRaises(TypeError):
            self.engine.validate_and_route_order(order(metal_code=42))


class TestEngineConstruction(unittest.TestCase):

    def test_catalog_is_injectable(self):
        only_copper = {"CA": LME_SPECS["CA"]}
        engine = LmeExchangeApiEngine(specs=only_copper)
        self.assertEqual(
            engine.validate_and_route_order(order()).status, STATUS_PASSED)
        self.assertEqual(
            engine.validate_and_route_order(order(metal_code="NI")).status,
            STATUS_INVALID_METAL_CODE)

    def test_catalog_is_copied_on_construction(self):
        supplied = {"CA": LME_SPECS["CA"]}
        engine = LmeExchangeApiEngine(specs=supplied)
        supplied.clear()
        self.assertEqual(
            engine.validate_and_route_order(order()).status, STATUS_PASSED)

    def test_specs_property_does_not_expose_internal_state(self):
        engine = LmeExchangeApiEngine()
        engine.specs.clear()
        self.assertIn("CA", engine.specs)

    def test_empty_catalog_raises(self):
        with self.assertRaises(ValueError):
            LmeExchangeApiEngine(specs={})


class TestHelpers(unittest.TestCase):

    def test_is_third_wednesday(self):
        self.assertTrue(is_third_wednesday(date(2026, 8, 19)))
        self.assertFalse(is_third_wednesday(date(2026, 8, 12)))  # second Wed
        self.assertFalse(is_third_wednesday(date(2026, 8, 26)))  # fourth Wed
        self.assertFalse(is_third_wednesday(date(2026, 8, 18)))  # Tuesday

    def test_months_forward(self):
        self.assertEqual(months_forward(date(2026, 8, 25), date(2026, 11, 25)), 3)
        self.assertEqual(months_forward(date(2026, 8, 25), date(2026, 11, 24)), 2)
        self.assertEqual(months_forward(date(2026, 8, 25), date(2027, 8, 25)), 12)

    def test_to_decimal_rejects_bool_and_non_numeric(self):
        with self.assertRaises(TypeError):
            to_decimal(True, "price")
        with self.assertRaises(TypeError):
            to_decimal(None, "price")
        with self.assertRaises(ValueError):
            to_decimal("abc", "price")

    def test_to_decimal_routes_floats_through_str(self):
        self.assertEqual(to_decimal(9250.50, "price"), Decimal("9250.5"))


if __name__ == "__main__":
    unittest.main()
