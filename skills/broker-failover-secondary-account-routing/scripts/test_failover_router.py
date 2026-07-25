import unittest
import time
from failover_router import (
    BrokerFailoverRouter, CircuitBreakerState, MockBrokerAdapter, OrderRequest,
)

class TestBrokerFailoverRouter(unittest.TestCase):
    def setUp(self):
        self.primary = MockBrokerAdapter(name="primary_broker", account_id="P123")
        self.secondary = MockBrokerAdapter(name="secondary_broker", account_id="S456")
        self.router = BrokerFailoverRouter(
            primary_broker=self.primary,
            secondary_broker=self.secondary,
            max_consecutive_failures=3,
            recovery_timeout_seconds=0.1  # very short for tests
        )
        self.router.register_symbol_map("AAPL", "AAPL.P", "AAPL.S")

    def test_normal_routing(self):
        order = OrderRequest(symbol="AAPL", action="BUY", quantity=100)
        res = self.router.submit_order(order)
        self.assertEqual(res.broker_name, "primary_broker")
        self.assertEqual(res.symbol, "AAPL.P")
        self.assertEqual(self.router.circuit_state, CircuitBreakerState.CLOSED)

    def test_circuit_breaker_trips_and_reroutes(self):
        self.primary.should_fail = True
        order = OrderRequest(symbol="AAPL", action="BUY", quantity=100)
        
        # First 2 failures
        self.router.submit_order(order)
        self.router.submit_order(order)
        self.assertEqual(self.router.circuit_state, CircuitBreakerState.CLOSED)
        
        # 3rd failure trips the breaker
        self.router.submit_order(order)
        self.assertEqual(self.router.circuit_state, CircuitBreakerState.OPEN)
        
        # Next order should be routed to secondary directly
        res = self.router.submit_order(order)
        self.assertEqual(res.broker_name, "secondary_broker")
        self.assertEqual(res.symbol, "AAPL.S")
        self.assertEqual(len(self.secondary.executed_orders), 4) # all 4 orders fell back to secondary

    def test_half_open_recovery(self):
        self.primary.should_fail = True
        order = OrderRequest(symbol="AAPL", action="BUY", quantity=100)
        
        # Trip the breaker
        for _ in range(3):
            self.router.submit_order(order)
            
        self.assertEqual(self.router.circuit_state, CircuitBreakerState.OPEN)
        
        # Wait for recovery timeout
        time.sleep(0.15)
        
        # The next submit will transition to HALF_OPEN and probe
        # If primary still fails, it goes back to OPEN
        self.router.submit_order(order)
        self.assertEqual(self.router.circuit_state, CircuitBreakerState.OPEN)
        
        # Wait again
        time.sleep(0.15)
        # Fix primary
        self.primary.should_fail = False
        
        # Next order probes (HALF_OPEN) and succeeds (CLOSED)
        res = self.router.submit_order(order)
        self.assertEqual(res.broker_name, "primary_broker")
        self.assertEqual(self.router.circuit_state, CircuitBreakerState.CLOSED)

if __name__ == "__main__":
    unittest.main()
