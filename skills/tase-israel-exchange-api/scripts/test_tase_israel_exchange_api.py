import datetime
import unittest

from tase_israel_exchange_api import (
    ORDER_ENTRY_PHASES,
    TRADING_WEEK_CHANGE_DATE,
    InstrumentType,
    MarketPhase,
    OrderSide,
    OrderStatus,
    OrderType,
    PriceDenomination,
    TASEConfig,
    TASEConfigurationError,
    TASEConnectionError,
    TASEIntegrationEngine,
    TASEMarketClosedError,
    TASEOrder,
    TASERiskLimitError,
    TASESecurity,
    TASESessionSchedule,
    TASEValidationError,
)

UTC = datetime.timezone.utc

# Monday 2026-08-03 12:00 Israel local time. Israel is on IDT (UTC+3) in August,
# so 12:00 local is 09:00 UTC. Chosen to sit inside continuous trading.
MONDAY_MIDSESSION_UTC = datetime.datetime(2026, 8, 3, 9, 0, tzinfo=UTC)


def _engine(**config_overrides):
    config = TASEConfig(
        sender_comp_id="SENDER_TEST",
        target_comp_id="TASE_GATEWAY",
        host="127.0.0.1",
        port=9876,
        max_order_value_ils=500_000.0,
        max_order_qty=50_000.0,
        max_price_collar_pct=10.0,
        **config_overrides,
    )
    engine = TASEIntegrationEngine(config)
    engine.register_security(
        TASESecurity(
            symbol="TEVA.TA",
            security_id="1082511",
            isin="IL0001082511",
            instrument_type=InstrumentType.EQUITY,
            price_denomination=PriceDenomination.AGOROT,
            tick_size_agorot=10.0,
            reference_price_ils=35.0,  # 3500 Agorot
        )
    )
    return engine


def _order(**overrides):
    params = dict(
        symbol="TEVA.TA",
        security_id="1082511",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=1000,
        price=3500.0,
        price_denomination=PriceDenomination.AGOROT,
    )
    params.update(overrides)
    return TASEOrder(**params)


