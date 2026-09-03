import unittest
from decimal import Decimal

from taiwan_stock_exchange_twse_api import (
    SECURITY_CLASS_EQUITY,
    SECURITY_CLASS_ETF_REIT,
    SECURITY_CLASS_ETN,
    SECURITY_CLASS_WARRANT,
    SESSION_AFTER_HOURS_ODD_LOT,
    SESSION_CLOSING_CALL_AUCTION,
    SESSION_CONTINUOUS,
    SESSION_INTRADAY_ODD_LOT,
    SESSION_OPENING_CALL_AUCTION,
    STATUS_VALIDATED,
    TICKET_CASH,
    TICKET_MARGIN_LONG,
    TICKET_MARGIN_SHORT,
    TICKET_SBL_SHORT,
    REASON_CREDIT_TICKET_NOT_PERMITTED_ODD_LOT,
    REASON_INVALID_ODD_LOT_QUANTITY,
    REASON_INVALID_TICK_SIZE,
    REASON_INVALID_TRADING_UNIT,
    REASON_MARKET_ORDER_NOT_PERMITTED,
    REASON_MISSING_INVESTOR_ID,
    REASON_ODD_LOT_INSTRUMENT_INELIGIBLE,
    REASON_ORDER_TYPE_NOT_IN_SESSION,
    REASON_PRICE_LIMIT_EXCEEDED,
    REASON_SHORT_SALE_BELOW_REFERENCE,
    REASON_TICKET_SIDE_MISMATCH,
    TaiwanStockExchangeTwseEngine,
    TwseOrderPayload,
)

INVESTOR_ID = "FINI-TEST-0001"


def order(**overrides) -> TwseOrderPayload:
    """A valid TSMC round-lot limit order, overridden per test."""
    base = dict(
        symbol="2330",
        side="BUY",
        quantity=1000,
        security_class=SECURITY_CLASS_EQUITY,
        price="100.00",
        reference_price="100.00",
    )
    base.update(overrides)
    return TwseOrderPayload(**base)


class TestTickSchedules(unittest.TestCase):
    """Operating Rules Art. 62. Bands read 「X元至未滿Y元」 -- upper-EXCLUSIVE,
    so a price exactly on a boundary takes the COARSER tick above.
    """

    def setUp(self):
        self.engine = TaiwanStockExchangeTwseEngine(INVESTOR_ID)

    def test_equity_tick_by_band(self):
        cases = [
            ("0.05", "0.01"), ("9.99", "0.01"),
            ("10", "0.05"), ("49.95", "0.05"),
            ("50", "0.10"), ("99.90", "0.10"),
            ("100", "0.50"), ("499.50", "0.50"),
            ("500", "1.00"), ("999", "1.00"),
            ("1000", "5.00"), ("1500", "5.00"),
        ]
        for price, expected in cases:
            with self.subTest(price=price):
                self.assertEqual(
                    self.engine.get_tick_size(price, SECURITY_CLASS_EQUITY),
                    Decimal(expected),
                )

    def test_etf_and_etn_share_the_two_tier_schedule(self):
        for security_class in (SECURITY_CLASS_ETF_REIT, SECURITY_CLASS_ETN):
            with self.subTest(security_class=security_class):
                self.assertEqual(
                    self.engine.get_tick_size("49.99", security_class), Decimal("0.01")
                )
                self.assertEqual(
                    self.engine.get_tick_size("50", security_class), Decimal("0.05")
                )
                self.assertEqual(
                    self.engine.get_tick_size("1200", security_class), Decimal("0.05")
                )

    def test_warrant_schedule_breaks_at_five(self):
        cases = [
            ("4.99", "0.01"), ("5", "0.05"), ("9.99", "0.05"),
            ("10", "0.10"), ("49.90", "0.10"), ("50", "0.50"),
            ("99.50", "0.50"), ("100", "1.00"), ("499", "1.00"),
            ("500", "5.00"),
        ]
        for price, expected in cases:
            with self.subTest(price=price):
                self.assertEqual(
                    self.engine.get_tick_size(price, SECURITY_CLASS_WARRANT),
                    Decimal(expected),
                )

    def test_equity_is_not_the_etf_two_tier_table(self):
        """Regression: the pre-audit engine used the ETF table for every
        instrument. Under it a stock at NT$1,102.50 validated; the real
        NT$1,000-and-above equity tick is NT$5, so it is off-grid.
        """
        self.assertFalse(
            self.engine.is_price_on_tick("1102.50", SECURITY_CLASS_EQUITY)
        )
        self.assertTrue(self.engine.is_price_on_tick("1105.00", SECURITY_CLASS_EQUITY))
        # NT$44.66 is legal for an ETF and illegal for a stock.
        self.assertTrue(self.engine.is_price_on_tick("44.66", SECURITY_CLASS_ETF_REIT))
        self.assertFalse(self.engine.is_price_on_tick("44.66", SECURITY_CLASS_EQUITY))

    def test_boundary_prices_take_the_coarser_tick(self):
        # 49.95 is legal (NT$0.05 band); 50.05 is not (NT$0.10 band starts at 50).
        self.assertTrue(self.engine.is_price_on_tick("49.95", SECURITY_CLASS_EQUITY))
        self.assertFalse(self.engine.is_price_on_tick("50.05", SECURITY_CLASS_EQUITY))
        self.assertTrue(self.engine.is_price_on_tick("50.10", SECURITY_CLASS_EQUITY))


