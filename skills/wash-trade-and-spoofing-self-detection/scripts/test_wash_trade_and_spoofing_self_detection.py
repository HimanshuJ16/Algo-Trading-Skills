import unittest
import datetime
from wash_trade_and_spoofing_self_detection import (
    OrderAction,
    OrderSide,
    ViolationType,
    OrderEvent,
    SurveillanceViolation,
    TraderMetrics,
    WashTradeAndSpoofingDetectionEngine,
    SurveillanceError,
)


class TestWashTradeAndSpoofingDetectionEngine(unittest.TestCase):
    def setUp(self):
        self.engine = WashTradeAndSpoofingDetectionEngine(
            wash_trade_window_seconds=2.0,
            spoofing_lifespan_threshold_ms=1000.0,
            cancellation_ratio_threshold_pct=90.0,
        )
        self.now = datetime.datetime.utcnow()

    def test_compliant_order_flow(self):
        event = OrderEvent(
            event_id="E1",
            order_id="O1",
            trader_id="T1",
            account_id="ACC1",
            symbol="AAPL",
            side=OrderSide.BUY,
            price=150.0,
            quantity=100.0,
            action=OrderAction.PLACE,
            timestamp=self.now,
        )
        viols = self.engine.ingest_order_event(event)

        self.assertEqual(len(viols), 0)

    def test_wash_trade_self_cross_detection(self):
        # 1. Trader T1 places BUY 100 AAPL @ $150
        # 2. Trader T1 places SELL 100 AAPL @ $150 (Self-cross at same price within 2 sec)
        buy_event = OrderEvent(
            event_id="E1",
            order_id="O1",
            trader_id="T1",
            account_id="ACC1",
            symbol="AAPL",
            side=OrderSide.BUY,
            price=150.0,
            quantity=100.0,
            action=OrderAction.PLACE,
            timestamp=self.now,
        )
        self.engine.ingest_order_event(buy_event)

        sell_event = OrderEvent(
            event_id="E2",
            order_id="O2",
            trader_id="T1",
            account_id="ACC1",
            symbol="AAPL",
            side=OrderSide.SELL,
            price=150.0,
            quantity=100.0,
            action=OrderAction.PLACE,
            timestamp=self.now + datetime.timedelta(seconds=0.5),
        )
        viols = self.engine.ingest_order_event(sell_event)

        self.assertEqual(len(viols), 1)
        self.assertEqual(viols[0].violation_type, ViolationType.WASH_TRADE)
        self.assertEqual(viols[0].severity, "CRITICAL")

    def test_spoofing_pattern_detection_on_opposite_fill(self):
        # 1. T1 places large SELL order (non-bona fide Ask layering)
        # 2. T1 cancels large SELL order within 200 ms
        # 3. T1 gets a BUY fill immediately afterwards -> Spoofing pattern!
        place_sell = OrderEvent(
            event_id="E1",
            order_id="O1",
            trader_id="T1",
            account_id="ACC1",
            symbol="NVDA",
            side=OrderSide.SELL,
            price=120.0,
            quantity=10000.0,
            action=OrderAction.PLACE,
            timestamp=self.now,
        )
        self.engine.ingest_order_event(place_sell)

        cancel_sell = OrderEvent(
            event_id="E2",
            order_id="O1",
            trader_id="T1",
            account_id="ACC1",
            symbol="NVDA",
            side=OrderSide.SELL,
            price=120.0,
            quantity=10000.0,
            action=OrderAction.CANCEL,
            timestamp=self.now + datetime.timedelta(milliseconds=200),
        )
        self.engine.ingest_order_event(cancel_sell)

        fill_buy = OrderEvent(
            event_id="E3",
            order_id="O2",
            trader_id="T1",
            account_id="ACC1",
            symbol="NVDA",
            side=OrderSide.BUY,
            price=119.5,
            quantity=100.0,
            action=OrderAction.FILL,
            timestamp=self.now + datetime.timedelta(milliseconds=300),
        )
        viols = self.engine.ingest_order_event(fill_buy)

        self.assertTrue(any(v.violation_type == ViolationType.SPOOFING_LAYERING for v in viols))

    def test_high_order_cancellation_ratio_detection(self):
        # Place 10 orders, cancel 9 (90% cancellation ratio)
        for i in range(10):
            p_event = OrderEvent(
                event_id=f"P_{i}",
                order_id=f"ORD_{i}",
                trader_id="T2",
                account_id="ACC2",
                symbol="MSFT",
                side=OrderSide.BUY,
                price=300.0 + i,
                quantity=100.0,
                action=OrderAction.PLACE,
                timestamp=self.now + datetime.timedelta(seconds=i),
            )
            self.engine.ingest_order_event(p_event)

            if i < 9:
                c_event = OrderEvent(
                    event_id=f"C_{i}",
                    order_id=f"ORD_{i}",
                    trader_id="T2",
                    account_id="ACC2",
                    symbol="MSFT",
                    side=OrderSide.BUY,
                    price=300.0 + i,
                    quantity=100.0,
                    action=OrderAction.CANCEL,
                    timestamp=self.now + datetime.timedelta(seconds=i, milliseconds=100),
                )
                self.engine.ingest_order_event(c_event)

        metrics = self.engine.get_trader_metrics("T2")
        self.assertEqual(metrics.total_orders_placed, 10)
        self.assertEqual(metrics.total_orders_canceled, 9)
        self.assertEqual(metrics.cancellation_ratio_pct, 90.0)

    def test_invalid_inputs_raise_error(self):
        with self.assertRaises(SurveillanceError):
            bad_event = OrderEvent(
                event_id="E_ERR",
                order_id="O_ERR",
                trader_id="T1",
                account_id="ACC1",
                symbol="AAPL",
                side=OrderSide.BUY,
                price=-10.0,
                quantity=100.0,
                action=OrderAction.PLACE,
            )
            self.engine.ingest_order_event(bad_event)


if __name__ == "__main__":
    unittest.main()

