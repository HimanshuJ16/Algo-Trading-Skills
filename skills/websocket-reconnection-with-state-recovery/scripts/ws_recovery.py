"""
websocket-reconnection-with-state-recovery: Connection state machine, exponential backoff with jitter,
symbol re-subscription, and REST sequence gap-filling engine.
"""
from dataclasses import dataclass, field
import logging
import random
import time
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class ConnectionState(Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    AUTHENTICATED = "AUTHENTICATED"
    SUBSCRIBED = "SUBSCRIBED"
    RECOVERING_GAP = "RECOVERING_GAP"
    STREAMING = "STREAMING"


@dataclass
class WSMessage:
    symbol: str
    sequence_id: int
    data: Dict[str, Any]


class WebSocketStateRecoveryManager:
    """
    Manages WebSocket lifecycle transitions, backoff calculation with jitter,
    symbol channel re-subscription, and sequence gap recovery.
    """

    def __init__(
        self,
        base_backoff_sec: float = 1.0,
        max_backoff_sec: float = 30.0,
        jitter_factor: float = 0.5,
        rest_gap_fill_fn: Optional[Callable[[str, int, int], List[WSMessage]]] = None,
    ):
        self.state = ConnectionState.DISCONNECTED
        self.base_backoff_sec = base_backoff_sec
        self.max_backoff_sec = max_backoff_sec
        self.jitter_factor = jitter_factor
        self._rest_gap_fill_fn = rest_gap_fill_fn

        self.reconnect_attempts = 0
        self.subscribed_symbols: Set[str] = set()
        self.last_seen_sequence: Dict[str, int] = {}  # symbol -> last_seq_id
        self.processed_messages: List[WSMessage] = []

    def compute_next_backoff(self) -> float:
        """Calculates exponential backoff delay with randomized full jitter."""
        exp_delay = min(self.max_backoff_sec, self.base_backoff_sec * (2 ** self.reconnect_attempts))
        jitter = random.uniform(0, exp_delay * self.jitter_factor)
        return exp_delay + jitter

    def register_symbol_subscription(self, symbol: str) -> None:
        """Registers a symbol to be automatically re-subscribed upon reconnection."""
        self.subscribed_symbols.add(symbol.upper())

    def on_connection_lost(self, reason: str = "Network Disconnect") -> float:
        """Transitions state to DISCONNECTED and returns recommended backoff delay."""
        self.state = ConnectionState.DISCONNECTED
        self.reconnect_attempts += 1
        delay = self.compute_next_backoff()
        logger.warning(
            f"WebSocket connection lost ({reason}). Reconnect attempt #{self.reconnect_attempts}. "
            f"Waiting {delay:.2f}s before retry..."
        )
        return delay

    def on_connection_established(self) -> List[str]:
        """
        Transitions state to CONNECTING -> SUBSCRIBED and returns active symbols to re-subscribe.
        """
        self.state = ConnectionState.CONNECTING
        logger.info("WebSocket connected. Re-subscribing symbol channels...")
        self.state = ConnectionState.SUBSCRIBED
        return list(self.subscribed_symbols)

    def process_incoming_message(self, message: WSMessage) -> List[WSMessage]:
        """
        Processes incoming message. Detects sequence gaps, triggers REST gap recovery if needed,
        and returns ordered list of messages ready for consumption.
        """
        symbol = message.symbol.upper()
        seq = message.sequence_id
        emitted: List[WSMessage] = []

        last_seq = self.last_seen_sequence.get(symbol)

        if last_seq is not None and seq > last_seq + 1:
            gap_start = last_seq + 1
            gap_end = seq - 1
            logger.warning(f"SEQUENCE GAP DETECTED on {symbol}: Last seen {last_seq}, got {seq}. Missing [{gap_start}..{gap_end}]")

            self.state = ConnectionState.RECOVERING_GAP
            if self._rest_gap_fill_fn:
                gap_messages = self._rest_gap_fill_fn(symbol, gap_start, gap_end)
                for g_msg in gap_messages:
                    emitted.append(g_msg)
                    self.last_seen_sequence[g_msg.symbol.upper()] = g_msg.sequence_id
                logger.info(f"REST Gap Fill Recovered {len(gap_messages)} missing messages for {symbol}.")

        # Append current message
        self.state = ConnectionState.STREAMING
        emitted.append(message)
        self.last_seen_sequence[symbol] = seq
        self.processed_messages.extend(emitted)
        self.reconnect_attempts = 0  # Reset backoff on successful stream
        return emitted
