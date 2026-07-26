import time
import logging
from enum import Enum
from typing import Callable, Any, Tuple, Type

logger = logging.getLogger(__name__)

class CircuitState(Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

class CircuitBreakerOpenException(Exception):
    """Raised when attempting to execute a call while the circuit is open."""
    pass

class CircuitBreaker:
    """
    Software circuit breaker to prevent cascading failures.
    """
    def __init__(self, failure_threshold: int = 3, recovery_timeout_sec: float = 10.0, expected_exceptions: Tuple[Type[Exception], ...] = (Exception,)):
        self.failure_threshold = failure_threshold
        self.recovery_timeout_sec = recovery_timeout_sec
        self.expected_exceptions = expected_exceptions
        
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0.0

    def call(self, func: Callable, *args, **kwargs) -> Any:
        self._update_state()
        
        if self.state == CircuitState.OPEN:
            raise CircuitBreakerOpenException("Circuit is OPEN. Fast-failing request.")
            
        try:
            result = func(*args, **kwargs)
            # If successful in half-open, reset completely
            if self.state == CircuitState.HALF_OPEN:
                self._reset()
            return result
            
        except self.expected_exceptions as e:
            self._handle_failure()
            raise e

    def _update_state(self):
        """Transitions OPEN to HALF_OPEN if the timeout has elapsed."""
        if self.state == CircuitState.OPEN:
            time_since_failure = time.time() - self.last_failure_time
            if time_since_failure >= self.recovery_timeout_sec:
                self.state = CircuitState.HALF_OPEN
                logger.info("CircuitBreaker transitioned to HALF_OPEN.")

    def _handle_failure(self):
        """Increments failure count and trips circuit if threshold is reached."""
        if self.state == CircuitState.HALF_OPEN:
            # If we fail during half-open, immediately trip back to open
            self.state = CircuitState.OPEN
            self.last_failure_time = time.time()
            logger.warning("CircuitBreaker test request failed. Transitioned back to OPEN.")
            return

        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.critical(f"CircuitBreaker tripped to OPEN after {self.failure_count} failures.")

    def _reset(self):
        """Resets the circuit back to normal."""
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        logger.info("CircuitBreaker recovered and transitioned to CLOSED.")