class TestSessionCalendar(unittest.TestCase):
    """
    TASE moved from a Sunday-Thursday to a Monday-Friday week on 2026-01-05.
    These are the regression tests for that change: each one fails against the
    old hard-coded Sunday-Thursday / UTC+2 implementation.
    """

    def setUp(self):
        self.engine = _engine()

    def test_friday_is_a_trading_day_under_current_schedule(self):
        # Friday 2026-08-07, 11:00 local (IDT) == 08:00 UTC. Friday is a live
        # session since the 2026 change; the legacy calendar returned CLOSED.
        friday = datetime.datetime(2026, 8, 7, 8, 0, tzinfo=UTC)
        self.assertEqual(
            self.engine.get_market_phase(friday), MarketPhase.CONTINUOUS_TRADING
        )

    def test_sunday_is_closed_under_current_schedule(self):
        # Sunday 2026-08-09, 13:00 local (IDT) == 10:00 UTC. Sunday ceased to be
        # a trading day in 2026; the legacy calendar returned CONTINUOUS_TRADING.
        sunday = datetime.datetime(2026, 8, 9, 10, 0, tzinfo=UTC)
        self.assertEqual(self.engine.get_market_phase(sunday), MarketPhase.CLOSED)

    def test_friday_closes_early_before_shabbat(self):
        # Friday 2026-08-07, 14:30 local (IDT) == 11:30 UTC, past the 13:50 close.
        friday_late = datetime.datetime(2026, 8, 7, 11, 30, tzinfo=UTC)
        self.assertEqual(self.engine.get_market_phase(friday_late), MarketPhase.CLOSED)
        # ... while a Monday at the same local time is still trading.
        monday_same_local = datetime.datetime(2026, 8, 3, 11, 30, tzinfo=UTC)
        self.assertEqual(
            self.engine.get_market_phase(monday_same_local),
            MarketPhase.CONTINUOUS_TRADING,
        )

    def test_daylight_saving_is_applied_not_a_fixed_utc2_offset(self):
        """
        17:00 Israel local on a Monday is inside continuous trading (close is
        17:15). In August, Israel is on IDT (UTC+3), so that instant is 14:00 UTC.

        A fixed UTC+2 implementation reads 14:00 UTC as 16:00 local and lands in
        the same phase by luck, so the discriminating case is the boundary: at
        14:20 UTC the true local time is 17:20 (closing auction), while UTC+2
        computes 16:20 (continuous trading).
        """
        # Sanity-check the premise: Israel really is UTC+3 on this date.
        israel_offset = (
            datetime.datetime(2026, 8, 3, 14, 20, tzinfo=UTC)
            .astimezone(self.engine._tz)
            .utcoffset()
        )
        self.assertEqual(israel_offset, datetime.timedelta(hours=3))

        boundary = datetime.datetime(2026, 8, 3, 14, 20, tzinfo=UTC)
        self.assertEqual(
            self.engine.get_market_phase(boundary), MarketPhase.CLOSING_AUCTION
        )

    def test_winter_standard_time_is_utc_plus_two(self):
        # January is IST (UTC+2): 15:20 UTC == 17:20 local -> closing auction.
        winter = datetime.datetime(2026, 1, 12, 15, 20, tzinfo=UTC)
        self.assertEqual(
            _engine().get_market_phase(winter), MarketPhase.CLOSING_AUCTION
        )

    def test_all_phase_boundaries_monday(self):
        """Walk every boundary of a Monday session in Israel local time."""
        tz = self.engine._tz
        cases = [
            (datetime.time(9, 24), MarketPhase.CLOSED),
            (datetime.time(9, 25), MarketPhase.PRE_OPEN),
            (datetime.time(9, 58), MarketPhase.PRE_OPEN),
            (datetime.time(9, 59), MarketPhase.OPENING_AUCTION),
            (datetime.time(10, 0), MarketPhase.CONTINUOUS_TRADING),
            (datetime.time(17, 14), MarketPhase.CONTINUOUS_TRADING),
            (datetime.time(17, 15), MarketPhase.CLOSING_AUCTION),
            (datetime.time(17, 24), MarketPhase.CLOSING_AUCTION),
            (datetime.time(17, 25), MarketPhase.CLOSED),
        ]
        for local_time, expected in cases:
            with self.subTest(local_time=local_time):
                local_dt = datetime.datetime.combine(
                    datetime.date(2026, 8, 3), local_time, tzinfo=tz
                )
                self.assertEqual(self.engine.get_market_phase(local_dt), expected)

    def test_naive_datetime_is_rejected(self):
        with self.assertRaises(TASEValidationError):
            self.engine.get_market_phase(datetime.datetime(2026, 8, 3, 12, 0))

    def test_holidays_close_the_market(self):
        holiday = datetime.date(2026, 9, 14)  # Monday, supplied by the caller.
        engine = _engine(
            session_schedule=TASESessionSchedule.current(holidays=frozenset({holiday}))
        )
        at_midday = datetime.datetime(2026, 9, 14, 9, 0, tzinfo=UTC)
        self.assertEqual(engine.get_market_phase(at_midday), MarketPhase.CLOSED)

    def test_for_date_selects_the_regime_actually_in_force(self):
        before = TASESessionSchedule.for_date(datetime.date(2025, 6, 1))
        after = TASESessionSchedule.for_date(datetime.date(2026, 6, 1))
        # Sunday (weekday 6) traded before the change, not after.
        self.assertIn(6, before.trading_weekdays)
        self.assertNotIn(6, after.trading_weekdays)
        # Friday (weekday 4) trades after the change, not before.
        self.assertNotIn(4, before.trading_weekdays)
        self.assertIn(4, after.trading_weekdays)

    def test_for_date_boundary_is_the_change_date_itself(self):
        day_before = TRADING_WEEK_CHANGE_DATE - datetime.timedelta(days=1)
        self.assertIn(6, TASESessionSchedule.for_date(day_before).trading_weekdays)
        self.assertIn(4, TASESessionSchedule.for_date(TRADING_WEEK_CHANGE_DATE).trading_weekdays)

    def test_legacy_schedule_still_trades_sunday(self):
        engine = _engine(session_schedule=TASESessionSchedule.legacy_sunday_thursday())
        # Sunday 2025-08-10, 13:00 local (IDT) == 10:00 UTC.
        sunday = datetime.datetime(2025, 8, 10, 10, 0, tzinfo=UTC)
        self.assertEqual(
            engine.get_market_phase(sunday), MarketPhase.CONTINUOUS_TRADING
        )

    def test_schedule_rejects_out_of_order_boundaries(self):
        with self.assertRaises(TASEConfigurationError):
            TASESessionSchedule(
                trading_weekdays=frozenset({0}),
                pre_open=datetime.time(10, 0),
                opening_auction=datetime.time(9, 0),  # before pre_open
                continuous_open=datetime.time(11, 0),
                closing_auction=datetime.time(17, 0),
                close=datetime.time(17, 30),
            )

    def test_schedule_rejects_short_weekday_outside_trading_week(self):
        with self.assertRaises(TASEConfigurationError):
            TASESessionSchedule(
                trading_weekdays=frozenset({0, 1}),
                pre_open=datetime.time(9, 0),
                opening_auction=datetime.time(9, 30),
                continuous_open=datetime.time(10, 0),
                closing_auction=datetime.time(17, 0),
                close=datetime.time(17, 30),
                short_weekdays=frozenset({4}),  # Friday is not a trading day here
                short_closing_auction=datetime.time(13, 0),
                short_close=datetime.time(13, 30),
            )