class TestDailyPriceLimitBounds(unittest.TestCase):
    """Operating Rules Art. 63 (+/-10% since 1 June 2015) read together with
    Art. 62: the computed bound is snapped onto the tick grid TOWARD the
    reference price, because the outward tick would breach the band.
    """

    def setUp(self):
        self.engine = TaiwanStockExchangeTwseEngine(INVESTOR_ID)

    def test_twse_published_worked_example(self):
        """TWSE's own example: reference 40.60 -> limit-up 44.65, limit-down
        36.55. The naive arithmetic gives 44.66 / 36.54, and the nearer
        outward ticks 44.70 / 36.50 both breach 10%.
        """
        low, high = self.engine.get_daily_price_limit_bounds("40.60")
        self.assertEqual(high, Decimal("44.65"))
        self.assertEqual(low, Decimal("36.55"))

    def test_naive_percentage_band_would_accept_an_illegal_price(self):
        """Regression guard. 44.70 is inside 40.60 x 1.1 only if you ignore
        that it is above the true limit-up; 44.66 is inside the percentage but
        off the NT$0.05 grid. Both must be rejected for an equity.
        """
        for bad_price in ("44.70", "44.66"):
            with self.subTest(price=bad_price):
                report = self.engine.validate_and_route_order(
                    order(price=bad_price, reference_price="40.60")
                )
                self.assertFalse(report.accepted)
                self.assertIn(
                    report.status,
                    (REASON_PRICE_LIMIT_EXCEEDED, REASON_INVALID_TICK_SIZE),
                )
        report = self.engine.validate_and_route_order(
            order(price="44.65", reference_price="40.60")
        )
        self.assertTrue(report.accepted)

    def test_bounds_are_inclusive(self):
        low, high = self.engine.get_daily_price_limit_bounds("100")
        self.assertEqual((low, high), (Decimal("90.00"), Decimal("110.00")))
        for price in ("110.00", "90.00"):
            with self.subTest(price=price):
                self.assertTrue(
                    self.engine.validate_and_route_order(
                        order(price=price, reference_price="100")
                    ).accepted
                )
        report = self.engine.validate_and_route_order(
            order(price="110.50", reference_price="100")
        )
        self.assertFalse(report.accepted)
        self.assertEqual(report.status, REASON_PRICE_LIMIT_EXCEEDED)
        self.assertEqual(report.limit_up_price, Decimal("110.00"))

    def test_snapping_across_a_band_boundary(self):
        """11.11 x 0.9 = 9.999. Rounded up on the NT$0.01 tick that applies
        below NT$10 it becomes 10.00, which sits in the NT$0.05 band -- and
        10.00 is a legal price there, so the snap is stable.
        """
        low, high = self.engine.get_daily_price_limit_bounds("11.11")
        self.assertEqual(low, Decimal("10.00"))
        self.assertEqual(high, Decimal("12.20"))  # 12.221 floored on NT$0.05

    def test_high_priced_equity_uses_the_five_dollar_tick(self):
        low, high = self.engine.get_daily_price_limit_bounds("1075")
        self.assertEqual(high, Decimal("1180.00"))  # 1182.5 floored on NT$5
        self.assertEqual(low, Decimal("968.00"))    # 967.5 raised on NT$1

    def test_sub_cent_amount_counts_as_one_cent(self):
        """「升降幅度經換算後，未滿一分者，以一分計」: 0.05 x 10% = 0.005."""
        low, high = self.engine.get_daily_price_limit_bounds(
            "0.05", SECURITY_CLASS_WARRANT
        )
        self.assertEqual((low, high), (Decimal("0.04"), Decimal("0.06")))

    def test_price_never_falls_below_one_cent(self):
        low, _ = self.engine.get_daily_price_limit_bounds(
            "0.01", SECURITY_CLASS_WARRANT
        )
        self.assertEqual(low, Decimal("0.01"))

    def test_security_class_changes_the_band(self):
        """Same reference price, different grid: an ETF keeps NT$0.01."""
        low, high = self.engine.get_daily_price_limit_bounds(
            "40.60", SECURITY_CLASS_ETF_REIT
        )
        self.assertEqual((low, high), (Decimal("36.54"), Decimal("44.66")))

    def test_leveraged_etf_multiplies_the_percentage(self):
        low, high = self.engine.get_daily_price_limit_bounds(
            "20.00", SECURITY_CLASS_ETF_REIT, Decimal("20")
        )
        self.assertEqual((low, high), (Decimal("16.00"), Decimal("24.00")))


