"""
circuit-breaker-for-downstream-service-calls: Circuit breaker state machine (CLOSED -> OPEN -> HALF_OPEN)
with fail-fast execution and fallback handling for downstream microservice calls.
"""
from dataclasses import dataclass, field
import logging
import time
from enum import Enum
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpenException(Exception):
    """Raised when downstream call is rejected because Circuit Breaker is OPEN."""
    pass


@dataclass
class CircuitBreakerStatus:
    service_name: str
    state: CircuitState
    consecutive_failures: int
    total_calls: int
    total_failures: int
    last_state_change: float


class ServiceCircuitBreaker:
    """
    Implements Circuit Breaker resilience pattern for downstream service calls.
    """

    def __init__(
        self,
        service_name: str,
        failure_threshold: int = 3,
        cooldown_seconds: float = 5.0,
        half_open_trials: int = 2,
    ):
        self.service_name = service_name
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.half_open_trials = half_open_trials

        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self.total_calls = 0
        self.total_failures = 0
        self.last_failure_time = 0.0
        self.last_state_change = time.time()
        self.half_open_successes = 0

    def call(self, fn: Callable[..., Any], *args: Any, fallback_fn: Optional[Callable[..., Any]] = None, **kwargs: Any) -> Any:
        """
        Executes downstream function fn with circuit breaker isolation.
        """
        self.total_calls += 1
        now = time.time()

        # Check if OPEN state cooldown has expired -> Transition to HALF_OPEN
        if self.state == CircuitState.OPEN:
            if now - self.last_failure_time >= self.cooldown_seconds:
                self._transition_to(CircuitState.HALF_OPEN)
            else:
                logger.warning(f"Circuit Breaker for '{self.service_name}' is OPEN. Failing fast.")
                if fallback_fn:
                    return fallback_fn(*args, **kwargs)
                raise CircuitBreakerOpenException(f"Circuit Breaker for '{self.service_name}' is OPEN.")

        try:
            result = fn(*args, **kwargs)
            self._on_success()
            return result
        except Exception as exc:
            self._on_failure(exc)
            if fallback_fn:
                logger.info(f"Downstream call failed. Invoking fallback for '{self.service_name}'.")
                return fallback_fn(*args, **kwargs)
            raise exc

    def _on_success(self) -> None:
        if self.state == CircuitState.HALF_OPEN:
            self.half_open_successes += 1
            if self.half_open_successes >= self.half_open_trials:
                self._transition_to(CircuitState.CLOSED)
                self.consecutive_failures = 0
        elif self.state == CircuitState.CLOSED:
            self.consecutive_failures = 0

    def _on_failure(self, exc: Exception) -> None:
        self.total_failures += 1
        self.consecutive_failures += 1
        self.last_failure_time = time.time()

        logger.error(f"Downstream call to '{self.service_name}' failed ({type(exc).__name__}: {exc}). Failure count: {self.consecutive_failures}")

        if self.state in (CircuitState.CLOSED, CircuitState.HALF_OPEN):
            if self.consecutive_failures >= self.failure_threshold:
                self._transition_to(CircuitState.OPEN)

    def _transition_to(self, new_state: CircuitState) -> None:
        old_state = self.state
        self.state = new_state
        self.last_state_change = time.time()
        if new_state == CircuitState.HALF_OPEN:
            self.half_open_successes = 0

        logger.warning(f"Circuit Breaker '{self.service_name}': State transition {old_state.value} -> {new_state.value}")

    def get_status(self) -> CircuitBreakerStatus:
        return CircuitBreakerStatus(
            service_name=self.service_name,
            state=self.state,
            consecutive_failures=self.consecutive_failures,
            total_calls=self.total_calls,
            total_failures=self.total_failures,
            last_state_change=self.last_state_change,
        )
