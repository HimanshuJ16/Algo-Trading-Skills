import time
import logging
from dataclasses import dataclass
from typing import Dict, List, Set, Optional

logger = logging.getLogger(__name__)

class PartitionRevokedException(Exception):
    """Raised when an application attempts to process messages on a revoked partition."""
    pass

class DuplicateMessageException(Exception):
    """Raised when a duplicate message is detected via idempotency key."""
    pass

@dataclass
class StreamMessage:
    partition: int
    offset: int
    idempotency_key: str              # Unique order_id / event_id
    payload: Dict

class ConsumerGroupRebalanceGuard:
    """
    Manages partition assignment/revocation lifecycle, enforces thread fencing,
    synchronously flushes pending buffers, and detects rebalance storms.
    """
    def __init__(self, rebalance_storm_threshold_count: int = 3, rebalance_window_sec: float = 60.0):
        self.rebalance_storm_threshold_count = rebalance_storm_threshold_count
        self.rebalance_window_sec = rebalance_window_sec

        self.active_partitions: Set[int] = set()
        self.processed_idempotency_keys: Set[str] = set()
        self.in_flight_buffer: Dict[int, List[StreamMessage]] = {}
        self.committed_offsets: Dict[int, int] = {}
        self.rebalance_timestamps: List[float] = []

    def _track_rebalance_storm(self):
        now = time.time()
        self.rebalance_timestamps.append(now)
        # Filter timestamps within window
        self.rebalance_timestamps = [
            ts for ts in self.rebalance_timestamps 
            if now - ts <= self.rebalance_window_sec
        ]
        
        if len(self.rebalance_timestamps) >= self.rebalance_storm_threshold_count:
            logger.error(
                f"REBALANCE STORM DETECTED: {len(self.rebalance_timestamps)} rebalances "
                f"occurred in the last {self.rebalance_window_sec}s. Consumer group may be unstable!"
            )

    def on_partitions_assigned(self, partitions: List[int]):
        """
        Callback executed when new partitions are assigned to this worker.
        """
        self._track_rebalance_storm()
        for p in partitions:
            self.active_partitions.add(p)
            if p not in self.in_flight_buffer:
                self.in_flight_buffer[p] = []
        logger.info(f"Partitions Assigned: {partitions}. Current Active: {sorted(list(self.active_partitions))}")

    def on_partitions_revoked(self, partitions: List[int]):
        """
        Callback executed when partitions are revoked.
        Step 1: Immediately fence worker from accepting new messages on these partitions.
        Step 2: Flush in-flight execution buffers.
        Step 3: Synchronously commit offsets.
        """
        logger.warning(f"Partitions Revoked: {partitions}. Initiating fencing and flush protocol.")
        
        for p in partitions:
            # Step 1: Fence Partition
            if p in self.active_partitions:
                self.active_partitions.remove(p)

            # Step 2: Flush in-flight buffer
            if p in self.in_flight_buffer and self.in_flight_buffer[p]:
                flushed_count = len(self.in_flight_buffer[p])
                last_offset = self.in_flight_buffer[p][-1].offset
                self.committed_offsets[p] = last_offset
                self.in_flight_buffer[p].clear()
                logger.info(f"Partition {p} flushed {flushed_count} messages. Synchronous Offset Committed: {last_offset}")

    def process_message(self, message: StreamMessage):
        """
        Processes an incoming streaming order/event message safely.
        """
        # 1. Fencing Check: Reject if partition was revoked
        if message.partition not in self.active_partitions:
            raise PartitionRevokedException(
                f"Cannot process message on Partition {message.partition}. Partition is revoked/fenced."
            )

        # 2. Idempotency Check: Reject if already processed
        if message.idempotency_key in self.processed_idempotency_keys:
            raise DuplicateMessageException(
                f"Duplicate message detected for key {message.idempotency_key}. Dropping to prevent double execution."
            )

        # 3. Process & Buffer
        self.processed_idempotency_keys.add(message.idempotency_key)
        self.in_flight_buffer[message.partition].append(message)
        self.committed_offsets[message.partition] = message.offset
        logger.debug(f"Processed message {message.idempotency_key} on partition {message.partition} at offset {message.offset}")
