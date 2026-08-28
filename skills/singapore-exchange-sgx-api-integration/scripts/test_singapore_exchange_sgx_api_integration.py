"""Unit tests for the SGX pre-trade contract-specification validator.

Expected values are taken from the published SGX contract specifications and the
SGX-ST minimum bid size scale (see ``references/standards.md``), not recomputed from
the module's own tables, so a wrong figure in the table fails a test rather than
agreeing with it.
"""
import unittest
from decimal import Decimal

from singapore_exchange_sgx_api_integration import (
    SGX_DERIVATIVES_CONTRACTS,
    OrderSide,
    PriceOutOfRangeError,
    SGXContractSpec,
    SGXMarket,
    SGXOrderError,
    SGXOrderType,
    SGXSecurityClass,
    SGXTradeType,
    STATUS_INVALID_PRICE,
    STATUS_INVALID_QUANTITY,
    STATUS_INVALID_TICK,
    STATUS_MISSING_LIMIT_PRICE,
    STATUS_VALIDATED,
    TickSizeUnavailableError,
    UnknownContractError,
    get_sgx_st_minimum_bid_size,
    is_on_tick,
    to_decimal,
    validate_derivatives_order,
    validate_securities_order,
)


class TestContractTable(unittest.TestCase):
    """The shipped figures must match the current published specifications."""

    def test_china_a50_ticks_at_one_index_point_not_two_and_a_half(self):
        # Cut from 2.5 to 1 index point on 5 October 2020. SGX's archived 2018 PDF
        # still shows 2.5; a table copied from it is stale by six years.
        spec = SGX_DERIVATIVES_CONTRACTS["CN"]
        self.assertEqual(spec.tick_size_for(), Decimal("1"))
        self.assertEqual(spec.contract_multiplier, Decimal("1"))
        self.assertEqual(spec.currency, "USD")
        self.assertEqual(spec.tick_value(), Decimal("1"))

    def test_nikkei_multiplier_and_three_published_increments(self):
        spec = SGX_DERIVATIVES_CONTRACTS["NK"]
        self.assertEqual(spec.contract_multiplier, Decimal("500"))
        self.assertEqual(spec.currency, "JPY")
        self.assertEqual(spec.tick_size_for(SGXTradeType.OUTRIGHT), Decimal("5"))
        self.assertEqual(spec.tick_size_for(SGXTradeType.CALENDAR_SPREAD), Decimal("1"))
        self.assertEqual(
            spec.tick_size_for(SGXTradeType.TRADE_AT_INDEX_CLOSE), Decimal("0.25")
        )
        # JPY 2,500 / JPY 500 / JPY 125 per tick, derived independently here.
        self.assertEqual(spec.tick_value(SGXTradeType.OUTRIGHT), Decimal("2500"))
        self.assertEqual(spec.tick_value(SGXTradeType.CALENDAR_SPREAD), Decimal("500"))
        self.assertEqual(
            spec.tick_value(SGXTradeType.TRADE_AT_INDEX_CLOSE), Decimal("125")
        )

    def test_taiwan_contract_is_ftse_not_msci(self):
        spec = SGX_DERIVATIVES_CONTRACTS["TWN"]
        self.assertIn("FTSE Taiwan", spec.name)
        self.assertEqual(spec.contract_multiplier, Decimal("40"))
        self.assertEqual(spec.tick_size_for(), Decimal("0.25"))
        self.assertEqual(spec.tick_value(), Decimal("10"))  # US$10 per tick
        self.assertEqual(
            spec.tick_size_for(SGXTradeType.NEGOTIATED_LARGE_TRADE), Decimal("0.01")
        )
        self.assertEqual(
            spec.tick_size_for(SGXTradeType.TRADE_AT_INDEX_CLOSE), Decimal("0.05")
        )

    def test_retired_msci_taiwan_code_is_absent(self):
        # 'TW' (MSCI Taiwan, US$100 x index, 0.1 point tick) left SGX in 2020.
        self.assertNotIn("TW", SGX_DERIVATIVES_CONTRACTS)

    def test_iron_ore_code_is_fef_and_ticks_at_one_cent_per_tonne(self):
        self.assertNotIn("FE", SGX_DERIVATIVES_CONTRACTS)
        spec = SGX_DERIVATIVES_CONTRACTS["FEF"]
        self.assertEqual(spec.tick_size_for(), Decimal("0.01"))
        self.assertEqual(spec.contract_multiplier, Decimal("100"))  # metric tonnes
        self.assertEqual(spec.tick_value(), Decimal("1"))  # US$1 per contract per tick

    def test_every_spec_records_its_source_and_verification_date(self):
        for code, spec in SGX_DERIVATIVES_CONTRACTS.items():
            with self.subTest(code=code):
                self.assertTrue(spec.source, f"{code} has no source attribution")
                self.assertRegex(spec.verified_on, r"^\d{4}-\d{2}-\d{2}$")
                self.assertIs(spec.market, SGXMarket.DERIVATIVES_TITAN_DT)

    def test_unpublished_increment_raises_instead_of_reusing_the_outright_tick(self):
        spec = SGX_DERIVATIVES_CONTRACTS["CN"]
        with self.assertRaises(TickSizeUnavailableError):
            spec.tick_size_for(SGXTradeType.CALENDAR_SPREAD)

    def test_notional_uses_the_contract_multiplier(self):
        # 38,000 index points x JPY 500 = JPY 19,000,000.
        self.assertEqual(
            SGX_DERIVATIVES_CONTRACTS["NK"].notional("38000"), Decimal("19000000")
        )
        # 105.25 US$/tonne x 100 tonnes = US$10,525.
        self.assertEqual(
            SGX_DERIVATIVES_CONTRACTS["FEF"].notional("105.25"), Decimal("10525.00")
        )


