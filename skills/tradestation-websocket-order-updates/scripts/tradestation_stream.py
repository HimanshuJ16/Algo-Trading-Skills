"""
tradestation-websocket-order-updates: Production-grade TradeStation WebAPI order stream consumer,
heartbeat tracker, REST gap reconciliation on reconnect, and fill deduplicator.
"""
from dataclasses import dataclass, field
import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class TradeStationStreamError(RuntimeError):
    """Raised when stream processing or REST gap reconciliation fails."""
    pass


@dataclass
class TradeStationOrderUpdate:
    order_id: str
    status: str
    filled_quantity: float
    average_price: float
    timestamp: float = field(default_factory=time.time)


class TradeStationStreamManager:
    """
    Manages TradeStation streaming WebSocket order updates, heartbeat tracking,
    REST catch-up queries on reconnect, and fill event deduplication.
    """

    def __init__(self, account_id: str):
        self.account_id = account_id
        self.processed_signatures: Set[str] = set()
        self.last_update_timestamp: float = time.time()
        self.is_connected: bool = False

    def make_event_signature(self, order_id: str, status: str, filled_qty: float) -> str:
        return f"{order_id}:{status}:{filled_qty:.4f}"

    def is_duplicate(self, order_id: str, status: str, filled_qty: float) -> bool:
        sig = self.make_event_signature(order_id, status, filled_qty)
        if sig in self.processed_signatures:
            return True
        self.processed_signatures.add(sig)
        return False

    def parse_stream_message(self, message_str: str) -> Optional[TradeStationOrderUpdate]:
        """
        Parses incoming stream message frame (NDJSON). Returns TradeStationOrderUpdate
        or None if frame is a heartbeat/ping message.
        """
        message_str = message_str.strip()
        if not message_str:
            return None

        try:
            data = json.loads(message_str)
        except Exception as e:
            logger.error(f"Error decoding TradeStation stream JSON: {e}")
            return None

        # Filter Heartbeats
        if "Heartbeat" in data or data.get("StreamStatus") == "HEARTBEAT":
            logger.debug("TradeStation stream heartbeat received.")
            return None

        order_id = str(data.get("OrderID", data.get("OrderID", "")))
        status = str(data.get("Status", data.get("OrderStatus", "")))
        filled_qty = float(data.get("FilledQuantity", data.get("ExecutedQuantity", 0)))
        avg_price = float(data.get("AveragePrice", data.get("Price", 0.0)))
        ts_str = data.get("Timestamp", "")

        if not order_id:
            return None

        ts = time.time()
        update = TradeStationOrderUpdate(
            order_id=order_id,
            status=status,
            filled_quantity=filled_qty,
            average_price=avg_price,
            timestamp=ts,
        )

        if not self.is_duplicate(order_id, status, filled_qty):
            self.last_update_timestamp = ts
            logger.info(f"Stream order update ingested: Order '{order_id}' Status '{status}' Filled {filled_qty}")
            return update
        else:
            logger.debug(f"Duplicate stream update skipped: '{order_id}'")
            return None

    def reconcile_missed_orders(
        self, rest_fetch_fn: Callable[[float], List[Dict[str, Any]]]
    ) -> List[TradeStationOrderUpdate]:
        """
        Called immediately upon WebSocket reconnection to fetch orders updated during downtime.
        """
        logger.info(f"Executing REST gap catch-up query since {self.last_update_timestamp}...")
        try:
            missed_raw_orders = rest_fetch_fn(self.last_update_timestamp)
        except Exception as e:
            raise TradeStationStreamError(f"REST catch-up query failed: {e}")

        reconciled: List[TradeStationOrderUpdate] = []
        for raw in missed_raw_orders:
            order_id = str(raw.get("OrderID", ""))
            status = str(raw.get("Status", ""))
            filled_qty = float(raw.get("FilledQuantity", 0))
            avg_price = float(raw.get("AveragePrice", 0.0))

            if order_id and not self.is_duplicate(order_id, status, filled_qty):
                update = TradeStationOrderUpdate(
                    order_id=order_id,
                    status=status,
                    filled_quantity=filled_qty,
                    average_price=avg_price,
                    timestamp=time.time(),
                )
                reconciled.append(update)
                logger.info(f"Reconciled missed order fill via REST: Order '{order_id}' Status '{status}'")

        logger.info(f"Gap reconciliation complete: {len(reconciled)} missed fill events applied.")
        return reconciled
