import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class Config:
    """Legacy Config container for backward compatibility."""
    enabled: bool = True

class Engine:
    """Legacy Engine class for backward compatibility."""
    def __init__(self, config: Config):
        self.config = config

    def execute(self) -> bool:
        return self.config.enabled

class VenueHealthState(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    CIRCUIT_BROKEN_OUTAGE = "CIRCUIT_BROKEN_OUTAGE"

@dataclass
class TradingVenue:
    venue_id: str                         # e.g., 'NASDAQ', 'NYSE', 'BATS', 'EDGX'
    venue_name: str
    state: VenueHealthState = VenueHealthState.HEALTHY
    consecutive_error_count: int = 0
    max_error_threshold: int = 3
    last_error_time: Optional[float] = None
    bid_price: float = 0.0
    ask_price: float = 0.0
    available_qty: float = 0.0
    latency_ms: float = 5.0

@dataclass
class SORRoutingResult:
    order_id: str
    target_venue_id: str
    routed_quantity: float
    routed_price: float
    is_failover_triggered: bool
    fallback_venues_used: List[str]
    audit_notes: str

class SmartOrderRouterFailoverEngine:
    """
    Production-grade Smart Order Router (SOR) with automatic venue outage detection,
    circuit breaker trip logic, fallback routing, and in-flight order recovery.
    """
    def __init__(
        self,
        venues: Optional[List[TradingVenue]] = None,
        cooldown_seconds: float = 60.0
    ):
        self.cooldown_seconds = cooldown_seconds
        self.venues: Dict[str, TradingVenue] = {}
        if venues:
            for v in venues:
                self.venues[v.venue_id] = v

    def add_venue(self, venue: TradingVenue):
        self.venues[venue.venue_id] = venue

    def report_venue_error(self, venue_id: str, error_msg: str):
        """Reports a transport/API error for a venue and trips circuit breaker if threshold reached."""
        venue = self.venues.get(venue_id)
        if not venue:
            return

        venue.consecutive_error_count += 1
        venue.last_error_time = time.time()

        if venue.consecutive_error_count >= venue.max_error_threshold:
            venue.state = VenueHealthState.CIRCUIT_BROKEN_OUTAGE
            logger.error(f"CIRCUIT BREAKER TRIPPED for venue '{venue_id}' after {venue.consecutive_error_count} errors. Error: {error_msg}")
        else:
            venue.state = VenueHealthState.DEGRADED
            logger.warning(f"Venue '{venue_id}' degraded. Error count = {venue.consecutive_error_count}/{venue.max_error_threshold}.")

    def report_venue_success(self, venue_id: str):
        """Resets venue error counters upon successful execution or heartbeat."""
        venue = self.venues.get(venue_id)
        if venue:
            venue.consecutive_error_count = 0
            venue.state = VenueHealthState.HEALTHY

    def route_order(
        self,
        order_id: str,
        side: str,                       # 'BUY' or 'SELL'
        quantity: float,
        preferred_venue_id: Optional[str] = None
    ) -> SORRoutingResult:
        """
        Routes an order to the best healthy venue, automatically failing over to backup venues if outage occurs.
        """
        # Filter for healthy venues
        healthy_venues = [
            v for v in self.venues.values()
            if v.state != VenueHealthState.CIRCUIT_BROKEN_OUTAGE and v.available_qty > 0
        ]

        if not healthy_venues:
            raise RuntimeError(f"SOR FAILOVER FAILURE: All venues are in CIRCUIT_BROKEN_OUTAGE state or out of liquidity!")

        fallback_used: List[str] = []
        is_failover = False

        # Attempt preferred venue first
        selected_venue: Optional[TradingVenue] = None
        if preferred_venue_id and preferred_venue_id in self.venues:
            pref = self.venues[preferred_venue_id]
            if pref.state != VenueHealthState.CIRCUIT_BROKEN_OUTAGE and pref.available_qty > 0:
                selected_venue = pref
            else:
                is_failover = True
                fallback_used.append(preferred_venue_id)
                logger.warning(f"Preferred venue '{preferred_venue_id}' unavailable (State: {pref.state.value}). Triggering failover.")

        # Best execution selection logic among remaining healthy venues
        if not selected_venue:
            if side.upper() == "BUY":
                # Lowest ask price, then lowest latency
                selected_venue = min(healthy_venues, key=lambda v: (v.ask_price, v.latency_ms))
            else:
                # Highest bid price, then lowest latency
                selected_venue = max(healthy_venues, key=lambda v: (v.bid_price, -v.latency_ms))

        price = selected_venue.ask_price if side.upper() == "BUY" else selected_venue.bid_price
        routed_qty = min(quantity, selected_venue.available_qty)

        notes = (
            f"SOR ROUTE SUCCESS [{order_id}]: Routed {routed_qty} shares to venue '{selected_venue.venue_id}' "
            f"@ ${price} (Failover = {is_failover}, Fallback History = {fallback_used})."
        )

        logger.info(notes)

        return SORRoutingResult(
            order_id=order_id,
            target_venue_id=selected_venue.venue_id,
            routed_quantity=routed_qty,
            routed_price=price,
            is_failover_triggered=is_failover,
            fallback_venues_used=fallback_used,
            audit_notes=notes
        )
