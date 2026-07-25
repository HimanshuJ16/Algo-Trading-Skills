"""
broker-failover-secondary-account-routing: Dynamic multi-account failover router that
detects primary broker degradation and seamlessly redirects order flow to a secondary account.
"""
from dataclasses import dataclass, field
import logging
import time
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class BrokerHealthStatus(Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"


@dataclass
class OrderRequest:
    symbol: str
    action: str  # "BUY" or "SELL"
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
    """Mock broker adapter interface for testing failover routing."""

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
    Reroutes order flow to a secondary broker account when primary broker suffers
    connection errors or degradation.
    """

    def __init__(
        self,
        primary_broker: MockBrokerAdapter,
        secondary_broker: MockBrokerAdapter,
        max_consecutive_failures: int = 3,
    ):
        self.primary_broker = primary_broker
        self.secondary_broker = secondary_broker
        self.max_consecutive_failures = max_consecutive_failures

        self.primary_failures = 0
        self.primary_status = BrokerHealthStatus.HEALTHY
        self.active_broker_name = primary_broker.name
        self.symbol_mapping: Dict[str, Dict[str, str]] = {}  # symbol -> {primary: sys1, secondary: sys2}

    def register_symbol_map(self, canonical_symbol: str, primary_symbol: str, secondary_symbol: str) -> None:
        """Maps canonical symbol names to broker-specific symbols."""
        self.symbol_mapping[canonical_symbol] = {
            self.primary_broker.name: primary_symbol,
            self.secondary_broker.name: secondary_symbol,
        }

    def _get_broker_symbol(self, canonical_symbol: str, broker_name: str) -> str:
        if canonical_symbol in self.symbol_mapping:
            return self.symbol_mapping[canonical_symbol].get(broker_name, canonical_symbol)
        return canonical_symbol

    def submit_order(self, order: OrderRequest) -> OrderResult:
        """
        Submits order to active broker. Automatically trips breaker and reroutes
        to secondary broker if primary fails consecutively.
        """
        # If primary is HEALTHY, attempt primary
        if self.primary_status == BrokerHealthStatus.HEALTHY:
            try:
                mapped_order = OrderRequest(
                    symbol=self._get_broker_symbol(order.symbol, self.primary_broker.name),
                    action=order.action,
                    quantity=order.quantity,
                    order_type=order.order_type,
                    limit_price=order.limit_price,
                )
                result = self.primary_broker.place_order(mapped_order)
                self.primary_failures = 0
                return result
            except Exception as e:
                self.primary_failures += 1
                logger.warning(f"Primary broker failure ({self.primary_failures}/{self.max_consecutive_failures}): {e}")

                if self.primary_failures >= self.max_consecutive_failures:
                    self.primary_status = BrokerHealthStatus.DOWN
                    self.active_broker_name = self.secondary_broker.name
                    logger.error(
                        f"PRIMARY BROKER FAILED ({self.primary_broker.name}). "
                        f"Tripping circuit breaker! Rerouting all orders to SECONDARY BROKER ({self.secondary_broker.name})."
                    )

        # Route to secondary broker
        mapped_order = OrderRequest(
            symbol=self._get_broker_symbol(order.symbol, self.secondary_broker.name),
            action=order.action,
            quantity=order.quantity,
            order_type=order.order_type,
            limit_price=order.limit_price,
        )
        return self.secondary_broker.place_order(mapped_order)

    def reset_primary() -> None:
        """Resets primary broker status back to HEALTHY after manual/probed recovery."""
        self.primary_failures = 0
        self.primary_status = BrokerHealthStatus.HEALTHY
        self.active_broker_name = self.primary_broker.name
        logger.info(f"Primary broker {self.primary_broker.name} reset to HEALTHY.")