class TestDecimalTickArithmetic(unittest.TestCase):
    """Tick alignment is exact decimal arithmetic with no tolerance."""

    def test_float_prices_are_read_through_their_repr(self):
        self.assertEqual(to_decimal(0.005), Decimal("0.005"))
        self.assertEqual(to_decimal("12500.0"), Decimal("12500.0"))

    def test_binary_float_remainder_does_not_leak_into_the_decision(self):
        # 100.03 % 0.05 is 0.0299999999999956 in binary float; 1.005 % 0.005 is
        # 0.004999999999999873. Both must resolve exactly here.
        self.assertFalse(is_on_tick(100.03, "0.05"))
        self.assertTrue(is_on_tick(1.005, "0.005"))

    def test_zero_or_negative_tick_size_raises(self):
        for tick in ("0", "-2.5"):
            with self.subTest(tick=tick), self.assertRaises(SGXOrderError):
                is_on_tick("12500", tick)

    def test_non_numeric_price_raises(self):
        with self.assertRaises(SGXOrderError):
            to_decimal("not-a-price")
        with self.assertRaises(SGXOrderError):
            to_decimal(True)

    def test_nan_and_infinity_are_refused_rather_than_propagated(self):
        # NaN compares False against every bound, so it is neither on tick nor off
        # tick; letting it through validates an order priced off a missing quote.
        for value in (float("nan"), float("inf"), Decimal("NaN")):
            with self.subTest(value=value), self.assertRaises(SGXOrderError):
                to_decimal(value)
        self.assertEqual(
            validate_derivatives_order(
                "CN", OrderSide.BUY, float("nan"), price="12500"
            ).status,
            STATUS_INVALID_QUANTITY,
        )


