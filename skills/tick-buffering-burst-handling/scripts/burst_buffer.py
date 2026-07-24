"""
tick-buffering-burst-handling: Production-grade bounded per-symbol tick buffer manager,
empirical peak-rate capacity sizing, keep-latest-N vs drop-newest strategies,
high-water mark occupancy tracking, and structured drop audit logging.
"""
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
import logging
import math
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class DropStrategy(str, Enum):
    KEEP_LATEST_N = "KEEP_LATEST_N"        # Drops oldest unconsumed tick when full (overwrite semantics)
    DROP_NEWEST_LOG = "DROP_NEWEST_LOG"    # Drops incoming tick when full and records structured audit log


@dataclass
class TickDropRecord:
    timestamp: float
    symbol: str
    drop_strategy: DropStrategy
    dropped_tick: Any
    buffer_capacity: int
    occupancy_pct: float


class BurstBufferManager:
    """
    Empirically sized bounded tick buffer manager maintaining per-symbol buffers,
    preventing memory leaks / OOM crashes during market volatility bursts.
    """

    def __init__(
        self,
        default_capacity: int = 500,
        strategy: DropStrategy = DropStrategy.KEEP_LATEST_N,
        custom_capacities: Optional[Dict[str, int]] = None,
    ):
        self.default_capacity = default_capacity
        self.strategy = strategy
        self.custom_capacities = custom_capacities or {}

        self.buffers: Dict[str, deque] = {}
        self.drop_logs: List[TickDropRecord] = []
        self.high_water_marks: Dict[str, float] = {}  # {symbol: max_occupancy_pct}

    @staticmethod
    def calculate_empirical_capacity(peak_ticks_per_sec: float, max_lag_sec: float = 2.0) -> int:
        """Calculates bounded buffer capacity based on peak observed tick rate."""
        return max(50, int(math.ceil(peak_ticks_per_sec * max_lag_sec)))

    def _get_buffer(self, symbol: str) -> deque:
        sym = symbol.upper()
        if sym not in self.buffers:
            cap = self.custom_capacities.get(sym, self.default_capacity)
            self.buffers[sym] = deque(maxlen=cap)
            self.high_water_marks[sym] = 0.0
        return self.buffers[sym]

    def push(self, symbol: str, tick: Any) -> bool:
        buf = self._get_buffer(symbol)
        sym = symbol.upper()
        cap = buf.maxlen or self.default_capacity

        is_full = len(buf) == cap

        if is_full:
            if self.strategy == DropStrategy.KEEP_LATEST_N:
                oldest = buf[0]
                self.drop_logs.append(
                    TickDropRecord(
                        timestamp=time.time(),
                        symbol=sym,
                        drop_strategy=self.strategy,
                        dropped_tick=oldest,
                        buffer_capacity=cap,
                        occupancy_pct=100.0,
                    )
                )
                buf.append(tick)
                self.high_water_marks[sym] = 100.0
                return True
            else:  # DROP_NEWEST_LOG
                self.drop_logs.append(
                    TickDropRecord(
                        timestamp=time.time(),
                        symbol=sym,
                        drop_strategy=self.strategy,
                        dropped_tick=tick,
                        buffer_capacity=cap,
                        occupancy_pct=100.0,
                    )
                )
                logger.warning(f"Burst Buffer FULL for '{sym}'! Dropped incoming tick.")
                self.high_water_marks[sym] = 100.0
                return False

        buf.append(tick)
        current_occ = (len(buf) / cap) * 100.0
        if current_occ > self.high_water_marks[sym]:
            self.high_water_marks[sym] = current_occ

        return True

    def get_latest(self, symbol: str) -> Optional[Any]:
        buf = self._get_buffer(symbol)
        return buf[-1] if buf else None

    def pop_oldest(self, symbol: str) -> Optional[Any]:
        buf = self._get_buffer(symbol)
        return buf.popleft() if buf else None

    def get_occupancy_report(self) -> Dict[str, Any]:
        report = {}
        for sym, buf in self.buffers.items():
            cap = buf.maxlen or self.default_capacity
            report[sym] = {
                "current_size": len(buf),
                "capacity": cap,
                "occupancy_pct": round((len(buf) / cap) * 100.0, 2),
                "high_water_mark_pct": round(self.high_water_marks.get(sym, 0.0), 2),
            }
        return report


# Backward compatibility class
class SymbolBuffer:

    def __init__(self, maxlen=500):
        self.buffer = deque(maxlen=maxlen)
        self.drop_log = []

    def push(self, tick):
        if len(self.buffer) == self.buffer.maxlen:
            self.drop_log.append({"ts": time.time(), "dropped_oldest": self.buffer[0]})
        self.buffer.append(tick)

    def latest(self):
        return self.buffer[-1] if self.buffer else None
