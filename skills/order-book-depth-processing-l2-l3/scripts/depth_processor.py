"""
order-book-depth-processing-l2-l3: Production-grade L2/L3 order book processor,
thread-safe atomic state mutator, crossed-book validator, and microstructure metrics engine.
"""
from dataclasses import dataclass
import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class DepthProcessorError(ValueError):
    """Raised when crossed book state or depth corruption occurs."""
    pass


@dataclass
class DepthMetrics:
    best_bid: float
    best_ask: float
    mid_price: float
    weighted_mid_price: float
    imbalance_ratio: float  # Range [-1.0, +1.0]
    spread: float
    is_crossed: bool


class L2L3DepthProcessor:
    """
    Thread-safe processor for Level 2 (price aggregated) and Level 3 (order-by-order)
    depth feeds, preventing crossed-book state corruptions and computing depth metrics.
    """

    def __init__(self, symbol: str):
        self.symbol = symbol
        self._lock = threading.Lock()
        self.bids: Dict[float, float] = {}  # price -> volume
        self.asks: Dict[float, float] = {}  # price -> volume
        self.l3_orders: Dict[str, Tuple[str, float, float]] = {}  # order_id -> (side, price, size)

    def update_l2_depth(self, bid_updates: List[Tuple[float, float]], ask_updates: List[Tuple[float, float]]) -> bool:
        """Atomically applies Level 2 price level updates."""
        with self._lock:
            for price, qty in bid_updates:
                if qty <= 0:
                    self.bids.pop(price, None)
                else:
                    self.bids[price] = qty

            for price, qty in ask_updates:
                if qty <= 0:
                    self.asks.pop(price, None)
                else:
                    self.asks[price] = qty

            # Check crossed book
            if self._is_crossed_internal():
                logger.warning(f"CROSSED BOOK DETECTED for '{self.symbol}'! Best Bid >= Best Ask.")
                return False
            return True

    def add_l3_order(self, order_id: str, side: str, price: float, size: float):
        """Atomically adds order in Level 3 order book."""
        with self._lock:
            clean_side = side.upper()
            self.l3_orders[order_id] = (clean_side, price, size)
            target_book = self.bids if clean_side == "BUY" else self.asks
            target_book[price] = target_book.get(price, 0.0) + size

    def cancel_l3_order(self, order_id: str):
        """Atomically cancels order in Level 3 order book."""
        with self._lock:
            order_info = self.l3_orders.pop(order_id, None)
            if not order_info:
                return
            side, price, size = order_info
            target_book = self.bids if side == "BUY" else self.asks
            if price in target_book:
                target_book[price] -= size
                if target_book[price] <= 1e-9:
                    target_book.pop(price, None)

    def _is_crossed_internal(self) -> bool:
        if not self.bids or not self.asks:
            return False
        best_bid = max(self.bids.keys())
        best_ask = min(self.asks.keys())
        return best_bid >= best_ask

    def compute_metrics(self, depth_levels: int = 5) -> DepthMetrics:
        """Computes volume-weighted midprice, order book imbalance, and spread."""
        with self._lock:
            if not self.bids or not self.asks:
                raise DepthProcessorError(f"Cannot compute metrics for '{self.symbol}': Empty order book.")

            sorted_bids = sorted(self.bids.items(), key=lambda x: x[0], reverse=True)[:depth_levels]
            sorted_asks = sorted(self.asks.items(), key=lambda x: x[0])[:depth_levels]

            best_bid, top_bid_vol = sorted_bids[0]
            best_ask, top_ask_vol = sorted_asks[0]

            if best_bid >= best_ask:
                return DepthMetrics(
                    best_bid=best_bid,
                    best_ask=best_ask,
                    mid_price=(best_bid + best_ask) / 2.0,
                    weighted_mid_price=(best_bid + best_ask) / 2.0,
                    imbalance_ratio=0.0,
                    spread=best_ask - best_bid,
                    is_crossed=True,
                )

            total_bid_vol = sum(v for _, v in sorted_bids)
            total_ask_vol = sum(v for _, v in sorted_asks)
            denom_vol = max(total_bid_vol + total_ask_vol, 1e-5)

            weighted_mid = (best_bid * top_ask_vol + best_ask * top_bid_vol) / max(top_bid_vol + top_ask_vol, 1e-5)
            imbalance = (total_bid_vol - total_ask_vol) / denom_vol

            return DepthMetrics(
                best_bid=best_bid,
                best_ask=best_ask,
                mid_price=(best_bid + best_ask) / 2.0,
                weighted_mid_price=weighted_mid,
                imbalance_ratio=imbalance,
                spread=best_ask - best_bid,
                is_crossed=False,
            )