class TestTradingUnitAndOddLot(unittest.TestCase):
    def setUp(self):
        self.engine = TaiwanStockExchangeTwseEngine(INVESTOR_ID)

    def test_regular_session_requires_a_thousand_share_multiple(self):
        report = self.engine.validate_and_route_order(order(quantity=500))
        self.assertFalse(report.accepted)
        self.assertEqual(report.status, REASON_INVALID_TRADING_UNIT)
        self.assertTrue(self.engine.validate_and_route_order(order(quantity=2000)).accepted)

    def test_secondary_listing_is_not_bound_to_one_thousand(self):
        """Secondary listings of foreign stocks and offshore ETFs are
        explicitly 「不以1,000股(單位)為限」.
        """
        report = self.engine.validate_and_route_order(
            order(symbol="9110", quantity=137, trading_unit=1)
        )
        self.assertTrue(report.accepted)

    def test_odd_lot_quantity_window(self):
        for quantity, accepted in ((1, True), (999, True), (1000, False)):
            with self.subTest(quantity=quantity):
                report = self.engine.validate_and_route_order(
                    order(quantity=quantity, session=SESSION_INTRADAY_ODD_LOT)
                )
                self.assertEqual(report.accepted, accepted)
                if not accepted:
                    self.assertEqual(report.status, REASON_INVALID_ODD_LOT_QUANTITY)

    def test_warrants_and_etns_may_not_trade_odd_lot(self):
        for security_class in (SECURITY_CLASS_WARRANT, SECURITY_CLASS_ETN):
            for session in (SESSION_INTRADAY_ODD_LOT, SESSION_AFTER_HOURS_ODD_LOT):
                with self.subTest(security_class=security_class, session=session):
                    report = self.engine.validate_and_route_order(
                        order(
                            symbol="030123",
                            quantity=100,
                            security_class=security_class,
                            session=session,
                            price="10.00",
                            reference_price="10.00",
                        )
                    )
                    self.assertFalse(report.accepted)
                    self.assertEqual(
                        report.status, REASON_ODD_LOT_INSTRUMENT_INELIGIBLE
                    )

    def test_odd_lot_is_cash_only(self):
        """「不得使用信用交易及借券賣出」 -- margin and SBL are barred from both
        odd-lot sessions, so an odd-lot short is never valid.
        """
        for ticket in (TICKET_MARGIN_LONG, TICKET_MARGIN_SHORT, TICKET_SBL_SHORT):
            with self.subTest(ticket=ticket):
                report = self.engine.validate_and_route_order(
                    order(
                        side="BUY" if ticket == TICKET_MARGIN_LONG else "SELL",
                        quantity=100,
                        session=SESSION_INTRADAY_ODD_LOT,
                        ticket_type=ticket,
                    )
                )
                self.assertFalse(report.accepted)
                self.assertEqual(
                    report.status, REASON_CREDIT_TICKET_NOT_PERMITTED_ODD_LOT
                )