class TestDerivativesValidation(unittest.TestCase):

    def test_valid_a50_limit_order(self):
        result = validate_derivatives_order(
            "CN", OrderSide.BUY, 10, SGXOrderType.LIMIT, price="12501"
        )
        self.assertEqual(result.status, STATUS_VALIDATED)
        self.assertTrue(result.is_valid)
        self.assertEqual(result.violations, ())
        self.assertEqual(result.tick_size, Decimal("1"))
        self.assertEqual(result.order_notional, Decimal("125010"))
        self.assertEqual(result.currency, "USD")
        self.assertIs(result.market, SGXMarket.DERIVATIVES_TITAN_DT)

    def test_order_notional_scales_with_quantity_and_keeps_contract_currency(self):
        # 38,000 index points x JPY 500 x 3 contracts = JPY 57,000,000.
        result = validate_derivatives_order("NK", OrderSide.SELL, 3, price="38000")
        self.assertEqual(result.order_notional, Decimal("57000000"))
        self.assertEqual(result.currency, "JPY")

    def test_price_legal_under_the_current_tick_is_not_rejected(self):
        # Regression: 12,501 is off-tick under the retired 2.5 increment and legal
        # under the current 1-point increment. The old table rejected this order.
        self.assertTrue(
            validate_derivatives_order("CN", OrderSide.BUY, 1, price="12501").is_valid
        )

    def test_off_tick_price_within_a_float_tolerance_is_rejected(self):
        # Regression: the previous float implementation rounded the remainder to four
        # decimal places, so 12500.00004 passed as an exact multiple of 2.5.
        result = validate_derivatives_order("CN", OrderSide.BUY, 1, price="12500.00004")
        self.assertEqual(result.status, STATUS_INVALID_TICK)
        self.assertIn(STATUS_INVALID_TICK, result.violations)

    def test_nikkei_outright_off_tick_price_is_rejected(self):
        result = validate_derivatives_order("NK", OrderSide.SELL, 5, price="38002")
        self.assertEqual(result.status, STATUS_INVALID_TICK)

    def test_calendar_spread_price_is_checked_on_the_spread_increment(self):
        # 38,001 is off tick as an outright (5-point) price and legal as a calendar
        # spread differential (1-point). Checking a spread on the outright increment
        # rejects a legal price.
        outright = validate_derivatives_order("NK", OrderSide.BUY, 1, price="38001")
        spread = validate_derivatives_order(
            "NK", OrderSide.BUY, 1, price="38001",
            trade_type=SGXTradeType.CALENDAR_SPREAD,
        )
        self.assertEqual(outright.status, STATUS_INVALID_TICK)
        self.assertTrue(spread.is_valid)
        self.assertEqual(spread.tick_size, Decimal("1"))

    def test_trade_at_index_close_increment_is_finer_than_outright(self):
        result = validate_derivatives_order(
            "TWN", OrderSide.BUY, 2, price="1005.05",
            trade_type=SGXTradeType.TRADE_AT_INDEX_CLOSE,
        )
        self.assertTrue(result.is_valid)
        self.assertEqual(result.tick_size, Decimal("0.05"))
        # The same price as an outright is off tick on the 0.25 increment.
        self.assertEqual(
            validate_derivatives_order("TWN", OrderSide.BUY, 2, price="1005.05").status,
            STATUS_INVALID_TICK,
        )

    def test_unknown_product_code_raises_rather_than_passing_through(self):
        # Regression: the previous implementation skipped tick validation entirely
        # for any symbol missing from its table, so a typo routed unvalidated.
        with self.assertRaises(UnknownContractError):
            validate_derivatives_order("ZZZ", OrderSide.BUY, 1, price="123.4567")

    def test_retired_taiwan_code_raises_and_names_its_replacement(self):
        with self.assertRaises(UnknownContractError) as ctx:
            validate_derivatives_order("TW", OrderSide.BUY, 1, price="900.1")
        self.assertIn("TWN", str(ctx.exception))

    def test_product_code_is_normalised(self):
        self.assertTrue(
            validate_derivatives_order(" fef ", OrderSide.SELL, 1, price="105.25").is_valid
        )

    def test_non_positive_prices_are_rejected(self):
        for price in ("0", "-38000"):
            with self.subTest(price=price):
                result = validate_derivatives_order("NK", OrderSide.BUY, 1, price=price)
                self.assertEqual(result.status, STATUS_INVALID_PRICE)
                self.assertIsNone(result.order_notional)

    def test_limit_order_without_a_price_is_rejected(self):
        result = validate_derivatives_order("CN", OrderSide.BUY, 1, SGXOrderType.LIMIT)
        self.assertEqual(result.status, STATUS_MISSING_LIMIT_PRICE)

    def test_market_order_carries_no_price_to_check(self):
        result = validate_derivatives_order("CN", OrderSide.BUY, 1, SGXOrderType.MARKET)
        self.assertTrue(result.is_valid)
        self.assertIsNone(result.order_notional)

    def test_market_order_reports_no_notional_from_an_unchecked_price(self):
        # A price alongside a MARKET order is never tick-checked, so it must not come
        # back as a validated notional.
        result = validate_derivatives_order(
            "CN", OrderSide.BUY, 4, SGXOrderType.MARKET, price="12500.7"
        )
        self.assertTrue(result.is_valid)
        self.assertIsNone(result.order_notional)

    def test_stop_limit_checks_both_prices(self):
        both_on_tick = validate_derivatives_order(
            "CN", OrderSide.SELL, 1, SGXOrderType.STOP_LIMIT,
            price="12500", stop_price="12495",
        )
        self.assertTrue(both_on_tick.is_valid)
        off_tick_trigger = validate_derivatives_order(
            "CN", OrderSide.SELL, 1, SGXOrderType.STOP_LIMIT,
            price="12500", stop_price="12495.5",
        )
        self.assertEqual(off_tick_trigger.status, STATUS_INVALID_TICK)
        missing_trigger = validate_derivatives_order(
            "CN", OrderSide.SELL, 1, SGXOrderType.STOP_LIMIT, price="12500",
        )
        self.assertEqual(missing_trigger.status, STATUS_MISSING_LIMIT_PRICE)

    def test_invalid_quantities_are_rejected(self):
        for quantity in (0, -5, 1.5, "10", None):
            with self.subTest(quantity=quantity):
                result = validate_derivatives_order(
                    "CN", OrderSide.BUY, quantity, price="12500"
                )
                self.assertEqual(result.status, STATUS_INVALID_QUANTITY)

    def test_an_order_breaching_two_rules_reports_both(self):
        result = validate_derivatives_order("CN", OrderSide.BUY, -3, price="12500.5")
        self.assertEqual(result.violations, (STATUS_INVALID_TICK, STATUS_INVALID_QUANTITY))
        self.assertEqual(result.status, STATUS_INVALID_TICK)

    def test_free_text_side_raises(self):
        with self.assertRaises(SGXOrderError):
            validate_derivatives_order("CN", "BUY", 1, price="12500")

    def test_caller_supplied_contract_table_is_used(self):
        micro_nikkei = SGXContractSpec(
            product_code="NS",
            name="SGX Micro Nikkei Stock Average Futures",
            market=SGXMarket.DERIVATIVES_TITAN_DT,
            currency="JPY",
            contract_multiplier=Decimal("10"),
            multiplier_unit="JPY per index point",
            tick_sizes={SGXTradeType.OUTRIGHT: Decimal("2.5")},
            source="Titan-DT reference data",
            verified_on="2026-06-22",
        )
        table = {"NS": micro_nikkei}
        self.assertTrue(
            validate_derivatives_order(
                "NS", OrderSide.BUY, 1, price="38002.5", contracts=table
            ).is_valid
        )
        with self.assertRaises(UnknownContractError):
            validate_derivatives_order("CN", OrderSide.BUY, 1, price="12500", contracts=table)


