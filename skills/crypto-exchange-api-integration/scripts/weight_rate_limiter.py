"""
crypto-exchange-api-integration: exchange-accurate rate limiting, order payload
construction, and 24/7 rolling P&L reset tracking.

Rate-limit models are NOT interchangeable across crypto exchanges. This module
implements the two documented models it can implement correctly, and refuses to
guess at the rest:

* ``WeightRateLimiter`` -- Binance-style **weight per fixed clock window**. The
  Binance counter resets at the start of each interval (the 1-minute counter
  resets at :00), which is why this is a fixed-window and not a sliding-window
  limiter: a sliding window cannot be reconciled with the
  ``X-MBX-USED-WEIGHT-1M`` header the exchange returns.
* ``KrakenDecayCounterLimiter`` -- Kraken's **per-API-key decaying counter**
  (a counter that rises per call and decays continuously), which is a
  structurally different model that a weight-per-window limiter cannot express.

Coinbase Advanced Trade uses a third model again (requests per second, throttled
by IP, reported via ``CB-RATE-LIMIT-*`` headers). Published figures for it
differ between Coinbase sources, so this module deliberately ships **no**
Coinbase preset rather than a fabricated one -- read the current limit from
Coinbase's documentation and register an explicit limiter.

Published limits go stale. Binance Spot's REQUEST_WEIGHT limit was 1,200/min
until 2023-08-25, when it became 6,000/min. Prefer
``CryptoExchangeRateLimiter.binance_from_exchange_info()`` to build limiters
from the live ``exchangeInfo`` ``rateLimits`` array over trusting the constants
below.
"""
import asyncio
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Deque, Dict, Iterable, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

# Binance REQUEST_WEIGHT limits, per IP, per 1-minute clock window.
# Spot: raised 1,200 -> 6,000 effective 2023-08-25 00:00 UTC.
# Treat both as defaults to be confirmed against exchangeInfo at startup.
BINANCE_SPOT_REQUEST_WEIGHT_PER_MINUTE = 6000
BINANCE_USDM_FUTURES_REQUEST_WEIGHT_PER_MINUTE = 2400

TimeSource = Callable[[], float]


class RateLimitError(RuntimeError):
    """Base class for rate-limiter faults."""


class UnknownNamespaceError(RateLimitError):
    """Raised when a limiter is requested for a namespace that was never
    registered. Namespaces are never auto-created: inventing a budget for a
    mistyped namespace lets a bot run at a limit no exchange published, which
    is how IP bans happen."""


class UnsatisfiableWeightError(RateLimitError, ValueError):
    """Raised when a single request costs more than the whole window budget.
    Waiting cannot help, so the caller is told immediately instead of spinning
    forever."""


class RateLimitTimeout(RateLimitError, TimeoutError):
    """Raised when ``acquire()`` could not obtain headroom within its timeout."""


class OrderValidationError(ValueError):
    """Raised when an order payload is not valid for the target market."""


class MarketType(str, Enum):
    """Binance markets have genuinely different order grammars; post-only and
    time-in-force are not portable between them."""

    SPOT = "SPOT"
    USDM_FUTURES = "USDM_FUTURES"


class SelfTradePreventionMode(str, Enum):
    """Binance STP modes.

    The set of modes a given symbol accepts is per-symbol: query
    ``allowedSelfTradePreventionModes`` from ``GET /api/v3/exchangeInfo``
    before sending one. ``NONE`` is what the exchange applies when the
    parameter is omitted -- it is not a safe default to pick silently.
    """

    EXPIRE_MAKER = "EXPIRE_MAKER"  # Expire the resting (maker) order's remainder
    EXPIRE_TAKER = "EXPIRE_TAKER"  # Expire the incoming (taker) order's remainder
    EXPIRE_BOTH = "EXPIRE_BOTH"    # Expire both orders' remainders
    DECREMENT = "DECREMENT"        # Decrement both orders by the prevented quantity
    NONE = "NONE"                  # No STP restriction (exchange default when omitted)


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"
    LIMIT_MAKER = "LIMIT_MAKER"  # Spot post-only. NOT supported on USD-M futures.


