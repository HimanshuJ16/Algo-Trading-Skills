"""
adaptive-sampling-under-extreme-tick-rates: Dynamic tick rate frequency monitor,
systematic 1:N sampling engine, and cumulative volume/VWAP aggregator.
"""
from dataclasses import dataclass
import logging
import math
import time
from enum import Enum
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class SamplingMode(Enum):
    PASSTHROUGH = "PASSTHROUGH"
    SYSTEMATIC_SAMPLING = "SYSTEMATIC_SAMPLING"


@dataclass
class SampledTick:
    symbol: str
    sequence_id: int
    timestamp: float
    price: float  # Represents VWAP if sampled
    volume: float # Total accumulated volume if sampled
    sampling_factor: int
    mode: SamplingMode


class AdaptiveTickSamplerEngine:
    """
    Monitors tick arrival rates and dynamically applies 1:N systematic sampling under
    extreme volume conditions while preserving volume and VWAP metrics.
    Uses O(1) sliding window counters for rate tracking to prevent memory bloat.
    """

    def __init__(
        self,
        target_max_rate_per_sec: int = 5000,
    ):
        self.target_max_rate_per_sec = target_max_rate_per_sec

        # O(1) rate tracking
        self.current_window_sec: Dict[str, int] = {}
        self.current_window_count: Dict[str, int] = {}
        self.previous_window_count: Dict[str, int] = {}

        # Sampling state
        self.tick_counters: Dict[str, int] = {}
        self.accumulated_vol: Dict[str, float] = {}
        self.accumulated_notional: Dict[str, float] = {}

    def _get_rolling_rate(self, sym: str, now: float) -> float:
        current_sec = int(now)
        
        if sym not in self.current_window_sec:
            self.current_window_sec[sym] = current_sec
            self.current_window_count[sym] = 0
            self.previous_window_count[sym] = 0
            
        if current_sec > self.current_window_sec[sym]:
            # Shift windows
            diff = current_sec - self.current_window_sec[sym]
            if diff == 1:
                self.previous_window_count[sym] = self.current_window_count[sym]
            else:
                self.previous_window_count[sym] = 0
            self.current_window_count[sym] = 0
            self.current_window_sec[sym] = current_sec
            
        self.current_window_count[sym] += 1
        
        # Sliding window estimate
        fraction_of_second = now - current_sec
        estimated_rate = self.previous_window_count[sym] * (1 - fraction_of_second) + self.current_window_count[sym]
        return estimated_rate

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
        Accumulates volume and notional to emit a VWAP price when sampling.
        """
        sym = symbol.upper()
        now = timestamp or time.time()

        # O(1) Rolling Rate Estimation
        current_rate = self._get_rolling_rate(sym, now)

        # Compute sampling factor k
        if current_rate > self.target_max_rate_per_sec:
            k = max(2, math.ceil(current_rate / float(self.target_max_rate_per_sec)))
            mode = SamplingMode.SYSTEMATIC_SAMPLING
        else:
            k = 1
            mode = SamplingMode.PASSTHROUGH

        if sym not in self.tick_counters:
            self.tick_counters[sym] = 0
            self.accumulated_vol[sym] = 0.0
            self.accumulated_notional[sym] = 0.0

        self.tick_counters[sym] += 1
        self.accumulated_vol[sym] += volume
        self.accumulated_notional[sym] += (price * volume)

        # Emit if counter hits k
        if self.tick_counters[sym] >= k or mode == SamplingMode.PASSTHROUGH:
            emitted_vol = self.accumulated_vol[sym]
            emitted_vwap = self.accumulated_notional[sym] / emitted_vol if emitted_vol > 0 else price
            
            self.tick_counters[sym] = 0
            self.accumulated_vol[sym] = 0.0
            self.accumulated_notional[sym] = 0.0

            if mode == SamplingMode.SYSTEMATIC_SAMPLING:
                logger.info(
                    f"Adaptive Sampler [{sym}]: Extreme tick rate ({current_rate:.0f}/s > {self.target_max_rate_per_sec}/s). "
                    f"Sampled 1:{k}. Emitted VWAP: {emitted_vwap:.2f}, Vol: {emitted_vol:.2f}"
                )

            return SampledTick(
                symbol=sym,
                sequence_id=sequence_id,
                timestamp=now,
                price=emitted_vwap,
                volume=emitted_vol,
                sampling_factor=k,
                mode=mode,
            )

        # Skipped tick
        return None

    def flush(self, symbol: str) -> Optional[SampledTick]:
        """Flushes remaining accumulated volume for symbol."""
        sym = symbol.upper()
        if sym in self.accumulated_vol and self.accumulated_vol[sym] > 0:
            emitted_vol = self.accumulated_vol[sym]
            emitted_vwap = self.accumulated_notional[sym] / emitted_vol
            
            self.accumulated_vol[sym] = 0.0
            self.accumulated_notional[sym] = 0.0
            self.tick_counters[sym] = 0
            
            return SampledTick(
                symbol=sym,
                sequence_id=-1,
                timestamp=time.time(),
                price=emitted_vwap,
                volume=emitted_vol,
                sampling_factor=1,
                mode=SamplingMode.PASSTHROUGH,
            )
        return None