class TestSessionOrderTypeMatrix(unittest.TestCase):
    def setUp(self):
        self.engine = TaiwanStockExchangeTwseEngine(INVESTOR_ID)

    def test_market_ioc_fok_exist_only_in_the_continuous_session(self):
        for session in (
            SESSION_OPENING_CALL_AUCTION,
            SESSION_CLOSING_CALL_AUCTION,
            SESSION_INTRADAY_ODD_LOT,
            SESSION_AFTER_HOURS_ODD_LOT,
        ):
            for order_type, tif in (("MARKET", "ROD"), ("LIMIT", "IOC"), ("LIMIT", "FOK")):
                with self.subTest(session=session, order_type=order_type, tif=tif):
                    report = self.engine.validate_and_route_order(
                        order(
                            quantity=100 if "ODD_LOT" in session else 1000,
                            session=session,
                            order_type=order_type,
                            time_in_force=tif,
                        )
                    )
                    self.assertFalse(report.accepted)
                    self.assertEqual(report.status, REASON_ORDER_TYPE_NOT_IN_SESSION)

    def test_limit_rod_is_accepted_in_every_session(self):
        for session in (
            SESSION_OPENING_CALL_AUCTION,
            SESSION_CONTINUOUS,
            SESSION_CLOSING_CALL_AUCTION,
            SESSION_INTRADAY_ODD_LOT,
            SESSION_AFTER_HOURS_ODD_LOT,
        ):
            with self.subTest(session=session):
                report = self.engine.validate_and_route_order(
                    order(quantity=100 if "ODD_LOT" in session else 1000, session=session)
                )
                self.assertTrue(report.accepted, report.reason)

    def test_continuous_session_accepts_all_six_combinations(self):
        for order_type in ("LIMIT", "MARKET"):
            for tif in ("ROD", "IOC", "FOK"):
                with self.subTest(order_type=order_type, tif=tif):
                    self.assertTrue(
                        self.engine.validate_and_route_order(
                            order(
                                order_type=order_type,
                                time_in_force=tif,
                                price=None if order_type == "MARKET" else "100.00",
                            )
                        ).accepted
                    )

    def test_market_order_carrying_a_price_is_a_caller_bug(self):
        """A TWSE market order has no price field, and 限價不可改為市價. A price
        on a market order means the caller confused the two -- raise rather
        than silently discard it.
        """
        with self.assertRaises(ValueError):
            self.engine.validate_and_route_order(
                order(order_type="MARKET", price="999999")
            )

    def test_market_orders_barred_where_there_is_no_price_limit(self):
        report = self.engine.validate_and_route_order(
            order(order_type="MARKET", price=None, price_limit_exempt=True)
        )
        self.assertFalse(report.accepted)
        self.assertEqual(report.status, REASON_MARKET_ORDER_NOT_PERMITTED)

    def test_limit_order_on_an_exempt_security_skips_the_band(self):
        """A new listing's first five sessions have no price limit, so a price
        far from the reference is legal -- but it still has to be on the grid.
        """
        report = self.engine.validate_and_route_order(
            order(price="500.00", reference_price="100.00", price_limit_exempt=True)
        )
        self.assertTrue(report.accepted, report.reason)
        self.assertIsNone(report.limit_up_price)

        off_grid = self.engine.validate_and_route_order(
            order(price="500.25", reference_price="100.00", price_limit_exempt=True)
        )
        self.assertFalse(off_grid.accepted)
        self.assertEqual(off_grid.status, REASON_INVALID_TICK_SIZE)