class TimeInForce(str, Enum):
    GTC = "GTC"  # Good 'Til Canceled
    IOC = "IOC"  # Immediate or Cancel
    FOK = "FOK"  # Fill or Kill
    GTX = "GTX"  # Post-only. USD-M futures only.
    GTD = "GTD"  # Good 'Til Date. USD-M futures only.


# Spot POST /api/v3/order documents GTC, IOC and FOK only.
_SPOT_TIME_IN_FORCE = frozenset({TimeInForce.GTC, TimeInForce.IOC, TimeInForce.FOK})


@dataclass
class CryptoOrderPayload:
    """Builds a Binance order payload that is actually valid for the target market.

    ``market_type`` and ``stp_mode`` are both required. STP is required because
    the whole point of the surrounding skill is that accepting an unexamined
    self-trade-prevention default is a bug; a dataclass default would reintroduce
    exactly that. Pass ``SelfTradePreventionMode.NONE`` explicitly to opt out.

    Post-only is expressed differently per market and is handled here:

    * SPOT           -> ``type=LIMIT_MAKER`` with no ``timeInForce``
    * USDM_FUTURES   -> ``type=LIMIT`` with ``timeInForce=GTX``
      (USD-M futures does not support the ``LIMIT_MAKER`` type at all)
    """

    symbol: str
    side: str  # BUY or SELL
    order_type: OrderType
    quantity: float
    market_type: MarketType
    stp_mode: SelfTradePreventionMode
    price: Optional[float] = None
    time_in_force: Optional[TimeInForce] = None
    post_only: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise OrderValidationError("symbol must be a non-empty string")
        if self.side not in ("BUY", "SELL"):
            raise OrderValidationError(f"side must be 'BUY' or 'SELL', got {self.side!r}")
        if not isinstance(self.quantity, (int, float)) or isinstance(self.quantity, bool):
            raise OrderValidationError(f"quantity must be a number, got {self.quantity!r}")
        if not math.isfinite(self.quantity) or self.quantity <= 0:
            raise OrderValidationError(
                f"quantity must be a positive finite number, got {self.quantity!r}"
            )
        if self.price is not None:
            if not isinstance(self.price, (int, float)) or isinstance(self.price, bool):
                raise OrderValidationError(f"price must be a number, got {self.price!r}")
            if not math.isfinite(self.price) or self.price <= 0:
                raise OrderValidationError(
                    f"price must be a positive finite number, got {self.price!r}"
                )
        if not isinstance(self.market_type, MarketType):
            raise OrderValidationError("market_type must be a MarketType")
        if not isinstance(self.stp_mode, SelfTradePreventionMode):
            raise OrderValidationError(
                "stp_mode must be an explicit SelfTradePreventionMode "
                "(pass NONE to opt out deliberately)"
            )

    def _resolve_type_and_tif(self) -> Tuple[OrderType, Optional[TimeInForce]]:
        """Resolve the (type, timeInForce) pair the target market actually accepts."""
        want_post_only = self.post_only or self.order_type == OrderType.LIMIT_MAKER

        if self.order_type == OrderType.MARKET:
            if want_post_only:
                raise OrderValidationError("a MARKET order cannot be post-only")
            if self.time_in_force is not None:
                # Binance rejects with "Parameter 'timeInForce' sent when not required".
                raise OrderValidationError(
                    "timeInForce must not be sent with a MARKET order"
                )
            return OrderType.MARKET, None

        if self.price is None:
            raise OrderValidationError(f"{self.order_type.value} order requires a price")

        if want_post_only:
            if self.market_type == MarketType.SPOT:
                if self.time_in_force is not None:
                    # LIMIT_MAKER's mandatory params are quantity and price only.
                    raise OrderValidationError(
                        "timeInForce must not be sent with a spot LIMIT_MAKER order"
                    )
                return OrderType.LIMIT_MAKER, None
            # USD-M futures has no LIMIT_MAKER type; post-only is LIMIT + GTX.
            if self.time_in_force not in (None, TimeInForce.GTX):
                raise OrderValidationError(
                    "post-only on USD-M futures requires timeInForce=GTX, got "
                    f"{self.time_in_force}"
                )
            return OrderType.LIMIT, TimeInForce.GTX

        if self.order_type == OrderType.LIMIT_MAKER:
            # Unreachable via want_post_only, kept explicit for clarity.
            raise OrderValidationError("LIMIT_MAKER implies post-only")

        tif = self.time_in_force or TimeInForce.GTC
        if self.market_type == MarketType.SPOT and tif not in _SPOT_TIME_IN_FORCE:
            raise OrderValidationError(
                f"timeInForce={tif.value} is not supported on Binance spot "
                f"(supported: {', '.join(sorted(t.value for t in _SPOT_TIME_IN_FORCE))})"
            )
        return OrderType.LIMIT, tif

    def to_dict(self) -> Dict[str, Any]:
        """Render the Binance REST order payload.

        Note there is no ``execInst`` parameter on Binance -- that is BitMEX
        syntax. Sending it does not make an order post-only.
        """
        order_type, tif = self._resolve_type_and_tif()

        payload: Dict[str, Any] = {
            "symbol": self.symbol,
            "side": self.side,
            "type": order_type.value,
            "quantity": str(self.quantity),
            "selfTradePreventionMode": self.stp_mode.value,
        }
        if self.price is not None and order_type != OrderType.MARKET:
            payload["price"] = str(self.price)
        if tif is not None:
            payload["timeInForce"] = tif.value
        return payload


