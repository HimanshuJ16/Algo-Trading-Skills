import logging
import time
import threading
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)

class CircuitBreakerState(Enum):
    CLOSED = "CLOSED"         # Normal operation, primary broker is healthy
    OPEN = "OPEN"             # Primary is down, routing everything to secondary
    HALF_OPEN = "HALF_OPEN"   # Probing primary with a single test order

class BrokerHealthStatus(Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"

@dataclass
class OrderRequest:
    symbol: str
    action: str
    quantity: float
    order_type: str = "MARKET"
    limit_price: Optional[float] = None

@dataclass
class OrderResult:
    order_id: str
    broker_name: str
    account_id: str
    symbol: str
    action: str
    quantity: float
    status: str
    executed_at: float

class MockBrokerAdapter:
    def __init__(self, name: str, account_id: str, should_fail: bool = False):
        self.name = name
        self.account_id = account_id
        self.should_fail = should_fail
        self.executed_orders: List[OrderResult] = []

    def place_order(self, order: OrderRequest) -> OrderResult:
        if self.should_fail:
            raise RuntimeError(f"Broker {self.name} connection failed / HTTP 503 Service Unavailable.")
        
        res = OrderResult(
            order_id=f"{self.name.upper()}_ORD_{len(self.executed_orders) + 1}",
            broker_name=self.name,
            account_id=self.account_id,
            symbol=order.symbol,
            action=order.action,
            quantity=order.quantity,
            status="FILLED",
            executed_at=time.time(),
        )
        self.executed_orders.append(res)
        return res

class BrokerFailoverRouter:
    """
    Rigorously engineered failover router using a Circuit Breaker pattern.
    Supports concurrency using threading locks, exponential backoff principles (simulated via reset timeouts),
    and Half-Open probing for primary broker recovery.
    """
    def __init__(
        self,
        primary_broker: MockBrokerAdapter,
        secondary_broker: MockBrokerAdapter,
        max_consecutive_failures: int = 3,
        recovery_timeout_seconds: float = 60.0
    ):
        self.primary_broker = primary_broker
        self.secondary_broker = secondary_broker
        
        self.max_consecutive_failures = max_consecutive_failures
        self.recovery_timeout_seconds = recovery_timeout_seconds
        
        self.circuit_state = CircuitBreakerState.CLOSED
        self.primary_failures = 0
        self.last_failure_time = 0.0
        self.lock = threading.Lock()
        
        self.symbol_mapping: Dict[str, Dict[str, str]] = {}

    def register_symbol_map(self, canonical_symbol: str, primary_symbol: str, secondary_symbol: str) -> None:
        with self.lock:
            self.symbol_mapping[canonical_symbol] = {
                self.primary_broker.name: primary_symbol,
                self.secondary_broker.name: secondary_symbol,
            }

    def _get_broker_symbol(self, canonical_symbol: str, broker_name: str) -> str:
        with self.lock:
            if canonical_symbol in self.symbol_mapping:
                return self.symbol_mapping[canonical_symbol].get(broker_name, canonical_symbol)
            return canonical_symbol

    def _route_to_broker(self, broker: MockBrokerAdapter, order: OrderRequest) -> OrderResult:
        mapped_order = OrderRequest(
            symbol=self._get_broker_symbol(order.symbol, broker.name),
            action=order.action,
            quantity=order.quantity,
            order_type=order.order_type,
            limit_price=order.limit_price,
        )
        return broker.place_order(mapped_order)

    def submit_order(self, order: OrderRequest) -> OrderResult:
        with self.lock:
            current_state = self.circuit_state
            
            if current_state == CircuitBreakerState.OPEN:
                if (time.time() - self.last_failure_time) > self.recovery_timeout_seconds:
                    logger.info("Recovery timeout reached. Transitioning to HALF_OPEN to probe primary broker.")
                    self.circuit_state = CircuitBreakerState.HALF_OPEN
                    current_state = CircuitBreakerState.HALF_OPEN
            
        if current_state == CircuitBreakerState.CLOSED or current_state == CircuitBreakerState.HALF_OPEN:
            try:
                result = self._route_to_broker(self.primary_broker, order)
                
                with self.lock:
                    if self.circuit_state == CircuitBreakerState.HALF_OPEN:
                        logger.info("Primary broker probe successful. Transitioning to CLOSED.")
                    self.circuit_state = CircuitBreakerState.CLOSED
                    self.primary_failures = 0
                return result
            
            except Exception as e:
                with self.lock:
                    self.last_failure_time = time.time()
                    self.primary_failures += 1
                    
                    if self.circuit_state == CircuitBreakerState.HALF_OPEN:
                        logger.warning("Primary broker probe failed. Transitioning back to OPEN.")
                        self.circuit_state = CircuitBreakerState.OPEN
                    elif self.primary_failures >= self.max_consecutive_failures:
                        logger.error(f"Primary broker failures ({self.primary_failures}) exceeded threshold. Circuit OPEN.")
                        self.circuit_state = CircuitBreakerState.OPEN
                    else:
                        logger.warning(f"Primary broker failure ({self.primary_failures}/{self.max_consecutive_failures}): {e}")
        
        # Route to secondary
        return self._route_to_broker(self.secondary_broker, order)

    def manual_reset(self) -> None:
        """Forcibly resets the circuit breaker to CLOSED state."""
        with self.lock:
            self.circuit_state = CircuitBreakerState.CLOSED
            self.primary_failures = 0
            logger.info("Circuit breaker manually reset to CLOSED.")
