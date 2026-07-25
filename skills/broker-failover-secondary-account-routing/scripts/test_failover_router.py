"""
Unit tests for broker-failover-secondary-account-routing skill.
"""
import unittest
from failover_router import (
    BrokerFailoverRouter, BrokerHealthStatus, MockBrokerAdapter, OrderRequest,
)


class TestBrokerFailoverRouter(unittest.TestCase):

    def setUp(self):
        self.primary = MockBrokerAdapter(name="ibkr", account_id="U1234567")
        self.secondary = MockBrokerAdapter(name="alpaca", account_id="ALP_98765")
        self.router = BrokerFailoverRouter(
            primary_broker=self.primary,
            secondary_broker=self.secondary,
            max_consecutive_failures=3,
        )
        self.router.register_symbol_map("AAPL", "AAPL STK SMART", "AAPL")

    def test_normal_primary_routing(self):
        order = OrderRequest(symbol="AAPL", action="BUY", quantity=10.0)
        result = self.router.submit_order(order)

        self.assertEqual(result.broker_name, "ibkr")
        self.assertEqual(result.account_id, "U1234567")
        self.assertEqual(result.symbol, "AAPL STK SMART")
        self.assertEqual(len(self.primary.executed_orders), 1)
        self.assertEqual(len(self.secondary.executed_orders), 0)

    def test_automatic_secondary_rerouting_on_failure(self):
        self.primary.should_fail = True
        order = OrderRequest(symbol="AAPL", action="BUY", quantity=10.0)

        # 1st failure
        self.router.submit_order(order)
        self.assertEqual(self.router.primary_failures, 1)

        # 2nd failure
        self.router.submit_order(order)
        self.assertEqual(self.router.primary_failures, 2)

        # 3rd failure trips breaker and reroutes to secondary
        res = self.router.submit_order(order)
        self.assertEqual(self.router.primary_status, BrokerHealthStatus.DOWN)
        self.assertEqual(self.router.active_broker_name, "alpaca")
        self.assertEqual(res.broker_name, "alpaca")
        self.assertEqual(res.symbol, "AAPL")
        self.assertEqual(len(self.secondary.executed_orders), 3)


if __name__ == "__main__":
    unittest.main()
