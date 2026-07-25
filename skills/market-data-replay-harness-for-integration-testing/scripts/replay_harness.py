"""
market-data-replay-harness-for-integration-testing: Deterministic time-warp tick replay engine
with speed multipliers and event-driven strategy listener callbacks.
"""
from dataclasses import dataclass, field
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ReplayTick:
    symbol: str
    timestamp: float
    sequence_id: int
    bid: float
    ask: float
    volume: float

    @property
    def price(self) -> float:
        return (self.bid + self.ask) / 2.0


@dataclass
class ReplaySessionSummary:
    total_ticks_replayed: int
    simulated_duration_sec: float
    actual_wall_time_sec: float
    speed_multiplier: float
    emitted_orders_count: int


class MarketDataReplayHarness:
    """
    Replays recorded historical market data sessions through trading strategy callbacks
    at deterministic speeds (1x real-time, 10x fast-forward, or ASAP mode).
    """

    def __init__(self, speed_multiplier: float = 1.0):
        self.speed_multiplier = speed_multiplier
        self.replayed_ticks: List[ReplayTick] = []
        self.generated_orders: List[Dict[str, Any]] = []

    def replay_session(
        self,
        tick_log: List[ReplayTick],
        strategy_callback: Callable[[ReplayTick], Optional[Dict[str, Any]]],
        asap_mode: bool = False,
    ) -> ReplaySessionSummary:
        """
        Replays tick_log through strategy_callback.
        """
        if not tick_log:
            return ReplaySessionSummary(0, 0.0, 0.0, self.speed_multiplier, 0)

        # Ensure ticks are sorted by timestamp
        sorted_ticks = sorted(tick_log, key=lambda t: t.timestamp)
        total_ticks = len(sorted_ticks)

        t_start_wall = time.perf_counter()
        t_start_sim = sorted_ticks[0].timestamp
        t_end_sim = sorted_ticks[-1].timestamp
        sim_duration = max(0.0, t_end_sim - t_start_sim)

        logger.info(
            f"Starting Market Data Replay: {total_ticks} ticks over {sim_duration:.2f}s simulated time "
            f"(Speed: {self.speed_multiplier}x, ASAP={asap_mode})."
        )

        for i in range(total_ticks):
            curr_tick = sorted_ticks[i]

            # Intra-tick timing delay calculation if not ASAP mode
            if not asap_mode and i > 0:
                prev_tick = sorted_ticks[i - 1]
                sim_delta = max(0.0, curr_tick.timestamp - prev_tick.timestamp)
                wall_delay = sim_delta / float(self.speed_multiplier)
                if wall_delay > 0.0001:
                    time.sleep(wall_delay)

            # Invoke strategy callback
            self.replayed_ticks.append(curr_tick)
            order = strategy_callback(curr_tick)
            if order:
                self.generated_orders.append(order)

        t_end_wall = time.perf_counter()
        actual_wall = max(0.0001, t_end_wall - t_start_wall)

        logger.info(
            f"Completed Market Data Replay: {total_ticks} ticks in {actual_wall:.3f}s wall time. "
            f"Generated {len(self.generated_orders)} orders."
        )

        return ReplaySessionSummary(
            total_ticks_replayed=total_ticks,
            simulated_duration_sec=sim_duration,
            actual_wall_time_sec=round(actual_wall, 4),
            speed_multiplier=self.speed_multiplier,
            emitted_orders_count=len(self.generated_orders),
        )
