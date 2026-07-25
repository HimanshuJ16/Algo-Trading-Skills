"""
order-book-imbalance-signal-pipeline: Dedicated fast-path L2 order book imbalance (OBI)
and volume-weighted micro-price calculation pipeline.
"""
from dataclasses import dataclass, field
import logging
import time
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ImbalanceSignalType(Enum):
    NEUTRAL = "NEUTRAL"
    HIGH_BUY_PRESSURE = "HIGH_BUY_PRESSURE"
    HIGH_SELL_PRESSURE = "HIGH_SELL_PRESSURE"


@dataclass
class L2OrderBookTop:
    symbol: str
    bid_price: float
    bid_volume: float
    ask_price: float
    ask_volume: float
    timestamp_ns: int


@dataclass
class OBISignalResult:
    symbol: str
    imbalance: float          # -1.0 to +1.0
    micro_price: float        # Volume weighted mid
    mid_price: float
    signal_type: ImbalanceSignalType
    calculation_latency_ns: int


class FastPathOBIPipelineEngine:
    """
    Dedicated low-latency pipeline for calculating Order Book Imbalance (OBI)
    and Micro-Price signals from Level-2 top-of-book data streams.
    """

    def __init__(
        self,
        imbalance_threshold: float = 0.60,
        signal_callback: Optional[Callable[[OBISignalResult], None]] = None,
    ):
        self.imbalance_threshold = imbalance_threshold
        self.signal_callback = signal_callback
        self.total_signals_emitted = 0

    def process_l2_update(self, book_top: L2OrderBookTop) -> OBISignalResult:
        """
        Fast-path L2 book calculation. Operates directly on floats without heap allocations.
        """
        t_start = time.perf_counter_ns()

        v_bid = book_top.bid_volume
        v_ask = book_top.ask_volume
        p_bid = book_top.bid_price
        p_ask = book_top.ask_price

        total_vol = v_bid + v_ask

        if total_vol <= 0.0:
            imbalance = 0.0
            micro_price = (p_bid + p_ask) / 2.0 if (p_bid > 0 and p_ask > 0) else 0.0
            mid_price = micro_price
        else:
            imbalance = (v_bid - v_ask) / total_vol
            micro_price = ((v_bid * p_ask) + (v_ask * p_bid)) / total_vol
            mid_price = (p_bid + p_ask) / 2.0

        # Classify signal type
        if imbalance >= self.imbalance_threshold:
            sig_type = ImbalanceSignalType.HIGH_BUY_PRESSURE
        elif imbalance <= -self.imbalance_threshold:
            sig_type = ImbalanceSignalType.HIGH_SELL_PRESSURE
        else:
            sig_type = ImbalanceSignalType.NEUTRAL

        t_end = time.perf_counter_ns()
        latency_ns = t_end - t_start

        result = OBISignalResult(
            symbol=book_top.symbol,
            imbalance=round(imbalance, 4),
            micro_price=round(micro_price, 4),
            mid_price=round(mid_price, 4),
            signal_type=sig_type,
            calculation_latency_ns=latency_ns,
        )

        if sig_type != ImbalanceSignalType.NEUTRAL:
            self.total_signals_emitted += 1
            logger.info(
                f"OBI Fast-Path Signal [{book_top.symbol}]: {sig_type.value} | "
                f"Imbalance={imbalance:+.2f}, MicroPrice=${micro_price:.2f} (Calc: {latency_ns} ns)."
            )
            if self.signal_callback:
                self.signal_callback(result)

        return result
