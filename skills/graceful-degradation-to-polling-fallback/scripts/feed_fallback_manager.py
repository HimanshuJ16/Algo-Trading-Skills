"""
graceful-degradation-to-polling-fallback: Production-grade market data feed degradation detector,
REST HTTP polling failover manager, handover tick deduplicator, and stream stabilization monitor.
"""
from dataclasses import dataclass, field
from enum import Enum
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class FeedMode(str, Enum):
    HEALTHY_WEBSOCKET = "HEALTHY_WEBSOCKET"
    DEGRADED_POLLING = "DEGRADED_POLLING"


@dataclass
class TickPayload:
    symbol: str
    price: float
    volume: float
    timestamp: float = field(default_factory=time.time)
    source: str = "WEBSOCKET"


class FeedFallbackManager:
    """
    Monitors WebSocket feed health, seamlessly transitions to REST polling when stream
    silence breaches threshold, deduplicates ticks, and restores WebSocket mode when stable.
    """

    def __init__(
        self,
        symbol: str,
        silence_timeout_seconds: float = 3.0,
        required_stabilization_ticks: int = 5,
    ):
        self.symbol = symbol
        self.silence_timeout_seconds = silence_timeout_seconds
        self.required_stabilization_ticks = required_stabilization_ticks

        self.feed_mode: FeedMode = FeedMode.HEALTHY_WEBSOCKET
        self.last_processed_timestamp: float = 0.0
        self.last_ws_tick_time: float = time.time()
        self.consecutive_ws_ticks: int = 0

    def ingest_websocket_tick(self, tick: TickPayload) -> Optional[TickPayload]:
        """Ingests incoming WebSocket tick update."""
        now = time.time()
        self.last_ws_tick_time = now
        self.consecutive_ws_ticks += 1

        # Check if recovering from DEGRADED_POLLING
        if self.feed_mode == FeedMode.DEGRADED_POLLING:
            if self.consecutive_ws_ticks >= self.required_stabilization_ticks:
                self.feed_mode = FeedMode.HEALTHY_WEBSOCKET
                logger.info(
                    f"WebSocket feed recovered and STABILIZED for '{self.symbol}'. Restoring HEALTHY_WEBSOCKET mode."
                )

        # Deduplicate tick timestamp
        if tick.timestamp <= self.last_processed_timestamp:
            logger.debug(f"Stale WebSocket tick skipped for '{self.symbol}': ts {tick.timestamp} <= last {self.last_processed_timestamp}")
            return None

        self.last_processed_timestamp = tick.timestamp
        return tick

    def check_feed_health(self) -> FeedMode:
        """Evaluates time since last WebSocket tick and transitions mode if silent."""
        now = time.time()
        elapsed = now - self.last_ws_tick_time

        if elapsed > self.silence_timeout_seconds and self.feed_mode == FeedMode.HEALTHY_WEBSOCKET:
            self.feed_mode = FeedMode.DEGRADED_POLLING
            self.consecutive_ws_ticks = 0
            logger.warning(
                f"WebSocket stream SILENT for {elapsed:.1f}s > {self.silence_timeout_seconds}s for '{self.symbol}'! Degrading to REST polling."
            )

        return self.feed_mode

    def poll_rest_fallback(
        self, rest_fetch_fn: Callable[[str], Optional[TickPayload]]
    ) -> Optional[TickPayload]:
        """Executes REST polling query when feed mode is DEGRADED_POLLING."""
        self.check_feed_health()

        if self.feed_mode != FeedMode.DEGRADED_POLLING:
            return None

        logger.debug(f"Executing REST polling query for '{self.symbol}'...")
        try:
            polled_tick = rest_fetch_fn(self.symbol)
        except Exception as e:
            logger.error(f"REST polling query failed for '{self.symbol}': {e}")
            return None

        if not polled_tick:
            return None

        polled_tick.source = "REST_POLLING"

        # Deduplicate against last processed timestamp
        if polled_tick.timestamp <= self.last_processed_timestamp:
            logger.debug(f"Deduplicated REST polled tick for '{self.symbol}'")
            return None

        self.last_processed_timestamp = polled_tick.timestamp
        logger.info(f"REST polled tick ingested for '{self.symbol}' at ${polled_tick.price:,.2f}")
        return polled_tick
