"""
Unit tests for broker-order-type-capability-matrix skill.
"""
import unittest
from capability_matrix import BrokerOrderCapabilityMatrix, OrderType


class TestBrokerOrderCapabilityMatrix(unittest.TestCase):

    def setUp(self):
        self.matrix = BrokerOrderCapabilityMatrix()

    def test_native_capability_checks(self):
        # IBKR supports native Bracket and Iceberg
        self.assertTrue(self.matrix.supports_native("ibkr", OrderType.BRACKET))
        self.assertTrue(self.matrix.supports_native("ibkr", OrderType.ICEBERG))

        # Zerodha does not support native Bracket or Iceberg
        self.assertFalse(self.matrix.supports_native("zerodha", OrderType.BRACKET))
        self.assertFalse(self.matrix.supports_native("zerodha", OrderType.ICEBERG))

    def test_native_order_plan(self):
        plan = self.matrix.plan_order_execution("ibkr", OrderType.BRACKET, "AAPL", "BUY", 100)
        self.assertTrue(plan.is_native)
        self.assertEqual(plan.primary_order_type, OrderType.BRACKET)
        self.assertEqual(len(plan.emulated_legs), 0)

    def test_synthesized_bracket_order_plan(self):
        plan = self.matrix.plan_order_execution(
            broker_name="zerodha",
            requested_order_type=OrderType.BRACKET,
            symbol="INFY",
            action="BUY",
            quantity=50,
            price=1500.0,
            stop_loss_price=1450.0,
            take_profit_price=1600.0,
        )

        self.assertFalse(plan.is_native)
        self.assertEqual(plan.primary_order_type, OrderType.LIMIT)
        self.assertEqual(len(plan.emulated_legs), 2)
        self.assertEqual(plan.emulated_legs[0]["leg_type"], "STOP_LOSS")
        self.assertEqual(plan.emulated_legs[1]["leg_type"], "TAKE_PROFIT")

    def test_synthesized_iceberg_order_plan(self):
        plan = self.matrix.plan_order_execution("alpaca", OrderType.ICEBERG, "AAPL", "BUY", 500)
        self.assertFalse(plan.is_native)
        self.assertEqual(plan.primary_order_type, OrderType.LIMIT)
        self.assertEqual(len(plan.emulated_legs), 1)


if __name__ == "__main__":
    unittest.main()
