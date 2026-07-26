import unittest
import time
from circuit_breaker import CircuitBreaker, CircuitState, CircuitBreakerOpenException

class TestCircuitBreaker(unittest.TestCase):

    def setUp(self):
        # 3 failures to trip, 0.1 second recovery timeout
        self.cb = CircuitBreaker(failure_threshold=3, recovery_timeout_sec=0.1, expected_exceptions=(ConnectionError,))

    def _mock_success(self):
        return "SUCCESS"

    def _mock_failure(self):
        raise ConnectionError("Service Down")

    def _mock_business_logic_error(self):
        raise ValueError("Invalid Input")

    def test_closed_state_success(self):
        res = self.cb.call(self._mock_success)
        self.assertEqual(res, "SUCCESS")
        self.assertEqual(self.cb.state, CircuitState.CLOSED)

    def test_business_error_does_not_trip_circuit(self):
        # ValueError is not in expected_exceptions
        with self.assertRaises(ValueError):
            self.cb.call(self._mock_business_logic_error)
            
        self.assertEqual(self.cb.failure_count, 0)
        self.assertEqual(self.cb.state, CircuitState.CLOSED)

    def test_trip_to_open_state(self):
        # Fail 3 times
        for _ in range(3):
            with self.assertRaises(ConnectionError):
                self.cb.call(self._mock_failure)
                
        self.assertEqual(self.cb.state, CircuitState.OPEN)
        
        # 4th call should raise CircuitBreakerOpenException immediately
        with self.assertRaises(CircuitBreakerOpenException):
            self.cb.call(self._mock_success) # Even if it would succeed, the circuit blocks it

    def test_half_open_recovery(self):
        # Trip the circuit
        for _ in range(3):
            try:
                self.cb.call(self._mock_failure)
            except ConnectionError:
                pass
                
        self.assertEqual(self.cb.state, CircuitState.OPEN)
        
        # Wait for recovery timeout
        time.sleep(0.15)
        
        # Next call should be permitted (Half-Open)
        res = self.cb.call(self._mock_success)
        
        # After success, should reset to Closed
        self.assertEqual(res, "SUCCESS")
        self.assertEqual(self.cb.state, CircuitState.CLOSED)
        self.assertEqual(self.cb.failure_count, 0)

    def test_half_open_failure_returns_to_open(self):
        # Trip the circuit
        for _ in range(3):
            try:
                self.cb.call(self._mock_failure)
            except ConnectionError:
                pass
                
        # Wait for recovery timeout
        time.sleep(0.15)
        
        # Next call is permitted (Half-Open) but fails again
        with self.assertRaises(ConnectionError):
            self.cb.call(self._mock_failure)
            
        # Circuit should immediately return to Open without needing 3 more failures
        self.assertEqual(self.cb.state, CircuitState.OPEN)

if __name__ == '__main__':
    unittest.main()