class TestShortSaleRules(unittest.TestCase):
    """TWSE has no US-style locate. The live constraint is 平盤以下: a short may
    not be priced BELOW the day's auction reference price unless the security
    is on that day's 平盤下得融(借)券賣出 list.
    """

    def setUp(self):
        self.engine = TaiwanStockExchangeTwseEngine(INVESTOR_ID)

    def _short(self, **overrides):
        return order(side="SELL", ticket_type=TICKET_SBL_SHORT, **overrides)

    def test_short_at_the_reference_price_is_allowed(self):
        report = self.engine.validate_and_route_order(
            self._short(price="100.00", reference_price="100.00")
        )
        self.assertTrue(report.accepted, report.reason)

    def test_short_below_the_reference_price_is_restricted(self):
        report = self.engine.validate_and_route_order(
            self._short(price="99.50", reference_price="100.00")
        )
        self.assertFalse(report.accepted)
        self.assertEqual(report.status, REASON_SHORT_SALE_BELOW_REFERENCE)

    def test_short_below_reference_allowed_when_on_the_daily_list(self):
        report = self.engine.validate_and_route_order(
            self._short(
                price="99.50",
                reference_price="100.00",
                below_reference_short_sale_permitted=True,
            )
        )
        self.assertTrue(report.accepted, report.reason)

    def test_both_short_ticket_types_are_covered(self):
        for ticket in (TICKET_MARGIN_SHORT, TICKET_SBL_SHORT):
            with self.subTest(ticket=ticket):
                report = self.engine.validate_and_route_order(
                    order(
                        side="SELL",
                        ticket_type=ticket,
                        price="95.00",
                        reference_price="100.00",
                    )
                )
                self.assertFalse(report.accepted)
                self.assertEqual(report.status, REASON_SHORT_SALE_BELOW_REFERENCE)

    def test_plain_cash_sale_below_the_reference_is_untouched(self):
        report = self.engine.validate_and_route_order(
            order(side="SELL", ticket_type=TICKET_CASH, price="95.00")
        )
        self.assertTrue(report.accepted, report.reason)

    def test_market_order_may_not_short_a_restricted_security(self):
        report = self.engine.validate_and_route_order(
            self._short(order_type="MARKET", price=None)
        )
        self.assertFalse(report.accepted)
        self.assertEqual(report.status, REASON_MARKET_ORDER_NOT_PERMITTED)

    def test_market_order_may_short_a_listed_security(self):
        report = self.engine.validate_and_route_order(
            self._short(
                order_type="MARKET",
                price=None,
                below_reference_short_sale_permitted=True,
            )
        )
        self.assertTrue(report.accepted, report.reason)

    def test_short_ticket_requires_sell_side(self):
        report = self.engine.validate_and_route_order(
            order(side="BUY", ticket_type=TICKET_SBL_SHORT)
        )
        self.assertFalse(report.accepted)
        self.assertEqual(report.status, REASON_TICKET_SIDE_MISMATCH)

    def test_margin_long_requires_buy_side(self):
        report = self.engine.validate_and_route_order(
            order(side="SELL", ticket_type=TICKET_MARGIN_LONG)
        )
        self.assertFalse(report.accepted)
        self.assertEqual(report.status, REASON_TICKET_SIDE_MISMATCH)


class TestInvestorId(unittest.TestCase):
    def test_engine_has_no_fabricated_default(self):
        """Regression: the pre-audit Config shipped a hard-coded FINI ID, so
        the check could never fire and orders carried an invented regulatory
        identifier.
        """
        self.assertIsNone(TaiwanStockExchangeTwseEngine().investor_id)

    def test_order_is_rejected_without_an_investor_id(self):
        report = TaiwanStockExchangeTwseEngine().validate_and_route_order(order())
        self.assertFalse(report.accepted)
        self.assertEqual(report.status, REASON_MISSING_INVESTOR_ID)

    def test_payload_id_overrides_the_engine_default(self):
        report = TaiwanStockExchangeTwseEngine().validate_and_route_order(
            order(investor_id="FINI-PER-ORDER")
        )
        self.assertTrue(report.accepted, report.reason)

    def test_blank_investor_id_is_rejected_at_construction(self):
        with self.assertRaises(ValueError):
            TaiwanStockExchangeTwseEngine("   ")


class TestRejectionReportsCarryRepriceHints(unittest.TestCase):
    def setUp(self):
        self.engine = TaiwanStockExchangeTwseEngine(INVESTOR_ID)

    def test_tick_rejection_reports_both_neighbours(self):
        report = self.engine.validate_and_route_order(
            order(price="100.30", reference_price="100.00")
        )
        self.assertFalse(report.accepted)
        self.assertEqual(report.status, REASON_INVALID_TICK_SIZE)
        self.assertEqual(report.tick_size, Decimal("0.50"))
        self.assertEqual(report.nearest_valid_price_below, Decimal("100.00"))
        self.assertEqual(report.nearest_valid_price_above, Decimal("100.50"))

    def test_accepted_report_carries_the_band(self):
        report = self.engine.validate_and_route_order(order())
        self.assertEqual(report.status, STATUS_VALIDATED)
        self.assertEqual(report.limit_up_price, Decimal("110.00"))
        self.assertEqual(report.limit_down_price, Decimal("90.00"))
        self.assertEqual(report.tick_size, Decimal("0.50"))

    def test_sub_cent_price_reports_no_unquotable_neighbour(self):
        """Snapping NT$0.005 down lands on NT$0.00, which cannot be quoted."""
        report = self.engine.validate_and_route_order(
            order(price="0.005", reference_price="0.05")
        )
        self.assertEqual(report.status, REASON_INVALID_TICK_SIZE)
        self.assertIsNone(report.nearest_valid_price_below)
        self.assertEqual(report.nearest_valid_price_above, Decimal("0.01"))

    def test_client_order_id_is_echoed(self):
        report = self.engine.validate_and_route_order(order(client_order_id="abc-1"))
        self.assertEqual(report.client_order_id, "abc-1")