class WeightRateLimiter:
    """Binance-style weight limiter over a **fixed clock window**.

    Binance's ``X-MBX-USED-WEIGHT-1M`` counter resets at the start of each
    minute rather than sliding, so the local accounting is bucketed the same
    way: ``bucket = floor(now / window_seconds)``. This is what makes
    ``update_from_header`` meaningful -- the server's number and the local
    number describe the same window and the local one can simply adopt it.

    ``safety_margin_pct`` reserves headroom below the published limit. It is
    this module's own conservatism dial, not an exchange rule; the exchange
    will happily let you use 100%.

    Not thread-safe. Under asyncio a single event loop is safe because
    ``try_consume`` never awaits; across OS threads, wrap calls in your own
    lock.
    """

    def __init__(
        self,
        max_weight_per_window: int = BINANCE_SPOT_REQUEST_WEIGHT_PER_MINUTE,
        window_seconds: float = 60.0,
        safety_margin_pct: float = 0.0,
        time_source: TimeSource = time.time,
        name: str = "weight",
    ) -> None:
        if not isinstance(max_weight_per_window, int) or isinstance(max_weight_per_window, bool):
            raise ValueError(
                f"max_weight_per_window must be an int, got {max_weight_per_window!r}"
            )
        if max_weight_per_window <= 0:
            raise ValueError(
                f"max_weight_per_window must be positive, got {max_weight_per_window}"
            )
        if not isinstance(window_seconds, (int, float)) or not math.isfinite(window_seconds) or window_seconds <= 0:
            raise ValueError(f"window_seconds must be positive and finite, got {window_seconds!r}")
        if not (0.0 <= safety_margin_pct < 1.0):
            raise ValueError(
                f"safety_margin_pct must be in [0, 1), got {safety_margin_pct!r}"
            )

        self.max_weight = max_weight_per_window
        self.window = float(window_seconds)
        self.safety_margin_pct = float(safety_margin_pct)
        self.name = name
        self._time = time_source
        self._bucket: Optional[int] = None
        self._used = 0

    @property
    def effective_max(self) -> int:
        """Usable budget after the safety margin. Always at least 1."""
        return max(1, int(self.max_weight * (1.0 - self.safety_margin_pct)))

    def _current_bucket(self) -> int:
        return int(self._time() // self.window)

    def _roll(self) -> None:
        bucket = self._current_bucket()
        if bucket != self._bucket:
            self._bucket = bucket
            self._used = 0

    def current_weight(self) -> int:
        self._roll()
        return self._used

    def seconds_until_reset(self) -> float:
        now = self._time()
        return max(0.0, ((now // self.window) + 1) * self.window - now)

    def try_consume(self, weight: int) -> bool:
        """Consume ``weight`` if the current window has room. Never blocks."""
        if not isinstance(weight, int) or isinstance(weight, bool) or weight <= 0:
            raise ValueError(f"weight must be a positive int, got {weight!r}")
        if weight > self.effective_max:
            raise UnsatisfiableWeightError(
                f"request weight {weight} exceeds the usable {self.name} budget "
                f"{self.effective_max} (published limit {self.max_weight} per "
                f"{self.window:g}s); waiting cannot satisfy it"
            )
        self._roll()
        if self._used + weight > self.effective_max:
            return False
        self._used += weight
        return True

    async def acquire(self, weight: int, timeout: Optional[float] = None) -> None:
        """Wait until ``weight`` fits in the budget, then consume it.

        Sleeps until the window boundary rather than polling, so a full window
        costs one sleep. Raises ``UnsatisfiableWeightError`` immediately if the
        request can never fit, and ``RateLimitTimeout`` if ``timeout`` elapses.
        """
        if timeout is not None and (not math.isfinite(timeout) or timeout < 0):
            raise ValueError(f"timeout must be a non-negative finite number, got {timeout!r}")
        deadline = None if timeout is None else self._time() + timeout

        while not self.try_consume(weight):
            wait = self.seconds_until_reset()
            if deadline is not None:
                remaining = deadline - self._time()
                if remaining <= 0:
                    raise RateLimitTimeout(
                        f"timed out waiting {timeout:g}s for {weight} {self.name} units"
                    )
                wait = min(wait, remaining)
            logger.debug(
                "%s limiter saturated (%d/%d); sleeping %.3fs to window reset",
                self.name, self._used, self.effective_max, wait,
            )
            await asyncio.sleep(max(wait, 0.0))

    def update_from_header(self, server_used_weight: int) -> None:
        """Adopt the exchange's own counter (e.g. ``X-MBX-USED-WEIGHT-1M``).

        The server is authoritative, so this sets the local counter in **both**
        directions. Only ever ratcheting upward -- never following the server's
        reset at the window boundary -- pins the limiter at its ceiling and
        stops the bot from trading.

        Call this immediately after reading the response, so the header still
        describes the window the limiter is currently in. Other processes
        sharing the IP will show up here as weight this limiter never consumed,
        which is exactly the point.
        """
        if not isinstance(server_used_weight, int) or isinstance(server_used_weight, bool):
            raise ValueError(
                f"server_used_weight must be an int, got {server_used_weight!r}"
            )
        if server_used_weight < 0:
            raise ValueError(
                f"server_used_weight must be non-negative, got {server_used_weight}"
            )
        self._roll()
        if server_used_weight != self._used:
            logger.debug(
                "%s limiter resynced from header: local=%d server=%d",
                self.name, self._used, server_used_weight,
            )
        self._used = server_used_weight


class KrakenTier(str, Enum):
    """Kraken Spot REST verification tiers and their documented counter limits."""

    STARTER = "STARTER"
    INTERMEDIATE = "INTERMEDIATE"
    PRO = "PRO"


# (max counter, decay per second) per Kraken's Spot REST rate limit guide.
KRAKEN_TIER_LIMITS: Mapping[KrakenTier, Tuple[int, float]] = {
    KrakenTier.STARTER: (15, 0.33),
    KrakenTier.INTERMEDIATE: (20, 0.5),
    KrakenTier.PRO: (20, 1.0),
}

# Kraken call costs: most calls +1; ledger/trade history +2.
KRAKEN_DEFAULT_CALL_COST = 1
KRAKEN_LEDGER_TRADE_HISTORY_COST = 2


class KrakenDecayCounterLimiter:
    """Kraken Spot REST **decaying counter**, which is not a weight window.

    The counter rises by the call's cost and decays continuously at a
    tier-dependent rate; exceeding the tier maximum returns
    ``EAPI:Rate limit exceeded``. The counter is **per API key**, not per IP,
    and master/subaccounts share the master's tier.

    ``AddOrder``/``CancelOrder`` are governed by a separate Kraken limiter that
    this class does not model -- do not route order placement through it and
    assume you are covered.
    """

    def __init__(
        self,
        tier: KrakenTier = KrakenTier.STARTER,
        time_source: TimeSource = time.monotonic,
    ) -> None:
        if not isinstance(tier, KrakenTier):
            raise ValueError(f"tier must be a KrakenTier, got {tier!r}")
        self.tier = tier
        self.max_counter, self.decay_per_sec = KRAKEN_TIER_LIMITS[tier]
        self._time = time_source
        self._counter = 0.0
        self._last = time_source()

    def _decay(self) -> None:
        now = self._time()
        elapsed = max(0.0, now - self._last)
        self._last = now
        self._counter = max(0.0, self._counter - elapsed * self.decay_per_sec)

    def current_counter(self) -> float:
        self._decay()
        return self._counter

    def try_consume(self, cost: int = KRAKEN_DEFAULT_CALL_COST) -> bool:
        if not isinstance(cost, int) or isinstance(cost, bool) or cost <= 0:
            raise ValueError(f"cost must be a positive int, got {cost!r}")
        if cost > self.max_counter:
            raise UnsatisfiableWeightError(
                f"call cost {cost} exceeds the {self.tier.value} tier maximum "
                f"counter {self.max_counter}"
            )
        self._decay()
        if self._counter + cost > self.max_counter:
            return False
        self._counter += cost
        return True

    def seconds_until_available(self, cost: int = KRAKEN_DEFAULT_CALL_COST) -> float:
        """How long until ``cost`` would fit, given continuous decay."""
        self._decay()
        deficit = (self._counter + cost) - self.max_counter
        if deficit <= 0:
            return 0.0
        return deficit / self.decay_per_sec


class CryptoExchangeRateLimiter:
    """Registry of per-namespace limiters.

    Spot, futures and margin are separate API surfaces with separate budgets,
    so they get separate limiters. Namespaces must be registered explicitly:
    an unregistered namespace raises rather than receiving an invented budget,
    because a typo that silently grants a made-up limit is indistinguishable
    from correct code right up until the IP ban.

    Only Binance presets ship here, and only because their published figures
    are unambiguous. Confirm them against ``exchangeInfo`` at startup -- see
    ``binance_from_exchange_info``.
    """

    def __init__(
        self,
        safety_margin_pct: float = 0.0,
        time_source: TimeSource = time.time,
        include_binance_defaults: bool = True,
    ) -> None:
        self._time = time_source
        self.limiters: Dict[str, Any] = {}
        if include_binance_defaults:
            self.register(
                "binance_spot",
                WeightRateLimiter(
                    BINANCE_SPOT_REQUEST_WEIGHT_PER_MINUTE,
                    60.0,
                    safety_margin_pct=safety_margin_pct,
                    time_source=time_source,
                    name="binance_spot",
                ),
            )
            self.register(
                "binance_futures",
                WeightRateLimiter(
                    BINANCE_USDM_FUTURES_REQUEST_WEIGHT_PER_MINUTE,
                    60.0,
                    safety_margin_pct=safety_margin_pct,
                    time_source=time_source,
                    name="binance_futures",
                ),
            )

    def register(self, namespace: str, limiter: Any) -> None:
        if not isinstance(namespace, str) or not namespace.strip():
            raise ValueError("namespace must be a non-empty string")
        self.limiters[namespace] = limiter

    def get_limiter(self, namespace: str) -> Any:
        try:
            return self.limiters[namespace]
        except KeyError:
            raise UnknownNamespaceError(
                f"no limiter registered for namespace {namespace!r}; "
                f"registered: {sorted(self.limiters)}. Register one explicitly with "
                "limits taken from the exchange's current documentation."
            ) from None

    @staticmethod
    def binance_from_exchange_info(
        rate_limits: Iterable[Mapping[str, Any]],
        safety_margin_pct: float = 0.0,
        time_source: TimeSource = time.time,
        name: str = "binance",
    ) -> "WeightRateLimiter":
        """Build a limiter from Binance ``exchangeInfo``'s ``rateLimits`` array.

        Hard-coded limits go stale -- Binance spot moved from 1,200 to 6,000
        weight per minute in 2023 -- so prefer reading the live value. Expects
        entries shaped like
        ``{"rateLimitType": "REQUEST_WEIGHT", "interval": "MINUTE",
        "intervalNum": 1, "limit": 6000}``.
        """
        seconds_per_interval = {"SECOND": 1.0, "MINUTE": 60.0, "HOUR": 3600.0, "DAY": 86400.0}
        for entry in rate_limits:
            if entry.get("rateLimitType") != "REQUEST_WEIGHT":
                continue
            interval = entry.get("interval")
            if interval not in seconds_per_interval:
                continue
            window = seconds_per_interval[interval] * int(entry.get("intervalNum", 1))
            return WeightRateLimiter(
                int(entry["limit"]),
                window,
                safety_margin_pct=safety_margin_pct,
                time_source=time_source,
                name=name,
            )
        raise ValueError("no REQUEST_WEIGHT rate limit found in exchangeInfo rateLimits")


@dataclass
class Rolling24hPnLTracker:
    """Rolling P&L window for markets with no session close.

    Crypto markets never close, so there is no exchange-provided boundary at
    which a "daily" risk counter resets. This keeps a sliding window instead.

    Timestamps are **wall-clock** (``time.time`` by default) because they must
    line up with exchange fill timestamps. Wall clock can jump (NTP correction,
    DST is irrelevant in UTC but the clock still steps); if a monotonic window
    matters more to you than agreement with exchange timestamps, pass
    ``time_source=time.monotonic`` and supply monotonic trade timestamps.

    Records are expected to arrive in roughly non-decreasing timestamp order,
    which is what a live fill stream produces. Entries already older than the
    window are rejected rather than stored.
    """

    window_hours: float = 24.0
    time_source: TimeSource = time.time
    _trades: Deque[Tuple[float, float]] = field(default_factory=deque, init=False, repr=False)
    _total: float = field(default=0.0, init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.window_hours, (int, float)) or not math.isfinite(self.window_hours) or self.window_hours <= 0:
            raise ValueError(f"window_hours must be positive and finite, got {self.window_hours!r}")
        self.window_sec = float(self.window_hours) * 3600.0

    def record_pnl(self, pnl: float, timestamp: Optional[float] = None) -> None:
        if not isinstance(pnl, (int, float)) or isinstance(pnl, bool) or not math.isfinite(pnl):
            raise ValueError(f"pnl must be a finite number, got {pnl!r}")
        # `timestamp or now` would treat a legitimate 0.0 (epoch) as "missing".
        ts = self.time_source() if timestamp is None else float(timestamp)
        if not math.isfinite(ts):
            raise ValueError(f"timestamp must be finite, got {timestamp!r}")

        cutoff = self.time_source() - self.window_sec
        if ts < cutoff:
            logger.debug("Ignoring P&L record older than the %.1fh window", self.window_hours)
            self._prune()
            return

        self._trades.append((ts, pnl))
        self._total += pnl
        self._prune()

    def _prune(self) -> None:
        cutoff = self.time_source() - self.window_sec
        while self._trades and self._trades[0][0] < cutoff:
            _, pnl = self._trades.popleft()
            self._total -= pnl
        if not self._trades:
            self._total = 0.0

    def get_rolling_pnl(self) -> float:
        self._prune()
        return self._total

    def __len__(self) -> int:
        self._prune()
        return len(self._trades)