class TestSecuritiesMinimumBidSize(unittest.TestCase):

    def test_ordinary_share_bands(self):
        cases = {
            "0.199": Decimal("0.001"),
            "0.20": Decimal("0.005"),
            "0.995": Decimal("0.005"),
            "1.00": Decimal("0.01"),
            "45.60": Decimal("0.01"),
        }
        for price, expected in cases.items():
            with self.subTest(price=price):
                self.assertEqual(get_sgx_st_minimum_bid_size(price), expected)

    def test_band_edges_are_lower_inclusive(self):
        # S$0.20 is the first price in the 0.005 band; S$1.00 the first in the 0.01 band.
        self.assertEqual(get_sgx_st_minimum_bid_size("0.1999"), Decimal("0.001"))
        self.assertEqual(get_sgx_st_minimum_bid_size("0.20"), Decimal("0.005"))
        self.assertEqual(get_sgx_st_minimum_bid_size("0.9999"), Decimal("0.005"))
        self.assertEqual(get_sgx_st_minimum_bid_size("1.0000"), Decimal("0.01"))

    def test_structured_warrants_keep_the_half_cent_band_to_one_dollar_ninety_nine(self):
        self.assertEqual(
            get_sgx_st_minimum_bid_size("1.50", SGXSecurityClass.STRUCTURED_WARRANT),
            Decimal("0.005"),
        )
        self.assertEqual(
            get_sgx_st_minimum_bid_size("2.00", SGXSecurityClass.STRUCTURED_WARRANT),
            Decimal("0.01"),
        )
        # The same price on an ordinary share is a full cent.
        self.assertEqual(get_sgx_st_minimum_bid_size("1.50"), Decimal("0.01"))

    def test_debt_is_flat(self):
        self.assertEqual(
            get_sgx_st_minimum_bid_size("0.15", SGXSecurityClass.DEBT), Decimal("0.001")
        )
        self.assertEqual(
            get_sgx_st_minimum_bid_size("101.50", SGXSecurityClass.DEBT), Decimal("0.001")
        )

    def test_etf_bid_size_is_not_guessed(self):
        with self.assertRaises(TickSizeUnavailableError):
            get_sgx_st_minimum_bid_size("1.35", SGXSecurityClass.ETF_ETN)

    def test_non_positive_price_has_no_band(self):
        with self.assertRaises(PriceOutOfRangeError):
            get_sgx_st_minimum_bid_size("0")