class TestInputGuards(unittest.TestCase):
    """Malformed input raises; only exchange-rule breaches become reports."""

    def setUp(self):
        self.engine = TaiwanStockExchangeTwseEngine(INVESTOR_ID)

    def test_non_finite_and_non_positive_prices_raise(self):
        for kwargs in (
            dict(price=float("nan")),
            dict(price=float("inf")),
            dict(price="0"),
            dict(price="-1"),
            dict(reference_price="0"),
            dict(reference_price=float("nan")),
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    self.engine.validate_and_route_order(order(**kwargs))

    def test_unknown_enumerations_raise(self):
        for kwargs in (
            dict(side="SHORT_SELL"),
            dict(order_type="STOP"),
            dict(time_in_force="ROH"),
            dict(session="AFTER_HOURS_FIXED_PRICE"),
            dict(ticket_type="NAKED_SHORT"),
            dict(security_class="BOND"),
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    self.engine.validate_and_route_order(order(**kwargs))

    def test_quantity_guards(self):
        for quantity in (0, -1000, 1000.0, True):
            with self.subTest(quantity=quantity):
                with self.assertRaises(ValueError):
                    self.engine.validate_and_route_order(order(quantity=quantity))

    def test_symbol_guards(self):
        for symbol in ("", "23", "2330456789", "23-0", None):
            with self.subTest(symbol=symbol):
                with self.assertRaises(ValueError):
                    self.engine.validate_and_route_order(order(symbol=symbol))

    def test_real_twse_codes_are_accepted(self):
        for symbol, security_class in (
            ("2330", SECURITY_CLASS_EQUITY),
            ("0050", SECURITY_CLASS_ETF_REIT),
            ("006208", SECURITY_CLASS_ETF_REIT),
            ("00679B", SECURITY_CLASS_ETF_REIT),
            ("00400A", SECURITY_CLASS_ETF_REIT),
        ):
            with self.subTest(symbol=symbol):
                report = self.engine.validate_and_route_order(
                    order(symbol=symbol, security_class=security_class)
                )
                self.assertTrue(report.accepted, report.reason)

    def test_limit_order_without_a_price_raises(self):
        with self.assertRaises(ValueError):
            self.engine.validate_and_route_order(order(price=None))

    def test_missing_reference_price_raises_unless_exempt(self):
        with self.assertRaises(ValueError):
            self.engine.validate_and_route_order(order(reference_price=None))
        self.assertTrue(
            self.engine.validate_and_route_order(
                order(reference_price=None, price_limit_exempt=True)
            ).accepted
        )

    def test_exempt_short_sale_still_needs_a_reference_price(self):
        """No price band does not mean no 平盤 -- the short-sale rule is still
        measured against the reference price.
        """
        with self.assertRaises(ValueError):
            self.engine.validate_and_route_order(
                order(
                    side="SELL",
                    ticket_type=TICKET_SBL_SHORT,
                    reference_price=None,
                    price_limit_exempt=True,
                )
            )

    def test_float_prices_do_not_drift_off_grid(self):
        """0.1 + 0.2 style binary error must not turn a legal price illegal."""
        self.assertTrue(self.engine.is_price_on_tick(44.65, SECURITY_CLASS_EQUITY))
        self.assertTrue(self.engine.is_price_on_tick(550.00, SECURITY_CLASS_EQUITY))
        self.assertFalse(self.engine.is_price_on_tick(550.30, SECURITY_CLASS_EQUITY))


if __name__ == "__main__":
    unittest.main()
