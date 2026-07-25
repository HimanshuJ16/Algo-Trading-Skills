"""
Unit tests for broker-order-type-capability-matrix skill.
"""
import unittest
from capability_matrix import BrokerOrderCapabilityMatrix, OrderType, BrokerCapabilities


class TestBrokerOrderCapabilityMatrix(unittest.TestCase):

    def setUp(self):
        self.matrix = BrokerOrderCapabilityMatrix()

    def test_native_capability_checks(self):
        # IBKR supports native Bracket and Iceberg
        self.assertTrue(self.matrix.supports_native("ibkr", OrderType.BRACKET))
        self.assertTrue(self.matrix.supports_native("ibkr", OrderType.ICEBERG))
        self.assertTrue(self.matrix.supports_native("ibkr", OrderType.TWAP))

        # Alpaca does not support native Iceberg
        self.assertTrue(self.matrix.supports_native("alpaca", OrderType.BRACKET))
        self.assertFalse(self.matrix.supports_native("alpaca", OrderType.ICEBERG))

    def test_custom_broker_registration(self):
        custom_broker = BrokerCapabilities(
            broker_name="custom_broker",
            native_order_types={OrderType.MARKET, OrderType.LIMIT},
            supports_fractional=False
        )
        self.matrix.register_broker(custom_broker)
        self.assertTrue(self.matrix.supports_native("custom_broker", OrderType.MARKET))
        self.assertFalse(self.matrix.supports_native("custom_broker", OrderType.BRACKET))

    def test_native_order_plan(self):
        plan = self.matrix.plan_order_execution("ibkr", OrderType.BRACKET, "AAPL", "BUY", 100)
        self.assertTrue(plan.is_native)
        self.assertEqual(plan.primary_order_type, OrderType.BRACKET)
        self.assertEqual(len(plan.emulated_legs), 0)

    def test_synthesized_bracket_order_plan(self):
        # Binance lacks native bracket in spot
        plan = self.matrix.plan_order_execution(
            broker_name="binance",
            requested_order_type=OrderType.BRACKET,
            symbol="BTCUSDT",
            action="BUY",
            quantity=1.5,
            price=60000.0,
            stop_loss_price=59000.0,
            take_profit_price=62000.0,
        )

        self.assertFalse(plan.is_native)
        self.assertEqual(plan.primary_order_type, OrderType.LIMIT)
        self.assertEqual(len(plan.emulated_legs), 2)
        
        sl_leg = next(l for l in plan.emulated_legs if l.leg_type == "STOP_LOSS")
        tp_leg = next(l for l in plan.emulated_legs if l.leg_type == "TAKE_PROFIT")
        
        self.assertEqual(sl_leg.action, "SELL")
        self.assertEqual(tp_leg.action, "SELL")
        self.assertEqual(sl_leg.trigger_price, 59000.0)
        self.assertEqual(tp_leg.limit_price, 62000.0)

    def test_synthesized_iceberg_order_plan(self):
        # Alpaca lacks native iceberg
        plan = self.matrix.plan_order_execution("alpaca", OrderType.ICEBERG, "AAPL", "BUY", 500, iceberg_slices=10)
        self.assertFalse(plan.is_native)
        self.assertEqual(plan.primary_order_type, OrderType.LIMIT)
        self.assertEqual(len(plan.emulated_legs), 1)
        self.assertEqual(plan.emulated_legs[0].leg_type, "SLICE_FEEDER")
        self.assertEqual(plan.emulated_legs[0].slice_qty, 50.0)
        
    def test_synthesized_twap_order_plan(self):
        # Alpaca lacks TWAP native
        plan = self.matrix.plan_order_execution("alpaca", OrderType.TWAP, "MSFT", "SELL", 1000, twap_duration_minutes=120)
        self.assertFalse(plan.is_native)
        self.assertEqual(plan.primary_order_type, OrderType.MARKET)
        self.assertEqual(len(plan.emulated_legs), 1)
        self.assertEqual(plan.emulated_legs[0].leg_type, "TWAP_FEEDER")
        self.assertEqual(plan.emulated_legs[0].slice_qty, 100.0) # 10 intervals
        self.assertEqual(plan.emulated_legs[0].interval_seconds, 720) # 120 * 60 / 10

    def test_invalid_broker(self):
        with self.assertRaises(ValueError):
            self.matrix.plan_order_execution("unknown_broker", OrderType.MARKET, "AAPL", "BUY", 100)

    def test_bracket_missing_prices(self):
        with self.assertRaises(ValueError):
            self.matrix.plan_order_execution("binance", OrderType.BRACKET, "AAPL", "BUY", 100)

if __name__ == "__main__":
    unittest.main()