class TestSecuritiesValidation(unittest.TestCase):

    def test_valid_dbs_order(self):
        result = validate_securities_order("D05", OrderSide.BUY, 1000, price="45.60")
        self.assertTrue(result.is_valid)
        self.assertEqual(result.tick_size, Decimal("0.01"))
        self.assertEqual(result.order_notional, Decimal("45600.00"))
        self.assertIs(result.market, SGXMarket.SECURITIES_REACH_ST)

    def test_sub_dollar_share_bids_in_half_cents_not_cents(self):
        # Regression: a flat S$0.01 tick rejects S$0.615, which is a legal price for
        # an ordinary share in the S$0.20-S$0.995 band.
        result = validate_securities_order("BSL", OrderSide.BUY, 5000, price="0.615")
        self.assertTrue(result.is_valid)
        self.assertEqual(result.tick_size, Decimal("0.005"))

    def test_half_cent_price_above_one_dollar_is_off_tick(self):
        result = validate_securities_order("D05", OrderSide.BUY, 100, price="1.005")
        self.assertEqual(result.status, STATUS_INVALID_TICK)

    def test_penny_stock_bids_in_a_tenth_of_a_cent(self):
        self.assertTrue(
            validate_securities_order("XYZ", OrderSide.SELL, 10000, price="0.153").is_valid
        )
        self.assertEqual(
            validate_securities_order("XYZ", OrderSide.SELL, 10000, price="0.1535").status,
            STATUS_INVALID_TICK,
        )

    def test_stop_limit_trigger_is_checked_in_its_own_band(self):
        # Limit S$1.02 sits in the 0.01 band; trigger S$0.995 sits in the 0.005 band
        # and is legal there. A single cached tick would reject the trigger.
        result = validate_securities_order(
            "ABC", OrderSide.SELL, 100, price="1.02",
            order_type=SGXOrderType.STOP_LIMIT, stop_price="0.995",
        )
        self.assertTrue(result.is_valid)
        self.assertEqual(result.tick_size, Decimal("0.01"))

    def test_foreign_currency_counter_is_refused(self):
        with self.assertRaises(SGXOrderError):
            validate_securities_order(
                "HK1", OrderSide.BUY, 100, price="5.00", currency="HKD"
            )

    def test_non_positive_price_is_a_violation_not_an_exception(self):
        result = validate_securities_order("D05", OrderSide.BUY, 100, price="0")
        self.assertEqual(result.status, STATUS_INVALID_PRICE)
        self.assertIsNone(result.order_notional)

    def test_fractional_quantity_is_rejected(self):
        result = validate_securities_order("D05", OrderSide.BUY, 100.5, price="45.60")
        self.assertEqual(result.status, STATUS_INVALID_QUANTITY)


if __name__ == "__main__":
    unittest.main()
