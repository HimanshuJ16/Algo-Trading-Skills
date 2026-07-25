"""
consumer-group-rebalance-safety: Stream consumer group rebalance listener,
revocation in-flight batch flusher, and partition assignment offset manager.
"""
from dataclasses import dataclass, field
import logging
import time
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class RebalanceState(Enum):
    NORMAL = "NORMAL"
    REVOKING = "REVOKING"
    REBALANCING = "REBALANCING"
    ASSIGNING = "ASSIGNING"


@dataclass
class RebalanceEventReport:
    state: RebalanceState
    revoked_partitions: List[int]
    assigned_partitions: List[int]
    flushed_records_count: int
    committed_offsets: Dict[int, int]
    is_safe: bool
    message: str


class ConsumerGroupRebalanceGuard:
    """
    Manages partition rebalance lifecycle events (Kafka / Redis Streams),
    flushing in-flight batches and committing offsets before partition revocation.
    """

    def __init__(self, group_id: str = "strategy-consumer-group"):
        self.group_id = group_id
        self.state = RebalanceState.NORMAL
        self.assigned_partitions: Set[int] = set()
        self.committed_offsets: Dict[int, int] = {}  # partition -> offset
        self.in_flight_batches: Dict[int, List[Dict[str, Any]]] = {}  # partition -> records

    def on_partitions_revoked(
        self,
        revoked_partitions: List[int],
        flush_fn: Optional[Callable[[int, List[Dict[str, Any]]], int]] = None,
    ) -> RebalanceEventReport:
        """
        Invoked when stream broker initiates partition revocation. Flushes in-flight
        records and commits offsets before partitions are unassigned.
        """
        self.state = RebalanceState.REVOKING
        logger.warning(f"Rebalance Guard [{self.group_id}]: Revoking partitions {revoked_partitions}. Flushing in-flight batches...")

        flushed_count = 0
        committed: Dict[int, int] = {}

        for p in revoked_partitions:
            records = self.in_flight_batches.pop(p, [])
            if records and flush_fn:
                # Flush batch for partition p
                last_offset = flush_fn(p, records)
                flushed_count += len(records)
                self.committed_offsets[p] = last_offset
                committed[p] = last_offset
                logger.info(f"Flushed & Committed Partition {p} Offset {last_offset} during revocation.")

            self.assigned_partitions.discard(p)

        self.state = RebalanceState.REBALANCING
        msg = f"Partition revocation complete. Flushed {flushed_count} records across {len(revoked_partitions)} partitions."
        logger.info(msg)

        return RebalanceEventReport(
            state=self.state,
            revoked_partitions=revoked_partitions,
            assigned_partitions=list(self.assigned_partitions),
            flushed_records_count=flushed_count,
            committed_offsets=committed,
            is_safe=True,
            message=msg,
        )

    def on_partitions_assigned(self, newly_assigned: List[int]) -> RebalanceEventReport:
        """
        Invoked when stream broker finishes partition assignment for this consumer node.
        """
        self.state = RebalanceState.ASSIGNING
        logger.info(f"Rebalance Guard [{self.group_id}]: Assigned new partitions {newly_assigned}.")

        for p in newly_assigned:
            self.assigned_partitions.add(p)
            if p not in self.in_flight_batches:
                self.in_flight_batches[p] = []

        self.state = RebalanceState.NORMAL
        msg = f"Partition assignment complete. Active partitions: {sorted(list(self.assigned_partitions))}"
        logger.info(msg)

        return RebalanceEventReport(
            state=self.state,
            revoked_partitions=[],
            assigned_partitions=sorted(list(self.assigned_partitions)),
            flushed_records_count=0,
            committed_offsets=self.committed_offsets,
            is_safe=True,
            message=msg,
        )

    def buffer_in_flight_record(self, partition: int, record: Dict[str, Any]) -> bool:
        """Buffers an in-flight record for partition if partition is assigned."""
        if partition not in self.assigned_partitions or self.state != RebalanceState.NORMAL:
            logger.warning(f"Record rejected: Partition {partition} not assigned or state={self.state.value}")
            return False

        if partition not in self.in_flight_batches:
            self.in_flight_batches[partition] = []

        self.in_flight_batches[partition].append(record)
        return True
