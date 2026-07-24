"""
crypto-exchange-api-integration: Production-grade weight-based rate limiter,
response header synchronization, multi-namespace API rate limit management,
self-trade prevention (STP) configuration, and 24/7 rolling P&L reset tracking.
"""
import asyncio
from collections import deque
from dataclasses import dataclass
from enum import Enum
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class SelfTradePreventionMode(str, Enum):
    EXPIRE_MAKER = "EXPIRE_MAKER"  # Cancel resting order
    EXPIRE_TAKER = "EXPIRE_TAKER"  # Cancel incoming order
    EXPIRE_BOTH = "EXPIRE_BOTH"    # Cancel both orders
    NONE = "NONE"                  # No STP restriction


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"
    LIMIT_MAKER = "LIMIT_MAKER"  # Post-Only


class TimeInForce(str, Enum):
    GTC = "GTC"  # Good 'Til Canceled
    IOC = "IOC"  # Immediate or Cancel
    FOK = "FOK"  # Fill or Kill
    GTD = "GTD"  # Good 'Til Date


@dataclass
class CryptoOrderPayload:
    symbol: str
    side: str  # BUY or SELL
    order_type: OrderType
    quantity: float
    price: Optional[float] = None
    time_in_force: TimeInForce = TimeInForce.GTC
    stp_mode: SelfTradePreventionMode = SelfTradePreventionMode.EXPIRE_MAKER
    post_only: bool = False

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "symbol": self.symbol,
            "side": self.side,
            "type": self.order_type.value,
            "quantity": str(self.quantity),
            "selfTradePreventionMode": self.stp_mode.value,
        }
        if self.price is not None:
            payload["price"] = str(self.price)
        if self.order_type != OrderType.MARKET:
            payload["timeInForce"] = self.time_in_force.value
        if self.post_only or self.order_type == OrderType.LIMIT_MAKER:
            payload["execInst"] = "PostOnly"
        return payload


class WeightRateLimiter:
    """
    Sliding window weight-based rate limiter supporting sync/async consumption
    and server response header synchronization (e.g. Binance x-mbx-used-weight-1m).
    """

    def __init__(self, max_weight_per_window: int = 1200, window_seconds: float = 60.0):
        self.max_weight = max_weight_per_window
        self.window = window_seconds
        self.usage: deque = deque()  # deque of (timestamp, weight)
        self._running_weight = 0

    def _prune(self):
        cutoff = time.monotonic() - self.window
        while self.usage and self.usage[0][0] < cutoff:
            _, w = self.usage.popleft()
            self._running_weight = max(0, self._running_weight - w)

    def current_weight(self) -> int:
        self._prune()
        return self._running_weight

    def try_consume(self, weight: int) -> bool:
        self._prune()
        if self._running_weight + weight > self.max_weight:
            return False
        self.usage.append((time.monotonic(), weight))
        self._running_weight += weight
        return True

    async def acquire(self, weight: int, retry_interval: float = 0.1):
        """Asynchronously waits until sufficient weight headroom is available."""
        while not self.try_consume(weight):
            await asyncio.sleep(retry_interval)

    def update_from_header(self, server_used_weight: int):
        """Synchronizes internal counter with server-reported used weight header."""
        self._prune()
        if server_used_weight > self._running_weight:
            diff = server_used_weight - self._running_weight
            self.usage.append((time.monotonic(), diff))
            self._running_weight = server_used_weight


class CryptoExchangeRateLimiter:
    """
    Multi-namespace rate limit manager separating Spot, Futures, and Margin API budgets.
    """

    def __init__(self):
        self.limiters: Dict[str, WeightRateLimiter] = {
            "binance_spot": WeightRateLimiter(max_weight_per_window=1200, window_seconds=60),
            "binance_futures": WeightRateLimiter(max_weight_per_window=2400, window_seconds=60),
            "coinbase_trade": WeightRateLimiter(max_weight_per_window=500, window_seconds=60),
            "kraken_rest": WeightRateLimiter(max_weight_per_window=1000, window_seconds=60),
        }

    def get_limiter(self, namespace: str) -> WeightRateLimiter:
        if namespace not in self.limiters:
            self.limiters[namespace] = WeightRateLimiter(max_weight_per_window=1000, window_seconds=60)
        return self.limiters[namespace]


class Rolling24hPnLTracker:
    """
    Tracks rolling 24-hour P&L reset boundaries for 24/7 crypto markets.
    """

    def __init__(self, window_hours: float = 24.0):
        self.window_sec = window_hours * 3600.0
        self.trades: deque = deque()  # (timestamp, pnl)

    def record_pnl(self, pnl: float, timestamp: Optional[float] = None):
        ts = timestamp or time.time()
        self.trades.append((ts, pnl))
        self._prune()

    def _prune(self):
        cutoff = time.time() - self.window_sec
        self.trades = deque([(ts, pnl) for ts, pnl in self.trades if ts >= cutoff])

    def get_rolling_pnl(self) -> float:
        self._prune()
        return sum(pnl for _, pnl in self.trades)
