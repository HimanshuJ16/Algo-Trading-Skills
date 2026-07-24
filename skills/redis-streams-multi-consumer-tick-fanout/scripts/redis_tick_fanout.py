"""
redis-streams-multi-consumer-tick-fanout: Production-grade Redis Streams market data fanout manager,
consumer group handler, stream entry acknowledger (XACK), and pending entry claimer (XCLAIM).
"""
from dataclasses import dataclass, field
import json
import logging
import time
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass
class TickData:
    symbol: str
    last_price: float
    volume: float
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, str]:
        return {
            "symbol": self.symbol,
            "last_price": str(self.last_price),
            "volume": str(self.volume),
            "timestamp": str(self.timestamp),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TickData":
        return cls(
            symbol=str(data.get("symbol", "")),
            last_price=float(data.get("last_price", 0.0)),
            volume=float(data.get("volume", 0.0)),
            timestamp=float(data.get("timestamp", time.time())),
        )


class MockRedisStreamEngine:
    """In-memory Redis Streams engine simulator for standalone testing."""

    def __init__(self):
        self.streams: Dict[str, List[Tuple[str, Dict[str, str]]]] = {}
        self.consumer_groups: Dict[str, Set[str]] = {}
        self.pels: Dict[str, Dict[str, List[Tuple[str, str, float]]]] = {}  # stream -> group -> [(msg_id, consumer, ts)]
        self.msg_counter = 0

    def xadd(self, stream: str, fields: Dict[str, str], maxlen: int = 100000) -> str:
        if stream not in self.streams:
            self.streams[stream] = []
        self.msg_counter += 1
        msg_id = f"{int(time.time()*1000)}-{self.msg_counter}"
        self.streams[stream].append((msg_id, fields))
        if len(self.streams[stream]) > maxlen:
            self.streams[stream].pop(0)
        return msg_id

    def xgroup_create(self, stream: str, group: str, mkstream: bool = True):
        if stream not in self.streams and mkstream:
            self.streams[stream] = []
        if stream not in self.consumer_groups:
            self.consumer_groups[stream] = set()
        self.consumer_groups[stream].add(group)
        if stream not in self.pels:
            self.pels[stream] = {}
        if group not in self.pels[stream]:
            self.pels[stream][group] = []

    def xreadgroup(
        self, group: str, consumer: str, stream: str, count: int = 10
    ) -> List[Tuple[str, Dict[str, str]]]:
        if stream not in self.streams:
            return []

        # Find un-ACKed or new messages
        already_pended = {m[0] for m in self.pels.get(stream, {}).get(group, [])}
        results = []
        for msg_id, fields in self.streams[stream]:
            if msg_id not in already_pended:
                results.append((msg_id, fields))
                self.pels[stream][group].append((msg_id, consumer, time.time()))
                if len(results) >= count:
                    break
        return results

    def xack(self, stream: str, group: str, msg_id: str) -> int:
        if stream in self.pels and group in self.pels[stream]:
            original_len = len(self.pels[stream][group])
            self.pels[stream][group] = [m for m in self.pels[stream][group] if m[0] != msg_id]
            return original_len - len(self.pels[stream][group])
        return 0

    def xclaim(
        self, stream: str, group: str, new_consumer: str, min_idle_ms: float, msg_ids: List[str]
    ) -> List[Tuple[str, Dict[str, str]]]:
        claimed = []
        now = time.time()
        if stream in self.pels and group in self.pels[stream]:
            new_pel = []
            for item in self.pels[stream][group]:
                m_id, old_c, ts = item
                idle_ms = (now - ts) * 1000
                if m_id in msg_ids and idle_ms >= min_idle_ms:
                    new_pel.append((m_id, new_consumer, now))
                    # Find fields
                    for s_id, f in self.streams[stream]:
                        if s_id == m_id:
                            claimed.append((m_id, f))
                            break
                else:
                    new_pel.append(item)
            self.pels[stream][group] = new_pel
        return claimed


class RedisTickFanoutManager:
    """
    Manages publishing market data ticks to Redis Streams, creating consumer groups,
    reading streams, acknowledging delivery (XACK), and claiming stale entries (XCLAIM).
    """

    def __init__(self, redis_client: Any = None, stream_name: str = "market_ticks"):
        self.stream_name = stream_name
        self.redis = redis_client if redis_client else MockRedisStreamEngine()

    def publish_tick(self, tick: TickData, maxlen: int = 100000) -> str:
        """Publishes tick to Redis Stream via XADD."""
        fields = tick.to_dict()
        msg_id = self.redis.xadd(self.stream_name, fields, maxlen=maxlen)
        logger.debug(f"Published tick '{tick.symbol}' to stream '{self.stream_name}' with ID {msg_id}")
        return msg_id

    def create_consumer_group(self, group_name: str):
        """Creates consumer group on stream."""
        try:
            self.redis.xgroup_create(self.stream_name, group_name, mkstream=True)
            logger.info(f"Consumer group '{group_name}' created on stream '{self.stream_name}'.")
        except Exception as e:
            logger.debug(f"Consumer group '{group_name}' may already exist: {e}")

    def consume_ticks(
        self, group_name: str, consumer_name: str, count: int = 10
    ) -> List[Tuple[str, TickData]]:
        """Reads ticks for specific consumer group."""
        raw_msgs = self.redis.xreadgroup(group_name, consumer_name, self.stream_name, count=count)
        ticks: List[Tuple[str, TickData]] = []
        for msg_id, fields in raw_msgs:
            tick = TickData.from_dict(fields)
            ticks.append((msg_id, tick))
        return ticks

    def acknowledge_tick(self, group_name: str, msg_id: str) -> int:
        """Acknowledges tick delivery via XACK."""
        return self.redis.xack(self.stream_name, group_name, msg_id)

    def claim_stale_ticks(
        self, group_name: str, new_consumer_name: str, min_idle_ms: float, msg_ids: List[str]
    ) -> List[Tuple[str, TickData]]:
        """Claims un-ACKed stale ticks from crashed consumer workers via XCLAIM."""
        claimed_raw = self.redis.xclaim(self.stream_name, group_name, new_consumer_name, min_idle_ms, msg_ids)
        claimed_ticks: List[Tuple[str, TickData]] = []
        for msg_id, fields in claimed_raw:
            tick = TickData.from_dict(fields)
            claimed_ticks.append((msg_id, tick))
            logger.warning(f"Re-claimed stale tick {msg_id} for consumer '{new_consumer_name}'")
        return claimed_ticks
