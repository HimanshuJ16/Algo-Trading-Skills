"""
adaptive-sampling-under-extreme-tick-rates: Dynamic tick rate frequency monitor,
systematic 1:N sampling engine, and cumulative volume/VWAP aggregator.
"""
from dataclasses import dataclass, field
import logging
import math
import time
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class SamplingMode(Enum):
    PASSTHROUGH = "PASSTHROUGH"
    SYSTEMATIC_SAMPLING = "SYSTEMATIC_SAMPLING"


@dataclass
class SampledTick:
    symbol: str
    sequence_id: int
    timestamp: float
    price: float
    volume: float
    accumulated_volume: float
    sampling_factor: int
    mode: SamplingMode


class AdaptiveTickSamplerEngine:
    """
    Monitors tick arrival rates and dynamically applies 1:N systematic sampling under
    extreme volume conditions while preserving volume and VWAP metrics.
    """

    def __init__(
        self,
        target_max_rate_per_sec: int = 5000,
        window_sec: float = 1.0,
    ):
        self.target_max_rate_per_sec = target_max_rate_per_sec
        self.window_sec = window_sec

        self.tick_counts: Dict[str, List[float]] = {}  # symbol -> list of timestamps
        self.tick_counters: Dict[str, int] = {}       # symbol -> counter modulo k
        self.accumulated_vol: Dict[str, float] = {}   # symbol -> skipped volume sum

    def ingest_tick(
        self,
        symbol: str,
        sequence_id: int,
        price: float,
        volume: float,
        timestamp: Optional[float] = None,
    ) -> Optional[SampledTick]:
        """
        Ingests a tick and determines if it should be emitted under current sampling rate.
        """
        sym = symbol.upper()
        now = timestamp or time.time()

        # Update frequency tracking window
        if sym not in self.tick_counts:
            self.tick_counts[sym] = []
            self.tick_counters[sym] = 0
            self.accumulated_vol[sym] = 0.0

        ts_list = self.tick_counts[sym]
        ts_list.append(now)

        # Evict timestamps older than window_sec
        cutoff = now - self.window_sec
        self.tick_counts[sym] = [t for t in ts_list if t >= cutoff]
        current_rate = len(self.tick_counts[sym])

        # Compute sampling factor k
        if current_rate > self.target_max_rate_per_sec:
            k = max(2, math.ceil(current_rate / float(self.target_max_rate_per_sec)))
            mode = SamplingMode.SYSTEMATIC_SAMPLING
        else:
            k = 1
            mode = SamplingMode.PASSTHROUGH

        self.tick_counters[sym] += 1
        self.accumulated_vol[sym] += volume

        # Emit if counter hits k
        if self.tick_counters[sym] >= k or mode == SamplingMode.PASSTHROUGH:
            emitted_vol = self.accumulated_vol[sym]
            self.tick_counters[sym] = 0
            self.accumulated_vol[sym] = 0.0

            if mode == SamplingMode.SYSTEMATIC_SAMPLING:
                logger.info(
                    f"Adaptive Sampler [{sym}]: Extreme tick rate ({current_rate}/s > {self.target_max_rate_per_sec}/s). "
                    f"Sampled 1:{k}. Emitted volume: {emitted_vol:.2f}"
                )

            return SampledTick(
                symbol=sym,
                sequence_id=sequence_id,
                timestamp=now,
                price=price,
                volume=volume,
                accumulated_volume=emitted_vol,
                sampling_factor=k,
                mode=mode,
            )

        # Skipped tick
        return None

    def flush(self, symbol: str) -> Optional[SampledTick]:
        """Flushes remaining accumulated volume for symbol during quiet periods or shutdown."""
        sym = symbol.upper()
        if sym in self.accumulated_vol and self.accumulated_vol[sym] > 0:
            emitted_vol = self.accumulated_vol[sym]
            self.accumulated_vol[sym] = 0.0
            self.tick_counters[sym] = 0
            return SampledTick(
                symbol=sym,
                sequence_id=-1,
                timestamp=time.time(),
                price=0.0,
                volume=0.0,
                accumulated_volume=emitted_vol,
                sampling_factor=1,
                mode=SamplingMode.PASSTHROUGH,
            )
        return None
