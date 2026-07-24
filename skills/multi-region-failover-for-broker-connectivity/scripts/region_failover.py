"""
multi-region-failover-for-broker-connectivity: Automatic failover to backup
broker endpoint when primary connection degrades.
"""
from dataclasses import dataclass, field
import logging
import time
from enum import Enum
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class EndpointState(Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"


@dataclass
class BrokerEndpoint:
    name: str
    region: str
    url: str
    is_primary: bool
    state: EndpointState = EndpointState.HEALTHY
    consecutive_failures: int = 0
    last_check_time: float = 0.0


@dataclass
class FailoverEvent:
    timestamp: float
    from_endpoint: str
    to_endpoint: str
    reason: str


class RegionFailoverManager:
    """
    Manages multi-region broker connectivity failover with health probing,
    automatic switchover, and cooldown-based failback.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_seconds: float = 60.0,
        health_check_fn: Optional[Callable[[BrokerEndpoint], bool]] = None,
    ):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.health_check_fn = health_check_fn or (lambda ep: True)
        self.endpoints: Dict[str, BrokerEndpoint] = {}
        self.active_endpoint: Optional[str] = None
        self.last_failover_time: float = 0.0
        self.failover_history: List[FailoverEvent] = []

    def register_endpoint(self, name: str, region: str, url: str, is_primary: bool = False) -> None:
        """Register a broker endpoint."""
        self.endpoints[name] = BrokerEndpoint(
            name=name, region=region, url=url, is_primary=is_primary,
        )
        if is_primary and self.active_endpoint is None:
            self.active_endpoint = name

    def probe_health(self, endpoint_name: str) -> EndpointState:
        """Probe an endpoint's health and update its state."""
        if endpoint_name not in self.endpoints:
            raise ValueError(f"Unknown endpoint: '{endpoint_name}'")

        ep = self.endpoints[endpoint_name]
        ep.last_check_time = time.time()

        healthy = self.health_check_fn(ep)
        if healthy:
            ep.consecutive_failures = 0
            ep.state = EndpointState.HEALTHY
        else:
            ep.consecutive_failures += 1
            if ep.consecutive_failures >= self.failure_threshold:
                ep.state = EndpointState.DOWN
            else:
                ep.state = EndpointState.DEGRADED

        return ep.state

    def evaluate_failover(self) -> Optional[FailoverEvent]:
        """Check if failover is needed and execute if so."""
        if self.active_endpoint is None:
            return None

        active_ep = self.endpoints[self.active_endpoint]

        # Check if active endpoint is down
        if active_ep.state != EndpointState.DOWN:
            return None

        # Find a healthy backup
        for name, ep in self.endpoints.items():
            if name == self.active_endpoint:
                continue
            if ep.state == EndpointState.HEALTHY:
                return self._execute_failover(active_ep.name, name, "Primary endpoint DOWN")

        logger.error("ALL endpoints are unhealthy. No failover target available.")
        return None

    def evaluate_failback(self) -> Optional[FailoverEvent]:
        """Check if failback to primary is possible after cooldown."""
        now = time.time()
        if (now - self.last_failover_time) < self.cooldown_seconds:
            return None  # Still in cooldown

        # Find the primary endpoint
        for name, ep in self.endpoints.items():
            if ep.is_primary and name != self.active_endpoint and ep.state == EndpointState.HEALTHY:
                return self._execute_failover(
                    self.active_endpoint, name, "Primary recovered — failback"
                )

        return None

    def _execute_failover(self, from_name: str, to_name: str, reason: str) -> FailoverEvent:
        """Execute the failover switch."""
        event = FailoverEvent(
            timestamp=time.time(),
            from_endpoint=from_name,
            to_endpoint=to_name,
            reason=reason,
        )
        self.active_endpoint = to_name
        self.last_failover_time = time.time()
        self.failover_history.append(event)

        msg = f"FAILOVER: {from_name} -> {to_name} (reason: {reason})"
        logger.warning(msg)
        return event

    def get_active_endpoint(self) -> Optional[BrokerEndpoint]:
        """Return the currently active endpoint."""
        if self.active_endpoint:
            return self.endpoints[self.active_endpoint]
        return None
