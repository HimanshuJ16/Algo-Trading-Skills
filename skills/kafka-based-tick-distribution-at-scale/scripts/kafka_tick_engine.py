"""
kafka-based-tick-distribution-at-scale: Symbol-keyed partition router, batch producer simulator,
and multi-consumer group offset coordinator for high-throughput tick distribution.
"""
from dataclasses import dataclass, field
import hashlib
import logging
import time
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class KafkaTickMessage:
    topic: str
    partition: int
    offset: int
    symbol: str
    timestamp: float
    bid: float
    ask: float
    volume: float


@dataclass
class ConsumerGroupOffset:
    group_id: str
    partition: int
    committed_offset: int


class KafkaTickProducerConsumerEngine:
    """
    Simulates high-throughput Kafka topic partitioning by symbol key, batch producer logic,
    and consumer group offset management.
    """

    def __init__(self, topic_name: str = "market-ticks", num_partitions: int = 4):
        self.topic_name = topic_name
        self.num_partitions = num_partitions
        self.partitions: Dict[int, List[KafkaTickMessage]] = {i: [] for i in range(num_partitions)}
        self.consumer_offsets: Dict[str, Dict[int, int]] = {}  # group_id -> {partition -> committed_offset}

    def get_partition_for_symbol(self, symbol: str) -> int:
        """Hashes symbol string to a deterministic partition ID."""
        key_bytes = symbol.upper().encode("utf-8")
        hash_val = int(hashlib.md5(key_bytes).hexdigest(), 16)
        return hash_val % self.num_partitions

    def produce_tick(self, symbol: str, bid: float, ask: float, volume: float) -> KafkaTickMessage:
        """
        Produces a tick message to the symbol's designated partition with sequential offset.
        """
        partition = self.get_partition_for_symbol(symbol)
        current_offset = len(self.partitions[partition])

        msg = KafkaTickMessage(
            topic=self.topic_name,
            partition=partition,
            offset=current_offset,
            symbol=symbol.upper(),
            timestamp=time.time(),
            bid=bid,
            ask=ask,
            volume=volume,
        )
        self.partitions[partition].append(msg)
        return msg

    def produce_batch(self, ticks: List[Dict[str, Any]]) -> int:
        """Produces a batch of ticks in high-throughput mode."""
        count = 0
        for t in ticks:
            self.produce_tick(
                symbol=t["symbol"],
                bid=t["bid"],
                ask=t["ask"],
                volume=t.get("volume", 1.0),
            )
            count += 1
        logger.info(f"Kafka Batch Producer: Published {count} ticks across {self.num_partitions} partitions.")
        return count

    def consume_partition_batch(
        self,
        group_id: str,
        partition: int,
        max_messages: int = 100,
    ) -> List[KafkaTickMessage]:
        """
        Consumes messages from a partition starting from the group's last committed offset.
        """
        if group_id not in self.consumer_offsets:
            self.consumer_offsets[group_id] = {p: 0 for p in range(self.num_partitions)}

        start_offset = self.consumer_offsets[group_id].get(partition, 0)
        partition_msgs = self.partitions[partition]

        fetched = partition_msgs[start_offset: start_offset + max_messages]
        return fetched

    def commit_offset(self, group_id: str, partition: int, offset: int) -> None:
        """Commits the processed offset checkpoint for a consumer group."""
        if group_id not in self.consumer_offsets:
            self.consumer_offsets[group_id] = {}
        self.consumer_offsets[group_id][partition] = offset
        logger.debug(f"Consumer Group '{group_id}': Committed Partition {partition} Offset {offset}")
