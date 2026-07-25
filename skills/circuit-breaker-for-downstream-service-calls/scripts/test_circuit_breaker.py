"""
Unit tests for circuit-breaker-for-downstream-service-calls skill.
"""
import time
import unittest
from circuit_breaker import CircuitBreakerOpenException, CircuitState, ServiceCircuitBreaker


def mock_failing_service():
    raise ConnectionError("Database connection timeout")


def mock_healthy_service():
    return {"status": "ok", "risk_approved": True}


def mock_fallback_service():
    return {"status": "fallback", "risk_approved": True}


class TestServiceCircuitBreaker(unittest.TestCase):

    def setUp(self):
        self.breaker = ServiceCircuitBreaker(
            service_name="risk-db",
            failure_threshold=3,
            cooldown_seconds=1.0,
            half_open_trials=2,
        )

    def test_normal_operation_closed_state(self):
        res = self.breaker.call(mock_healthy_service)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(self.breaker.state, CircuitState.CLOSED)

    def test_trip_to_open_state_and_fail_fast(self):
        # 3 failures -> Trips breaker to OPEN
        for i in range(3):
            with self.assertRaises(ConnectionError):
                self.breaker.call(mock_failing_service)

        self.assertEqual(self.breaker.state, CircuitState.OPEN)

        # 4th call fails fast with CircuitBreakerOpenException
        with self.assertRaises(CircuitBreakerOpenException):
            self.breaker.call(mock_healthy_service)

    def test_fallback_execution_when_open(self):
        for i in range(3):
            with self.assertRaises(ConnectionError):
                self.breaker.call(mock_failing_service)

        # Execute call with fallback
        res = self.breaker.call(mock_healthy_service, fallback_fn=mock_fallback_service)
        self.assertEqual(res["status"], "fallback")

    def test_cooldown_and_half_open_recovery(self):
        for i in range(3):
            with self.assertRaises(ConnectionError):
                self.breaker.call(mock_failing_service)

        self.assertEqual(self.breaker.state, CircuitState.OPEN)
        time.sleep(1.1)  # Cooldown expires

        # 1st trial call -> HALF_OPEN
        res1 = self.breaker.call(mock_healthy_service)
        self.assertEqual(self.breaker.state, CircuitState.HALF_OPEN)

        # 2nd trial call -> Back to CLOSED
        res2 = self.breaker.call(mock_healthy_service)
        self.assertEqual(self.breaker.state, CircuitState.CLOSED)


if __name__ == "__main__":
    unittest.main()
