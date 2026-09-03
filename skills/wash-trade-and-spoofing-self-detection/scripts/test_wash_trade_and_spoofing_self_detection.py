import datetime
import unittest

from wash_trade_and_spoofing_self_detection import (
    AlertType,
    OrderAction,
    OrderEvent,
    OrderSide,
    PatternShape,
    SurveillanceAlert,
    SurveillanceError,
    SurveillanceViolation,
    TraderMetrics,
    ViolationType,
    WashTradeAndSpoofingDetectionEngine,
)

UTC = datetime.timezone.utc
T0 = datetime.datetime(2026, 3, 2, 14, 30, 0, tzinfo=UTC)


def ev(
    event_id,
    order_id,
    action,
    side,
    *,
    price=None,
    quantity=100.0,
    trader_id="T1",
    account_id="ACC1",
    symbol="AAPL",
    strategy_id=None,
    offset_ms=0.0,
):
    """Build an OrderEvent at ``T0 + offset_ms``."""
    return OrderEvent(
        event_id=event_id,
        order_id=order_id,
        trader_id=trader_id,
        account_id=account_id,
        symbol=symbol,
        side=side,
        quantity=quantity,
        action=action,
        price=price,
        strategy_id=strategy_id,
        timestamp=T0 + datetime.timedelta(milliseconds=offset_ms),
    )