class TestPriceDenomination(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()

    def test_agorot_ils_conversion(self):
        self.assertEqual(TASEIntegrationEngine.convert_price_agorot_to_ils(3500.0), 35.0)
        self.assertEqual(TASEIntegrationEngine.convert_price_ils_to_agorot(35.0), 3500.0)

    def test_order_value_agorot(self):
        # 1000 shares at 3500 Agorot = 35.00 ILS each = 35,000 ILS.
        self.assertEqual(self.engine.calculate_order_value_ils(_order()), 35_000.0)

    def test_percentage_quoted_bond_uses_par_value(self):
        """
        A bond quoted at 102.5 is 102.5% of par, not 102.5 ILS.

        Independently derived: 50,000 units x 1.00 ILS par x 102.5% = 51,250 ILS.
        The previous implementation returned 102.5 x 50,000 = 5,125,000 ILS -- a
        100x overstatement that falsely tripped the notional cap.
        """
        self.engine.register_security(
            TASESecurity(
                symbol="GOV0328.TA",
                security_id="1140000",
                isin="IL0011400001",
                instrument_type=InstrumentType.BOND,
                price_denomination=PriceDenomination.PERCENTAGE,
                par_value_ils=1.0,
                reference_price_ils=1.02,
            )
        )
        bond = _order(
            symbol="GOV0328.TA",
            security_id="1140000",
            quantity=50_000,
            price=102.5,
            price_denomination=PriceDenomination.PERCENTAGE,
        )
        self.assertAlmostEqual(
            self.engine.calculate_order_value_ils(bond), 51_250.0, places=6
        )

    def test_percentage_security_requires_par_value_at_registration(self):
        with self.assertRaises(TASEValidationError):
            self.engine.register_security(
                TASESecurity(
                    symbol="NOPAR.TA",
                    security_id="9999999",
                    isin="IL0099999999",
                    price_denomination=PriceDenomination.PERCENTAGE,
                )
            )

    def test_denomination_mismatch_against_security_master_is_rejected(self):
        """
        The headline TASE failure mode: an Agorot-quoted equity submitted with an
        ILS price. 35 ILS and 35 Agorot differ by 100x, and every other check
        passes, so only a security-master comparison catches it.
        """
        self.engine.connect()
        mispriced = _order(price=35.0, price_denomination=PriceDenomination.ILS)
        with self.assertRaises(TASEValidationError):
            self.engine.submit_order(mispriced, now=MONDAY_MIDSESSION_UTC)

    def test_percentage_price_without_registered_par_value_raises(self):
        unknown = _order(
            symbol="UNKNOWN.TA", price_denomination=PriceDenomination.PERCENTAGE
        )
        with self.assertRaises(TASEValidationError):
            self.engine.price_to_ils(
                102.5, PriceDenomination.PERCENTAGE, unknown.symbol
            )


class TestPreTradeRisk(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        self.engine.connect()

    def submit(self, order):
        return self.engine.submit_order(order, now=MONDAY_MIDSESSION_UTC)

    def test_valid_limit_order_is_accepted(self):
        order = _order()
        order_id = self.submit(order)
        self.assertIn(order_id, self.engine.orders)
        self.assertEqual(self.engine.orders[order_id].status, OrderStatus.NEW)

    def test_max_quantity_breach(self):
        with self.assertRaises(TASERiskLimitError):
            self.submit(_order(quantity=60_000))

    def test_max_value_breach(self):
        # 20,000 x 35 ILS = 700,000 ILS against a 500,000 ILS cap.
        with self.assertRaises(TASERiskLimitError):
            self.submit(_order(quantity=20_000))

    def test_market_order_notional_is_estimated_not_treated_as_zero(self):
        """
        Regression: a MARKET order has no price. The previous implementation
        substituted 0.0, so its notional was 0 and the max-order-value cap never
        fired -- an unbounded market order passed the control unchallenged.

        20,000 shares x 35 ILS reference = 700,000 ILS, over the 500,000 cap.
        """
        market = _order(order_type=OrderType.MARKET, price=None, quantity=20_000)
        self.assertEqual(self.engine.calculate_order_value_ils(market), 700_000.0)
        with self.assertRaises(TASERiskLimitError):
            self.submit(market)

    def test_market_order_within_cap_is_accepted(self):
        market = _order(order_type=OrderType.MARKET, price=None, quantity=1_000)
        self.assertIsNotNone(self.submit(market))

    def test_market_order_without_reference_price_is_rejected_not_valued_at_zero(self):
        engine = _engine(require_registered_security=False)
        engine.connect()
        market = _order(
            symbol="NOREF.TA", order_type=OrderType.MARKET, price=None, quantity=10
        )
        with self.assertRaises(TASEValidationError):
            engine.submit_order(market, now=MONDAY_MIDSESSION_UTC)

    def test_price_collar_breach(self):
        # Reference 35 ILS; 4500 Agorot = 45 ILS is a 28.57% deviation vs a 10% cap.
        with self.assertRaises(TASERiskLimitError):
            self.submit(_order(price=4500.0))

    def test_price_collar_boundary_is_inclusive(self):
        # Exactly 10% above 35 ILS is 38.50 ILS = 3850 Agorot: allowed at the cap.
        self.assertIsNotNone(self.submit(_order(price=3850.0)))
        # One tick beyond (3860 Agorot = 38.60 ILS, 10.29%) is rejected.
        with self.assertRaises(TASERiskLimitError):
            self.submit(_order(price=3860.0))

    def test_unregistered_security_is_rejected_by_default(self):
        """
        Regression: the previous implementation silently skipped the collar check
        for unknown symbols, so an unregistered instrument had no price control
        at all. An unknown symbol is where a collar is most needed.
        """
        with self.assertRaises(TASEValidationError):
            self.submit(_order(symbol="GHOST.TA"))

    def test_unregistered_security_allowed_when_explicitly_opted_out(self):
        engine = _engine(require_registered_security=False)
        engine.connect()
        # Needs a price, since notional cannot be estimated without a reference.
        order = _order(symbol="GHOST.TA", quantity=10, price=3500.0)
        self.assertIsNotNone(engine.submit_order(order, now=MONDAY_MIDSESSION_UTC))

    def test_tick_size_misalignment_is_rejected(self):
        # Tick size registered as 10 Agorot; 3505 is not a whole multiple.
        with self.assertRaises(TASEValidationError):
            self.submit(_order(price=3505.0))

    def test_tick_size_aligned_price_is_accepted(self):
        self.assertIsNotNone(self.submit(_order(price=3510.0)))

    def test_stop_limit_requires_stop_price(self):
        with self.assertRaises(TASEValidationError):
            self.submit(_order(order_type=OrderType.STOP_LIMIT, stop_price=None))

    def test_stop_limit_with_stop_price_is_accepted(self):
        order = _order(order_type=OrderType.STOP_LIMIT, stop_price=3450.0)
        self.assertIsNotNone(self.submit(order))

    def test_iceberg_display_qty_validation(self):
        with self.assertRaises(TASEValidationError):
            self.submit(_order(order_type=OrderType.ICEBERG, display_qty=1500))
        with self.assertRaises(TASEValidationError):
            self.submit(_order(order_type=OrderType.ICEBERG, display_qty=None))

    def test_iceberg_valid(self):
        order = _order(order_type=OrderType.ICEBERG, display_qty=100)
        self.assertIsNotNone(self.submit(order))

    def test_non_positive_quantity_rejected(self):
        with self.assertRaises(TASEValidationError):
            self.submit(_order(quantity=0))
        with self.assertRaises(TASEValidationError):
            self.submit(_order(quantity=-5))

    def test_limit_order_requires_positive_price(self):
        with self.assertRaises(TASEValidationError):
            self.submit(_order(price=None))
        with self.assertRaises(TASEValidationError):
            self.submit(_order(price=-1.0))


class TestNonFiniteInputs(unittest.TestCase):
    """
    Regression: every pre-trade control in this module is a comparison, and every
    comparison against NaN is False. A NaN quantity therefore passed
    ``quantity <= 0``, passed the quantity cap, and produced a NaN notional that
    passed the value cap and the collar -- one NaN disabled the entire risk layer
    at once. Infinity escaped as OverflowError from the tick arithmetic, which is
    not a TASEError and so bypassed the caller's rejection handling.
    """

    NAN = float("nan")
    INF = float("inf")

    def setUp(self):
        self.engine = _engine()
        self.engine.connect()

    def submit(self, order):
        return self.engine.submit_order(order, now=MONDAY_MIDSESSION_UTC)

    def test_nan_quantity_does_not_bypass_every_limit(self):
        with self.assertRaises(TASEValidationError):
            self.submit(_order(quantity=self.NAN))

    def test_nan_price_rejected_as_tase_error(self):
        with self.assertRaises(TASEValidationError):
            self.submit(_order(price=self.NAN))

    def test_infinite_price_rejected_as_tase_error(self):
        # Previously raised OverflowError out of the tick-alignment arithmetic.
        with self.assertRaises(TASEValidationError):
            self.submit(_order(price=self.INF))

    def test_infinite_quantity_rejected(self):
        with self.assertRaises(TASEValidationError):
            self.submit(_order(quantity=self.INF))

    def test_nan_stop_and_display_qty_rejected(self):
        with self.assertRaises(TASEValidationError):
            self.submit(_order(order_type=OrderType.STOP_LIMIT, stop_price=self.NAN))
        with self.assertRaises(TASEValidationError):
            self.submit(_order(order_type=OrderType.ICEBERG, display_qty=self.NAN))

    def test_nan_defeats_collar_when_unguarded(self):
        """A NaN price must not slip through even with the master check relaxed."""
        engine = _engine(require_registered_security=False)
        engine.connect()
        with self.assertRaises(TASEValidationError):
            engine.submit_order(
                _order(symbol="GHOST.TA", price=self.NAN), now=MONDAY_MIDSESSION_UTC
            )

    def test_non_finite_execution_report_values_rejected(self):
        order_id = self.submit(_order())
        for qty, price in ((self.NAN, 3500.0), (100, self.NAN), (self.INF, 3500.0)):
            with self.subTest(qty=qty, price=price):
                with self.assertRaises(TASEValidationError):
                    self.engine.simulate_execution_report(order_id, qty, price)

    def test_non_finite_security_master_fields_rejected_at_registration(self):
        for kwargs in (
            {"reference_price_ils": self.NAN},
            {"tick_size_agorot": self.NAN},
            {"tick_size_agorot": self.INF},
        ):
            with self.subTest(**kwargs):
                params = dict(
                    symbol="BAD.TA",
                    security_id="1",
                    isin="IL0000000001",
                    tick_size_agorot=10.0,
                    reference_price_ils=35.0,
                )
                params.update(kwargs)
                with self.assertRaises(TASEValidationError):
                    self.engine.register_security(TASESecurity(**params))


class TestFixEncoding(unittest.TestCase):
    """
    Regression tests for the FIX wire codes. The previous implementation encoded
    STOP_LIMIT as tag 40 = "3" (which is Stop / Stop Loss) and ICEBERG as "L"
    (Previous Fund Valuation Point), so serialising ``order_type.value`` into
    tag 40 transmitted a different order type than the caller intended.
    """

    def test_ord_type_codes_match_fix(self):
        self.assertEqual(OrderType.MARKET.fix_ord_type, "1")
        self.assertEqual(OrderType.LIMIT.fix_ord_type, "2")
        self.assertEqual(OrderType.STOP_LIMIT.fix_ord_type, "4")

    def test_iceberg_is_a_limit_order_not_an_ord_type(self):
        # Iceberg is expressed as a limit order plus DisplayQty (tag 1138).
        self.assertEqual(OrderType.ICEBERG.fix_ord_type, "2")

    def test_side_and_status_values_are_fix_codes(self):
        self.assertEqual(OrderSide.BUY.value, "1")
        self.assertEqual(OrderSide.SELL.value, "2")
        self.assertEqual(OrderStatus.NEW.value, "0")
        self.assertEqual(OrderStatus.PARTIALLY_FILLED.value, "1")
        self.assertEqual(OrderStatus.FILLED.value, "2")
        self.assertEqual(OrderStatus.CANCELED.value, "4")
        self.assertEqual(OrderStatus.REJECTED.value, "8")

    def test_every_order_type_has_a_fix_code(self):
        for order_type in OrderType:
            self.assertIn(order_type.fix_ord_type, {"1", "2", "4"})


class TestSessionGate(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        self.engine.connect()

    def test_order_entry_blocked_when_market_closed(self):
        # Sunday: no longer a trading day.
        sunday = datetime.datetime(2026, 8, 9, 10, 0, tzinfo=UTC)
        with self.assertRaises(TASEMarketClosedError):
            self.engine.submit_order(_order(), now=sunday)

    def test_order_entry_allowed_in_pre_open(self):
        # Monday 09:30 local (IDT) == 06:30 UTC.
        pre_open = datetime.datetime(2026, 8, 3, 6, 30, tzinfo=UTC)
        self.assertEqual(self.engine.get_market_phase(pre_open), MarketPhase.PRE_OPEN)
        self.assertIsNotNone(self.engine.submit_order(_order(), now=pre_open))

    def test_calendar_gate_can_be_disabled(self):
        engine = _engine(enforce_session_calendar=False)
        engine.connect()
        sunday = datetime.datetime(2026, 8, 9, 10, 0, tzinfo=UTC)
        self.assertIsNotNone(engine.submit_order(_order(), now=sunday))

    def test_order_entry_phases_exclude_closed(self):
        self.assertNotIn(MarketPhase.CLOSED, ORDER_ENTRY_PHASES)
        self.assertEqual(len(ORDER_ENTRY_PHASES), 4)

    def test_accepts_order_entry_helper(self):
        self.assertTrue(self.engine.accepts_order_entry(MONDAY_MIDSESSION_UTC))
        sunday = datetime.datetime(2026, 8, 9, 10, 0, tzinfo=UTC)
        self.assertFalse(self.engine.accepts_order_entry(sunday))


class TestOrderLifecycle(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        self.engine.connect()

    def submit(self, order):
        return self.engine.submit_order(order, now=MONDAY_MIDSESSION_UTC)

    def test_connect_and_disconnect(self):
        engine = _engine()
        self.assertFalse(engine.is_connected)
        self.assertTrue(engine.connect())
        self.assertTrue(engine.is_connected)
        self.assertTrue(engine.disconnect())
        self.assertFalse(engine.is_connected)
        # A second disconnect is a no-op, not an error.
        self.assertFalse(engine.disconnect())

    def test_invalid_config_rejected_at_connect(self):
        for bad in (
            dict(sender_comp_id="", target_comp_id="TASE", host="h", port=1),
            dict(sender_comp_id="S", target_comp_id="TASE", host="", port=1),
            dict(sender_comp_id="S", target_comp_id="TASE", host="h", port=0),
            dict(sender_comp_id="S", target_comp_id="TASE", host="h", port=-1),
            dict(sender_comp_id="S", target_comp_id="TASE", host="h", port=70000),
        ):
            with self.subTest(**bad):
                engine = TASEIntegrationEngine(TASEConfig(**bad))
                with self.assertRaises(TASEValidationError):
                    engine.connect()

    def test_submit_while_disconnected_raises(self):
        engine = _engine()
        with self.assertRaises(TASEConnectionError):
            engine.submit_order(_order(), now=MONDAY_MIDSESSION_UTC)

    def test_cancel_while_disconnected_raises(self):
        order_id = self.submit(_order())
        self.engine.disconnect()
        with self.assertRaises(TASEConnectionError):
            self.engine.cancel_order(order_id)

    def test_duplicate_client_order_id_is_rejected(self):
        """
        The client order id is the idempotency key. Re-submitting one already
        tracked would overwrite the original's fill state, losing the record of
        an order that may well be live at the venue.
        """
        order = _order()
        self.submit(order)
        duplicate = _order(client_order_id=order.client_order_id)
        with self.assertRaises(TASEValidationError):
            self.submit(duplicate)

    def test_cancel_lifecycle(self):
        order_id = self.submit(_order(quantity=500))
        self.assertTrue(self.engine.cancel_order(order_id))
        self.assertEqual(self.engine.orders[order_id].status, OrderStatus.CANCELED)
        self.assertFalse(self.engine.cancel_order(order_id))

    def test_cancel_unknown_order_returns_false(self):
        self.assertFalse(self.engine.cancel_order("no-such-id"))

    def test_partial_then_full_fill_vwap(self):
        order_id = self.submit(_order(quantity=1000))

        updated = self.engine.simulate_execution_report(order_id, 400, 3490.0)
        self.assertEqual(updated.status, OrderStatus.PARTIALLY_FILLED)
        self.assertEqual(updated.filled_quantity, 400)
        self.assertEqual(updated.average_price, 3490.0)

        updated = self.engine.simulate_execution_report(order_id, 600, 3510.0)
        self.assertEqual(updated.status, OrderStatus.FILLED)
        self.assertEqual(updated.filled_quantity, 1000)
        # Independently derived: (400*3490 + 600*3510) / 1000
        #                      = (1,396,000 + 2,106,000) / 1000 = 3502.0
        self.assertAlmostEqual(updated.average_price, 3502.0, places=9)

    def test_three_way_fill_vwap(self):
        order_id = self.submit(_order(quantity=1000))
        self.engine.simulate_execution_report(order_id, 200, 3400.0)
        self.engine.simulate_execution_report(order_id, 300, 3500.0)
        updated = self.engine.simulate_execution_report(order_id, 500, 3600.0)
        # (200*3400 + 300*3500 + 500*3600) / 1000
        # = (680,000 + 1,050,000 + 1,800,000) / 1000 = 3530.0
        self.assertAlmostEqual(updated.average_price, 3530.0, places=9)
        self.assertEqual(updated.status, OrderStatus.FILLED)

    def test_overfill_is_rejected(self):
        order_id = self.submit(_order(quantity=1000))
        self.engine.simulate_execution_report(order_id, 900, 3500.0)
        with self.assertRaises(TASEValidationError):
            self.engine.simulate_execution_report(order_id, 200, 3500.0)

    def test_fractional_fills_reach_filled_despite_float_error(self):
        """
        Regression: three fills of 1/3 of the quantity do not sum to exactly the
        quantity in binary floating point. An exact comparison left the order
        stuck in PARTIALLY_FILLED (or raised on overfill); the tolerance fixes it.
        """
        order = _order(quantity=1.0)
        order_id = self.submit(order)
        third = 1.0 / 3.0
        self.engine.simulate_execution_report(order_id, third, 3500.0)
        self.engine.simulate_execution_report(order_id, third, 3500.0)
        updated = self.engine.simulate_execution_report(order_id, third, 3500.0)
        self.assertEqual(updated.status, OrderStatus.FILLED)

    def test_non_positive_fill_quantity_rejected(self):
        order_id = self.submit(_order())
        with self.assertRaises(TASEValidationError):
            self.engine.simulate_execution_report(order_id, 0, 3500.0)
        with self.assertRaises(TASEValidationError):
            self.engine.simulate_execution_report(order_id, -10, 3500.0)

    def test_non_positive_execution_price_rejected(self):
        """
        Regression: a zero or negative fill price silently corrupted the running
        average, producing a VWAP no downstream P&L check would flag as invalid.
        """
        order_id = self.submit(_order())
        for bad_price in (0.0, -3500.0):
            with self.subTest(price=bad_price):
                with self.assertRaises(TASEValidationError):
                    self.engine.simulate_execution_report(order_id, 100, bad_price)

    def test_execution_report_for_unknown_order_returns_none(self):
        self.assertIsNone(
            self.engine.simulate_execution_report("no-such-id", 100, 3500.0)
        )

    def test_execution_report_ignored_after_cancel(self):
        order_id = self.submit(_order())
        self.engine.cancel_order(order_id)
        updated = self.engine.simulate_execution_report(order_id, 100, 3500.0)
        self.assertEqual(updated.status, OrderStatus.CANCELED)
        self.assertEqual(updated.filled_quantity, 0.0)


if __name__ == "__main__":
    unittest.main()
