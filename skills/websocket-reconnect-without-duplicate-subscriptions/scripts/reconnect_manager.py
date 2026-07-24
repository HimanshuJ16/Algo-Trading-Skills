"""
websocket-reconnect-without-duplicate-subscriptions: Production-grade decoupled subscription state manager,
jittered exponential backoff reconnect engine, gap-window REST backfill trigger,
and consumer-level tick deduplication filter.
"""
from collections import deque
from dataclasses import dataclass, field
import logging
import random
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ReconnectEvent:
    disconnect_timestamp: float
    reconnect_timestamp: float
    gap_duration_sec: float
    subscribed_symbols_count: int
    backfill_executed: bool


class TickDeduplicator:
    """Sliding window tick deduplicator preventing duplicate tick processing."""

    def __init__(self, max_history: int = 10_000):
        self.seen_signatures: set = set()
        self.history_queue: deque = deque(maxlen=max_history)

    def is_duplicate(self, symbol: str, timestamp: float, seq_num: Optional[int] = None) -> bool:
        sig = f"{symbol}:{timestamp}:{seq_num if seq_num is not None else ''}"
        if sig in self.seen_signatures:
            return True

        if len(self.history_queue) == self.history_queue.maxlen:
            oldest = self.history_queue.popleft()
            self.seen_signatures.discard(oldest)

        self.history_queue.append(sig)
        self.seen_signatures.add(sig)
        return False


class WebSocketReconnectEngine:
    """
    Decoupled subscription state manager executing fresh resubscriptions,
    jittered backoff retries, and gap-window REST backfills on reconnect.
    """

    def __init__(
        self,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        jitter_pct: float = 0.20,
    ):
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter_pct = jitter_pct

        self.desired_symbols: Set[str] = set()
        self.last_disconnect: Optional[float] = None
        self.reconnect_history: List[ReconnectEvent] = []
        self.dedup = TickDeduplicator()

    def subscribe(self, symbol: str):
        self.desired_symbols.add(symbol.upper())

    def unsubscribe(self, symbol: str):
        self.desired_symbols.discard(symbol.upper())

    def on_disconnect(self):
        self.last_disconnect = time.time()
        logger.warning(f"WebSocket DISCONNECTED at {self.last_disconnect}. Desired symbols count: {len(self.desired_symbols)}")

    def calculate_backoff(self, attempt: int) -> float:
        """Calculates exponential backoff with +/-20% randomized jitter."""
        raw_delay = min(self.max_delay, self.base_delay * (2 ** (attempt - 1)))
        jitter = raw_delay * self.jitter_pct * (2 * random.random() - 1)
        return max(0.1, raw_delay + jitter)

    def on_reconnect(
        self,
        subscribe_fn: Callable[[List[str]], None],
        backfill_fn: Optional[Callable[[List[str], float, float], None]] = None,
    ) -> ReconnectEvent:
        now = time.time()
        gap_dur = (now - self.last_disconnect) if self.last_disconnect else 0.0
        backfill_done = False

        sorted_symbols = sorted(self.desired_symbols)

        # 1. Execute Gap Backfill if callback provided
        if backfill_fn and self.last_disconnect is not None:
            try:
                backfill_fn(sorted_symbols, self.last_disconnect, now)
                backfill_done = True
                logger.info(f"Gap backfill EXECUTED for {len(sorted_symbols)} symbols over {gap_dur:.2f}s gap.")
            except Exception as e:
                logger.error(f"Error during gap backfill execution: {e}")

        # 2. Resubscribe fresh from desired state set
        subscribe_fn(sorted_symbols)
        logger.info(f"WebSocket RECONNECTED. Resubscribed fresh to {len(sorted_symbols)} symbols.")

        event = ReconnectEvent(
            disconnect_timestamp=self.last_disconnect or now,
            reconnect_timestamp=now,
            gap_duration_sec=gap_dur,
            subscribed_symbols_count=len(sorted_symbols),
            backfill_executed=backfill_done,
        )
        self.reconnect_history.append(event)
        self.last_disconnect = None
        return event


# Backward compatibility class
class SubscriptionManager:

    def __init__(self):
        self.desired_symbols = set()
        self.last_disconnect = None
        self.gap_log = []

    def add_symbol(self, symbol):
        self.desired_symbols.add(symbol)

    def remove_symbol(self, symbol):
        self.desired_symbols.discard(symbol)

    def on_disconnect(self):
        self.last_disconnect = time.time()

    def on_reconnect(self, subscribe_fn, backfill_fn=None):
        if self.last_disconnect:
            gap = time.time() - self.last_disconnect
            self.gap_log.append(gap)
            if backfill_fn:
                backfill_fn(self.desired_symbols, gap)
        subscribe_fn(sorted(self.desired_symbols))
        self.last_disconnect = None