class TestSelfMatchDetection(unittest.TestCase):
    def setUp(self):
        self.engine = WashTradeAndSpoofingDetectionEngine()

    def test_compliant_single_order_raises_nothing(self):
        alerts = self.engine.ingest_order_event(
            ev("E1", "O1", OrderAction.PLACE, OrderSide.BUY, price=150.00)
        )
        self.assertEqual(alerts, [])

    def test_equal_price_self_cross_is_detected(self):
        self.engine.ingest_order_event(ev("E1", "O1", OrderAction.PLACE, OrderSide.BUY, price=150.00))
        alerts = self.engine.ingest_order_event(
            ev("E2", "O2", OrderAction.PLACE, OrderSide.SELL, price=150.00, offset_ms=500)
        )
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].alert_type, AlertType.WASH_TRADE)
        self.assertEqual(alerts[0].severity, "CRITICAL")
        self.assertTrue(alerts[0].requires_human_review)

    def test_marketable_sell_crossing_a_better_own_bid_is_detected(self):
        """Regression: a resting bid *above* the incoming offer self-executes.

        An exact price-equality test reports no self-match here, yet the
        matching engine would trade the incoming SELL at 150.00 against the
        firm's own resting BUY at 150.05.
        """
        self.engine.ingest_order_event(ev("E1", "O1", OrderAction.PLACE, OrderSide.BUY, price=150.05))
        alerts = self.engine.ingest_order_event(
            ev("E2", "O2", OrderAction.PLACE, OrderSide.SELL, price=150.00, offset_ms=10)
        )
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].alert_type, AlertType.WASH_TRADE)

    def test_marketable_buy_crossing_a_better_own_offer_is_detected(self):
        self.engine.ingest_order_event(ev("E1", "O1", OrderAction.PLACE, OrderSide.SELL, price=150.00))
        alerts = self.engine.ingest_order_event(
            ev("E2", "O2", OrderAction.PLACE, OrderSide.BUY, price=150.05, offset_ms=10)
        )
        self.assertEqual(len(alerts), 1)

    def test_non_crossing_two_sided_quote_raises_nothing(self):
        """A bid at 149.00 and an offer at 150.00 never meet."""
        self.engine.ingest_order_event(ev("E1", "O1", OrderAction.PLACE, OrderSide.BUY, price=149.00))
        alerts = self.engine.ingest_order_event(
            ev("E2", "O2", OrderAction.PLACE, OrderSide.SELL, price=150.00, offset_ms=10)
        )
        self.assertEqual(alerts, [])

    def test_unpriced_order_crosses_every_own_level(self):
        self.engine.ingest_order_event(ev("E1", "O1", OrderAction.PLACE, OrderSide.SELL, price=999.00))
        alerts = self.engine.ingest_order_event(
            ev("E2", "O2", OrderAction.PLACE, OrderSide.BUY, price=None, offset_ms=10)
        )
        self.assertEqual(len(alerts), 1)

    def test_stale_resting_order_still_self_matches(self):
        """Regression: a self-match is a function of crossing, not of resting age.

        An order that has rested for an hour is still hit by the firm's own
        aggressor. A short lookback window suppresses the alert entirely.
        """
        self.engine.ingest_order_event(ev("E1", "O1", OrderAction.PLACE, OrderSide.BUY, price=150.00))
        alerts = self.engine.ingest_order_event(
            ev("E2", "O2", OrderAction.PLACE, OrderSide.SELL, price=150.00, offset_ms=3_600_000)
        )
        self.assertEqual(len(alerts), 1)

    def test_explicit_window_bounds_the_lookback_when_requested(self):
        engine = WashTradeAndSpoofingDetectionEngine(wash_trade_window_seconds=2.0)
        engine.ingest_order_event(ev("E1", "O1", OrderAction.PLACE, OrderSide.BUY, price=150.00))
        self.assertEqual(
            engine.ingest_order_event(
                ev("E2", "O2", OrderAction.PLACE, OrderSide.SELL, price=150.00, offset_ms=5000)
            ),
            [],
        )

    def test_different_accounts_under_one_beneficial_owner_are_detected(self):
        """Regression: the wash-trade exposure attaches to the owner, not the string.

        Two desks with different trader ids *and* different account ids under
        one entity self-cross. Comparing raw ids finds nothing.
        """
        engine = WashTradeAndSpoofingDetectionEngine(
            beneficial_owner_map={"ACC1": "ENTITY_A", "ACC2": "ENTITY_A"}
        )
        engine.ingest_order_event(
            ev("E1", "O1", OrderAction.PLACE, OrderSide.BUY, price=150.00, trader_id="T1", account_id="ACC1")
        )
        alerts = engine.ingest_order_event(
            ev(
                "E2", "O2", OrderAction.PLACE, OrderSide.SELL, price=150.00,
                trader_id="T2", account_id="ACC2", offset_ms=10,
            )
        )
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].beneficial_owner_id, "ENTITY_A")

    def test_unrelated_owners_do_not_self_match(self):
        engine = WashTradeAndSpoofingDetectionEngine()
        engine.ingest_order_event(
            ev("E1", "O1", OrderAction.PLACE, OrderSide.BUY, price=150.00, trader_id="T1", account_id="ACC1")
        )
        alerts = engine.ingest_order_event(
            ev(
                "E2", "O2", OrderAction.PLACE, OrderSide.SELL, price=150.00,
                trader_id="T2", account_id="ACC2", offset_ms=10,
            )
        )
        self.assertEqual(alerts, [])

    def test_different_symbols_do_not_self_match(self):
        self.engine.ingest_order_event(
            ev("E1", "O1", OrderAction.PLACE, OrderSide.BUY, price=150.00, symbol="AAPL")
        )
        alerts = self.engine.ingest_order_event(
            ev("E2", "O2", OrderAction.PLACE, OrderSide.SELL, price=150.00, symbol="MSFT", offset_ms=10)
        )
        self.assertEqual(alerts, [])

    def test_unrelated_strategies_downgrade_but_do_not_suppress(self):
        """FINRA Rule 5210.02 treats unrelated-algorithm self-trades as generally
        bona fide; CME Rule 534 has no such carve-out, so the alert survives."""
        self.engine.ingest_order_event(
            ev("E1", "O1", OrderAction.PLACE, OrderSide.BUY, price=150.00, strategy_id="MM_QUOTER")
        )
        alerts = self.engine.ingest_order_event(
            ev(
                "E2", "O2", OrderAction.PLACE, OrderSide.SELL, price=150.00,
                strategy_id="STAT_ARB", offset_ms=10,
            )
        )
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].severity, "MEDIUM")

    def test_same_strategy_self_cross_is_critical(self):
        self.engine.ingest_order_event(
            ev("E1", "O1", OrderAction.PLACE, OrderSide.BUY, price=150.00, strategy_id="MM_QUOTER")
        )
        alerts = self.engine.ingest_order_event(
            ev(
                "E2", "O2", OrderAction.PLACE, OrderSide.SELL, price=150.00,
                strategy_id="MM_QUOTER", offset_ms=10,
            )
        )
        self.assertEqual(alerts[0].severity, "CRITICAL")

    def test_best_priced_own_order_is_reported_regardless_of_input_order(self):
        """The aggressor reaches the cheapest own offer first."""
        for i, price in enumerate((151.00, 150.00, 150.50)):
            self.engine.ingest_order_event(
                ev(f"E{i}", f"O{i}", OrderAction.PLACE, OrderSide.SELL, price=price, offset_ms=i)
            )
        alerts = self.engine.ingest_order_event(
            ev("EB", "OB", OrderAction.PLACE, OrderSide.BUY, price=152.00, offset_ms=10)
        )
        self.assertEqual(len(alerts), 1)
        # O1 is the 150.00 offer, the lowest reachable price.
        self.assertIn("O1", alerts[0].description)

    def test_cancelled_order_no_longer_self_matches(self):
        self.engine.ingest_order_event(ev("E1", "O1", OrderAction.PLACE, OrderSide.BUY, price=150.00))
        self.engine.ingest_order_event(
            ev("E2", "O1", OrderAction.CANCEL, OrderSide.BUY, offset_ms=100)
        )
        alerts = self.engine.ingest_order_event(
            ev("E3", "O2", OrderAction.PLACE, OrderSide.SELL, price=150.00, offset_ms=200)
        )
        self.assertEqual(alerts, [])

    def test_partial_fill_leaves_remainder_capable_of_self_matching(self):
        self.engine.ingest_order_event(
            ev("E1", "O1", OrderAction.PLACE, OrderSide.BUY, price=150.00, quantity=100.0)
        )
        self.engine.ingest_order_event(
            ev("E2", "O1", OrderAction.FILL, OrderSide.BUY, price=150.00, quantity=40.0, offset_ms=50)
        )
        self.assertEqual(self.engine.get_open_orders("ACC1"), ["O1"])
        alerts = self.engine.ingest_order_event(
            ev("E3", "O2", OrderAction.PLACE, OrderSide.SELL, price=150.00, offset_ms=100)
        )
        self.assertEqual(len(alerts), 1)

    def test_full_fill_removes_the_order_from_the_book(self):
        self.engine.ingest_order_event(
            ev("E1", "O1", OrderAction.PLACE, OrderSide.BUY, price=150.00, quantity=100.0)
        )
        self.engine.ingest_order_event(
            ev("E2", "O1", OrderAction.FILL, OrderSide.BUY, price=150.00, quantity=100.0, offset_ms=50)
        )
        self.assertEqual(self.engine.get_open_orders(), [])


class TestLayeringDetection(unittest.TestCase):
    def setUp(self):
        self.engine = WashTradeAndSpoofingDetectionEngine()

    def _place_layers(self, n=3, qty=5000.0, symbol="NVDA"):
        for i in range(n):
            self.engine.ingest_order_event(
                ev(
                    f"P{i}", f"S{i}", OrderAction.PLACE, OrderSide.SELL,
                    price=120.10 + i * 0.10, quantity=qty, symbol=symbol, offset_ms=i,
                )
            )

    def test_cancels_following_the_opposite_fill_are_detected(self):
        """Regression: FINRA Rule 5210.03 Type 1 places the cancellations
        *after* the opposite-side execution, so a detector that only inspects
        history at fill time sees nothing at all."""
        self._place_layers()
        self.engine.ingest_order_event(
            ev("F1", "B1", OrderAction.FILL, OrderSide.BUY, price=119.50, quantity=100.0,
               symbol="NVDA", offset_ms=100)
        )
        self.assertEqual(
            self.engine.ingest_order_event(
                ev("C0", "S0", OrderAction.CANCEL, OrderSide.SELL, quantity=5000.0,
                   symbol="NVDA", offset_ms=200)
            ),
            [],
            "one withdrawn order is not the 'multiple limit orders' shape",
        )
        alerts = self.engine.ingest_order_event(
            ev("C1", "S1", OrderAction.CANCEL, OrderSide.SELL, quantity=5000.0,
               symbol="NVDA", offset_ms=300)
        )
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].alert_type, AlertType.SPOOFING_LAYERING)
        self.assertEqual(alerts[0].pattern_shape, PatternShape.CANCEL_AFTER_FILL)
        self.assertEqual(alerts[0].severity, "HIGH")
        self.assertIn("F1", alerts[0].related_event_ids)

    def test_layering_alert_fires_once_per_execution(self):
        self._place_layers()
        self.engine.ingest_order_event(
            ev("F1", "B1", OrderAction.FILL, OrderSide.BUY, price=119.50, quantity=100.0,
               symbol="NVDA", offset_ms=100)
        )
        emitted = 0
        for i in range(3):
            emitted += len(
                self.engine.ingest_order_event(
                    ev(f"C{i}", f"S{i}", OrderAction.CANCEL, OrderSide.SELL, quantity=5000.0,
                       symbol="NVDA", offset_ms=200 + i * 50)
                )
            )
        self.assertEqual(emitted, 1)

    def test_market_maker_refreshing_matched_size_is_not_flagged(self):
        """Regression: two-sided quoting cancels opposite-side size around
        almost every fill. Without a size test the detector fires constantly.

        Withdrawn 200 against a fill of 100 is 2.0x, below the 3.0x default.
        """
        for i in range(2):
            self.engine.ingest_order_event(
                ev(f"P{i}", f"S{i}", OrderAction.PLACE, OrderSide.SELL, price=120.10 + i * 0.10,
                   quantity=100.0, symbol="NVDA", offset_ms=i)
            )
        self.engine.ingest_order_event(
            ev("F1", "B1", OrderAction.FILL, OrderSide.BUY, price=119.50, quantity=100.0,
               symbol="NVDA", offset_ms=100)
        )
        emitted = []
        for i in range(2):
            emitted += self.engine.ingest_order_event(
                ev(f"C{i}", f"S{i}", OrderAction.CANCEL, OrderSide.SELL, quantity=100.0,
                   symbol="NVDA", offset_ms=200 + i * 50)
            )
        self.assertEqual(emitted, [])

    def test_single_large_cancel_is_not_the_multiple_order_shape(self):
        self.engine.ingest_order_event(
            ev("P0", "S0", OrderAction.PLACE, OrderSide.SELL, price=120.10, quantity=10000.0,
               symbol="NVDA")
        )
        self.engine.ingest_order_event(
            ev("F1", "B1", OrderAction.FILL, OrderSide.BUY, price=119.50, quantity=100.0,
               symbol="NVDA", offset_ms=100)
        )
        alerts = self.engine.ingest_order_event(
            ev("C0", "S0", OrderAction.CANCEL, OrderSide.SELL, quantity=10000.0,
               symbol="NVDA", offset_ms=200)
        )
        self.assertEqual(alerts, [])

    def test_cancels_outside_the_window_do_not_attach(self):
        self._place_layers()
        self.engine.ingest_order_event(
            ev("F1", "B1", OrderAction.FILL, OrderSide.BUY, price=119.50, quantity=100.0,
               symbol="NVDA", offset_ms=100)
        )
        emitted = []
        for i in range(3):
            emitted += self.engine.ingest_order_event(
                ev(f"C{i}", f"S{i}", OrderAction.CANCEL, OrderSide.SELL, quantity=5000.0,
                   symbol="NVDA", offset_ms=5000 + i * 50)
            )
        self.assertEqual(emitted, [])

    def test_same_side_cancels_do_not_attach(self):
        """Withdrawing size on the *same* side as the execution is not layering."""
        for i in range(3):
            self.engine.ingest_order_event(
                ev(f"P{i}", f"B{i}", OrderAction.PLACE, OrderSide.BUY, price=119.00 - i * 0.10,
                   quantity=5000.0, symbol="NVDA", offset_ms=i)
            )
        self.engine.ingest_order_event(
            ev("F1", "BX", OrderAction.FILL, OrderSide.BUY, price=119.50, quantity=100.0,
               symbol="NVDA", offset_ms=100)
        )
        emitted = []
        for i in range(3):
            emitted += self.engine.ingest_order_event(
                ev(f"C{i}", f"B{i}", OrderAction.CANCEL, OrderSide.BUY, quantity=5000.0,
                   symbol="NVDA", offset_ms=200 + i * 50)
            )
        self.assertEqual(emitted, [])

    def test_cancels_preceding_the_fill_are_the_weaker_shape(self):
        self._place_layers(n=2, qty=5000.0)
        for i in range(2):
            self.engine.ingest_order_event(
                ev(f"C{i}", f"S{i}", OrderAction.CANCEL, OrderSide.SELL, quantity=5000.0,
                   symbol="NVDA", offset_ms=100 + i * 10)
            )
        alerts = self.engine.ingest_order_event(
            ev("F1", "B1", OrderAction.FILL, OrderSide.BUY, price=119.50, quantity=100.0,
               symbol="NVDA", offset_ms=200)
        )
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].pattern_shape, PatternShape.CANCEL_BEFORE_FILL)
        self.assertEqual(alerts[0].severity, "MEDIUM")

    def test_layering_groups_by_beneficial_owner(self):
        engine = WashTradeAndSpoofingDetectionEngine(
            beneficial_owner_map={"ACC1": "ENTITY_A", "ACC2": "ENTITY_A"}
        )
        for i in range(3):
            engine.ingest_order_event(
                ev(f"P{i}", f"S{i}", OrderAction.PLACE, OrderSide.SELL, price=120.10 + i * 0.10,
                   quantity=5000.0, symbol="NVDA", account_id="ACC1", trader_id="T1", offset_ms=i)
            )
        engine.ingest_order_event(
            ev("F1", "B1", OrderAction.FILL, OrderSide.BUY, price=119.50, quantity=100.0,
               symbol="NVDA", account_id="ACC2", trader_id="T2", offset_ms=100)
        )
        emitted = []
        for i in range(2):
            emitted += engine.ingest_order_event(
                ev(f"C{i}", f"S{i}", OrderAction.CANCEL, OrderSide.SELL, quantity=5000.0,
                   symbol="NVDA", account_id="ACC1", trader_id="T1", offset_ms=200 + i * 50)
            )
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].beneficial_owner_id, "ENTITY_A")


class TestMetricsAndRatio(unittest.TestCase):
    def _run_ten_placements_nine_cancels(self, engine):
        emitted = []
        for i in range(10):
            emitted += engine.ingest_order_event(
                ev(f"P_{i}", f"ORD_{i}", OrderAction.PLACE, OrderSide.BUY, price=300.0 + i,
                   symbol="MSFT", trader_id="T2", account_id="ACC2", offset_ms=i * 1000)
            )
            if i < 9:
                emitted += engine.ingest_order_event(
                    ev(f"C_{i}", f"ORD_{i}", OrderAction.CANCEL, OrderSide.BUY,
                       symbol="MSFT", trader_id="T2", account_id="ACC2", offset_ms=i * 1000 + 100)
                )
        return emitted

    def test_cancellation_ratio_arithmetic(self):
        engine = WashTradeAndSpoofingDetectionEngine()
        self._run_ten_placements_nine_cancels(engine)
        metrics = engine.get_trader_metrics("T2")
        self.assertEqual(metrics.total_orders_placed, 10)
        self.assertEqual(metrics.total_orders_canceled, 9)
        self.assertEqual(metrics.total_orders_filled, 0)
        # 9 / 10 * 100 = 90.0, derived independently of the implementation.
        self.assertEqual(metrics.cancellation_ratio_pct, 90.0)
        self.assertEqual(metrics.unmatched_cancels, 0)

    def test_cancellation_ratio_alert_is_latched(self):
        """Regression: re-emitting on every subsequent event buries the alert
        that mattered under thousands of duplicates."""
        engine = WashTradeAndSpoofingDetectionEngine()
        emitted = self._run_ten_placements_nine_cancels(engine)
        ratio_alerts = [a for a in emitted if a.alert_type == AlertType.HIGH_CANCELLATION_RATIO]
        self.assertEqual(len(ratio_alerts), 1)

        # Ratio climbs to 100%; the latch must hold.
        more = engine.ingest_order_event(
            ev("C_9", "ORD_9", OrderAction.CANCEL, OrderSide.BUY, symbol="MSFT",
               trader_id="T2", account_id="ACC2", offset_ms=20_000)
        )
        self.assertEqual([a for a in more if a.alert_type == AlertType.HIGH_CANCELLATION_RATIO], [])
        self.assertEqual(engine.get_trader_metrics("T2").cancellation_ratio_pct, 100.0)

    def test_latch_can_be_rearmed(self):
        engine = WashTradeAndSpoofingDetectionEngine()
        self._run_ten_placements_nine_cancels(engine)
        engine.reset_cancellation_ratio_alert("T2")
        more = engine.ingest_order_event(
            ev("C_9", "ORD_9", OrderAction.CANCEL, OrderSide.BUY, symbol="MSFT",
               trader_id="T2", account_id="ACC2", offset_ms=20_000)
        )
        self.assertEqual(len([a for a in more if a.alert_type == AlertType.HIGH_CANCELLATION_RATIO]), 1)

    def test_ratio_needs_a_minimum_sample(self):
        engine = WashTradeAndSpoofingDetectionEngine()
        emitted = []
        for i in range(3):
            emitted += engine.ingest_order_event(
                ev(f"P{i}", f"O{i}", OrderAction.PLACE, OrderSide.BUY, price=10.0, offset_ms=i * 10)
            )
            emitted += engine.ingest_order_event(
                ev(f"C{i}", f"O{i}", OrderAction.CANCEL, OrderSide.BUY, offset_ms=i * 10 + 1)
            )
        self.assertEqual(engine.get_trader_metrics("T1").cancellation_ratio_pct, 100.0)
        self.assertEqual([a for a in emitted if a.alert_type == AlertType.HIGH_CANCELLATION_RATIO], [])

    def test_average_lifespan_arithmetic(self):
        engine = WashTradeAndSpoofingDetectionEngine()
        engine.ingest_order_event(ev("P1", "O1", OrderAction.PLACE, OrderSide.BUY, price=10.0))
        engine.ingest_order_event(ev("P2", "O2", OrderAction.PLACE, OrderSide.BUY, price=11.0))
        engine.ingest_order_event(ev("C1", "O1", OrderAction.CANCEL, OrderSide.BUY, offset_ms=100))
        engine.ingest_order_event(ev("C2", "O2", OrderAction.CANCEL, OrderSide.BUY, offset_ms=300))
        # (100 + 300) / 2 = 200.0 ms.
        self.assertEqual(engine.get_trader_metrics("T1").avg_order_lifespan_ms, 200.0)

    def test_cancel_without_a_matching_placement_is_reported_not_guessed(self):
        engine = WashTradeAndSpoofingDetectionEngine()
        engine.ingest_order_event(ev("C1", "UNSEEN", OrderAction.CANCEL, OrderSide.BUY))
        metrics = engine.get_trader_metrics("T1")
        self.assertEqual(metrics.unmatched_cancels, 1)
        self.assertEqual(metrics.avg_order_lifespan_ms, 0.0)

    def test_metrics_resolve_through_any_alias(self):
        engine = WashTradeAndSpoofingDetectionEngine(beneficial_owner_map={"ACC1": "ENTITY_A"})
        engine.ingest_order_event(ev("E1", "O1", OrderAction.PLACE, OrderSide.BUY, price=10.0))
        for key in ("T1", "ACC1", "ENTITY_A"):
            self.assertEqual(engine.get_trader_metrics(key).total_orders_placed, 1, key)

    def test_metrics_aggregate_across_accounts_of_one_owner(self):
        engine = WashTradeAndSpoofingDetectionEngine(
            beneficial_owner_map={"ACC1": "ENTITY_A", "ACC2": "ENTITY_A"}
        )
        engine.ingest_order_event(
            ev("E1", "O1", OrderAction.PLACE, OrderSide.BUY, price=10.0, account_id="ACC1", symbol="X")
        )
        engine.ingest_order_event(
            ev("E2", "O2", OrderAction.PLACE, OrderSide.BUY, price=10.0, account_id="ACC2",
               trader_id="T2", symbol="Y", offset_ms=1)
        )
        self.assertEqual(engine.get_trader_metrics("ENTITY_A").total_orders_placed, 2)

    def test_metrics_for_an_unknown_id_are_empty_not_an_error(self):
        engine = WashTradeAndSpoofingDetectionEngine()
        metrics = engine.get_trader_metrics("NOBODY")
        self.assertIsInstance(metrics, TraderMetrics)
        self.assertEqual(metrics.total_orders_placed, 0)
        self.assertEqual(metrics.cancellation_ratio_pct, 0.0)


class TestAuditTrailIntegrity(unittest.TestCase):
    def _self_cross(self, engine, n, offset_ms):
        """One self-cross in its own instrument, so crosses do not interact."""
        symbol = f"SYM{n}"
        engine.ingest_order_event(
            ev(f"E{n}a", f"O{n}a", OrderAction.PLACE, OrderSide.BUY, price=150.00,
               symbol=symbol, offset_ms=offset_ms)
        )
        return engine.ingest_order_event(
            ev(f"E{n}b", f"O{n}b", OrderAction.PLACE, OrderSide.SELL, price=150.00,
               symbol=symbol, offset_ms=offset_ms + 1)
        )

    def test_alert_ids_are_unique_within_one_second(self):
        """Regression: an id derived from a whole-second timestamp collides,
        and a colliding id cannot be cited in an audit trail."""
        engine = WashTradeAndSpoofingDetectionEngine()
        first = self._self_cross(engine, 1, 0)
        second = self._self_cross(engine, 2, 10)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertNotEqual(first[0].alert_id, second[0].alert_id)

    def test_alert_ids_are_deterministic_across_runs(self):
        ids = []
        for _ in range(2):
            engine = WashTradeAndSpoofingDetectionEngine()
            ids.append(self._self_cross(engine, 1, 0)[0].alert_id)
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(ids[0], "ALT-WASH-E1a-E1b")

    def test_alerts_carry_an_indicator_reference_and_review_flag(self):
        engine = WashTradeAndSpoofingDetectionEngine()
        alert = self._self_cross(engine, 1, 0)[0]
        self.assertTrue(alert.indicator_reference)
        self.assertIn("4c(a)", alert.indicator_reference)
        self.assertTrue(alert.requires_human_review)

    def test_alert_log_accumulates(self):
        engine = WashTradeAndSpoofingDetectionEngine()
        self._self_cross(engine, 1, 0)
        self._self_cross(engine, 2, 10)
        self.assertEqual(len(engine.alerts), 2)
        self.assertIs(engine.violations, engine.alerts)

    def test_backwards_compatible_aliases(self):
        self.assertIs(SurveillanceViolation, SurveillanceAlert)
        self.assertIs(ViolationType, AlertType)


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.engine = WashTradeAndSpoofingDetectionEngine()

    def test_naive_timestamp_is_rejected(self):
        bad = OrderEvent(
            event_id="E1", order_id="O1", trader_id="T1", account_id="ACC1", symbol="AAPL",
            side=OrderSide.BUY, quantity=100.0, action=OrderAction.PLACE, price=150.0,
            timestamp=datetime.datetime(2026, 3, 2, 14, 30, 0),
        )
        with self.assertRaises(SurveillanceError):
            self.engine.ingest_order_event(bad)

    def test_non_utc_aware_timestamp_is_accepted_and_compared_correctly(self):
        """An aware timestamp in any offset is unambiguous, so it is usable."""
        tokyo = datetime.timezone(datetime.timedelta(hours=9))
        self.engine.ingest_order_event(ev("E1", "O1", OrderAction.PLACE, OrderSide.BUY, price=150.00))
        later = OrderEvent(
            event_id="E2", order_id="O2", trader_id="T1", account_id="ACC1", symbol="AAPL",
            side=OrderSide.SELL, quantity=100.0, action=OrderAction.PLACE, price=150.0,
            timestamp=(T0 + datetime.timedelta(milliseconds=10)).astimezone(tokyo),
        )
        self.assertEqual(len(self.engine.ingest_order_event(later)), 1)

    def test_duplicate_event_id_is_rejected(self):
        """A replayed event inflates the cancellation ratio and the size test."""
        self.engine.ingest_order_event(ev("E1", "O1", OrderAction.PLACE, OrderSide.BUY, price=150.0))
        with self.assertRaises(SurveillanceError):
            self.engine.ingest_order_event(
                ev("E1", "O9", OrderAction.PLACE, OrderSide.BUY, price=150.0, offset_ms=1)
            )

    def test_reusing_a_live_order_id_is_rejected(self):
        self.engine.ingest_order_event(ev("E1", "O1", OrderAction.PLACE, OrderSide.BUY, price=150.0))
        with self.assertRaises(SurveillanceError):
            self.engine.ingest_order_event(
                ev("E2", "O1", OrderAction.PLACE, OrderSide.SELL, price=160.0, offset_ms=1)
            )

    def test_cancel_without_a_price_is_accepted(self):
        """A cancel legitimately carries no price; demanding one rejects valid flow."""
        self.engine.ingest_order_event(ev("E1", "O1", OrderAction.PLACE, OrderSide.BUY, price=150.0))
        self.assertEqual(
            self.engine.ingest_order_event(ev("E2", "O1", OrderAction.CANCEL, OrderSide.BUY, offset_ms=10)),
            [],
        )

    def test_non_positive_and_non_finite_values_are_rejected(self):
        for kwargs in (
            {"price": -10.0},
            {"price": 0.0},
            {"price": float("nan")},
            {"price": float("inf")},
            {"quantity": 0.0},
            {"quantity": -5.0},
            {"quantity": float("nan")},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(SurveillanceError):
                    self.engine.ingest_order_event(
                        ev("E_BAD", "O_BAD", OrderAction.PLACE, OrderSide.BUY,
                           **{"price": 150.0, "quantity": 100.0, **kwargs})
                    )

    def test_blank_identifiers_are_rejected(self):
        for field_name in ("event_id", "order_id", "trader_id", "account_id", "symbol"):
            with self.subTest(field=field_name):
                kwargs = dict(
                    event_id="E1", order_id="O1", trader_id="T1", account_id="ACC1", symbol="AAPL",
                    side=OrderSide.BUY, quantity=100.0, action=OrderAction.PLACE, price=150.0,
                    timestamp=T0,
                )
                kwargs[field_name] = "  "
                with self.assertRaises(SurveillanceError):
                    self.engine.ingest_order_event(OrderEvent(**kwargs))

    def test_wrong_enum_types_are_rejected(self):
        bad_side = OrderEvent(
            event_id="E1", order_id="O1", trader_id="T1", account_id="ACC1", symbol="AAPL",
            side="BUY", quantity=100.0, action=OrderAction.PLACE, price=150.0, timestamp=T0,
        )
        with self.assertRaises(SurveillanceError):
            self.engine.ingest_order_event(bad_side)

        bad_action = OrderEvent(
            event_id="E2", order_id="O2", trader_id="T1", account_id="ACC1", symbol="AAPL",
            side=OrderSide.BUY, quantity=100.0, action="PLACE", price=150.0, timestamp=T0,
        )
        with self.assertRaises(SurveillanceError):
            self.engine.ingest_order_event(bad_action)

    def test_rejected_event_does_not_mutate_state(self):
        with self.assertRaises(SurveillanceError):
            self.engine.ingest_order_event(
                ev("E_BAD", "O_BAD", OrderAction.PLACE, OrderSide.BUY, price=-1.0)
            )
        self.assertEqual(self.engine.get_trader_metrics("T1").total_orders_placed, 0)
        self.assertEqual(self.engine.get_open_orders(), [])
        # The rejected id was never consumed, so a corrected replay is accepted.
        self.engine.ingest_order_event(ev("E_BAD", "O_BAD", OrderAction.PLACE, OrderSide.BUY, price=1.0))
        self.assertEqual(self.engine.get_trader_metrics("T1").total_orders_placed, 1)

    def test_invalid_constructor_parameters_are_rejected(self):
        for kwargs in (
            {"spoofing_lifespan_threshold_ms": 0.0},
            {"spoofing_lifespan_threshold_ms": float("nan")},
            {"cancellation_ratio_threshold_pct": 0.0},
            {"cancellation_ratio_threshold_pct": 101.0},
            {"min_orders_for_cancel_ratio": 0},
            {"layering_size_ratio": 0.0},
            {"min_layered_orders": 0},
            {"price_tolerance": -1.0},
            {"wash_trade_window_seconds": -1.0},
            {"max_history_per_owner": 0},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(SurveillanceError):
                    WashTradeAndSpoofingDetectionEngine(**kwargs)


class TestHousekeeping(unittest.TestCase):
    def test_expire_orders_before_drops_stale_resting_state(self):
        engine = WashTradeAndSpoofingDetectionEngine()
        engine.ingest_order_event(ev("E1", "O1", OrderAction.PLACE, OrderSide.BUY, price=150.0))
        self.assertEqual(engine.expire_orders_before(T0 + datetime.timedelta(hours=1)), 1)
        self.assertEqual(engine.get_open_orders(), [])
        # An expired order can no longer produce a self-match alert.
        self.assertEqual(
            engine.ingest_order_event(
                ev("E2", "O2", OrderAction.PLACE, OrderSide.SELL, price=150.0, offset_ms=3_600_001)
            ),
            [],
        )

    def test_expire_orders_before_requires_an_aware_cutoff(self):
        engine = WashTradeAndSpoofingDetectionEngine()
        with self.assertRaises(SurveillanceError):
            engine.expire_orders_before(datetime.datetime(2026, 3, 2, 15, 30))

    def test_negative_lifespan_is_excluded_not_averaged_in(self):
        """A cancel timestamped before its own placement is a clock defect.

        Averaging it in drags the mean towards zero and manufactures the
        'fast cancellation' signal the metric exists to measure.
        """
        engine = WashTradeAndSpoofingDetectionEngine()
        engine.ingest_order_event(ev("P1", "O1", OrderAction.PLACE, OrderSide.BUY, price=10.0, offset_ms=500))
        engine.ingest_order_event(ev("P2", "O2", OrderAction.PLACE, OrderSide.BUY, price=11.0, offset_ms=500))
        engine.ingest_order_event(ev("C1", "O1", OrderAction.CANCEL, OrderSide.BUY, offset_ms=100))
        engine.ingest_order_event(ev("C2", "O2", OrderAction.CANCEL, OrderSide.BUY, offset_ms=700))
        metrics = engine.get_trader_metrics("T1")
        self.assertEqual(metrics.unmatched_cancels, 1)
        # Only the valid 200ms lifespan contributes.
        self.assertEqual(metrics.avg_order_lifespan_ms, 200.0)

    def test_duplicate_detection_window_is_bounded(self):
        engine = WashTradeAndSpoofingDetectionEngine(max_tracked_event_ids=3)
        for i in range(5):
            engine.ingest_order_event(
                ev(f"E{i}", f"O{i}", OrderAction.PLACE, OrderSide.BUY, price=10.0, offset_ms=i)
            )
        self.assertEqual(len(engine._seen_event_ids), 3)
        # A recent id is still rejected...
        with self.assertRaises(SurveillanceError):
            engine.ingest_order_event(
                ev("E4", "OX", OrderAction.PLACE, OrderSide.BUY, price=10.0, offset_ms=99)
            )
        # ...while one evicted from the window is no longer recognised.
        engine.ingest_order_event(
            ev("E0", "OY", OrderAction.PLACE, OrderSide.BUY, price=10.0, offset_ms=100)
        )

    def test_unconvertible_integer_quantity_is_rejected_cleanly(self):
        engine = WashTradeAndSpoofingDetectionEngine()
        with self.assertRaises(SurveillanceError):
            engine.ingest_order_event(
                ev("E1", "O1", OrderAction.PLACE, OrderSide.BUY, price=10.0, quantity=10 ** 400)
            )

    def test_history_is_bounded(self):
        engine = WashTradeAndSpoofingDetectionEngine(max_history_per_owner=5)
        for i in range(50):
            engine.ingest_order_event(
                ev(f"E{i}", f"O{i}", OrderAction.PLACE, OrderSide.BUY, price=10.0, offset_ms=i)
            )
        self.assertEqual(len(engine._history["ACC1"]), 5)
        # Counters are incremental, so bounding history does not distort them.
        self.assertEqual(engine.get_trader_metrics("T1").total_orders_placed, 50)


if __name__ == "__main__":
    unittest.main()
