"""Client-side Post-Only limit repricing for fast markets.

Guarantees that a Post-Only (a.k.a. maker-only / ``LIMIT_MAKER`` / ``GTX`` /
``ParticipateDoNotInitiate``) limit order is *passive and tick-aligned before it
leaves the process*, so the venue is never asked to resolve a crossing Post-Only
order on the client's behalf.

Why the client must decide, rather than relying on the venue
-----------------------------------------------------------
"Post-Only" is not one behaviour. A crossing Post-Only order is handled at least
three different ways across venues:

* **Rejected** -- Binance Spot ``LIMIT_MAKER`` ("a ``LIMIT`` order that will be
  rejected if the order immediately matches and trades as a taker"), Coinbase
  ``post_only``.
* **Executed as a taker, at the resting order's price** -- Nasdaq / BX / PSX:
  "Post-Only orders that would cross the Exchange book will be executed at the
  price of the resting order."
* **Silently re-priced by the matching engine** -- Deribit ``post_only`` adjusts
  the price to just inside the spread unless ``reject_post_only`` is set.

Only the first case is a "harmless" rejection. On a Nasdaq-family venue a
crossing Post-Only order *trades*, paying taker fees and removing the liquidity
the strategy intended to provide. Client-side pre-checking is therefore the only
venue-portable guarantee, and this module implements that pre-check.

See ``references/standards.md`` for the sourced venue-behaviour table.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from decimal import Decimal, DecimalException, ROUND_CEILING, ROUND_FLOOR
from typing import Optional

logger = logging.getLogger(__name__)

BUY = "BUY"
SELL = "SELL"
_VALID_SIDES = (BUY, SELL)

# Report statuses.
STATUS_ACCEPTED_PASSIVE = "POST_ONLY_ACCEPTED_PASSIVE"
STATUS_PASSIVE_REPRICED = "POST_ONLY_PASSIVE_REPRICED"
STATUS_ATTEMPTS_EXCEEDED = "REPRICE_ATTEMPTS_EXCEEDED"
STATUS_BOOK_LOCKED_OR_CROSSED = "BOOK_LOCKED_OR_CROSSED"
STATUS_NO_PASSIVE_PRICE = "NO_VALID_PASSIVE_PRICE"

# Caller actions.
ACTION_SUBMIT = "SUBMIT"
ACTION_HOLD = "HOLD"


@dataclass(frozen=True)
class Config:
    """Repricer tuning. All values are library defaults, not venue mandates.

    Args:
        max_reprice_attempts: Consecutive reprices allowed for one order cycle
            before the engine withholds the order. Caps message churn; see
            ``references/standards.md`` for why an unbounded loop is unsafe.
        fast_market_velocity_threshold: Quote-update rate (ticks/sec) at or above
            which the book is treated as a fast market. Purely a local
            classification -- no venue publishes this number.
        fast_market_offset_ticks: Extra ticks *away from the touch* applied when
            repricing during a fast market. ``0`` (default) joins the near touch.
            A value of 1 or more trades fill probability for resistance to being
            re-crossed by the next quote update while the order is in flight.
    """

    max_reprice_attempts: int = 3
    fast_market_velocity_threshold: float = 20.0
    fast_market_offset_ticks: int = 0

    def __post_init__(self) -> None:
        if self.max_reprice_attempts < 0:
            raise ValueError("max_reprice_attempts must be >= 0")
        if (not math.isfinite(self.fast_market_velocity_threshold)
                or self.fast_market_velocity_threshold < 0):
            raise ValueError("fast_market_velocity_threshold must be finite and >= 0")
        if self.fast_market_offset_ticks < 0:
            raise ValueError("fast_market_offset_ticks must be >= 0")


def _require_finite(value: object, name: str) -> float:
    """Reject bools, non-numerics, NaN and Inf before they reach arithmetic."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    return numeric


@dataclass(frozen=True)
class MarketState:
    """A single-venue top-of-book snapshot.

    ``tick_size`` is the venue's minimum price increment *for this symbol* and
    must be supplied per symbol at runtime -- it is not a global constant. See
    ``references/standards.md``.
    """

    symbol: str
    best_bid: float
    best_ask: float
    tick_size: float = 0.01
    market_velocity_ticks_per_sec: float = 10.0

    def __post_init__(self) -> None:
        if not str(self.symbol).strip():
            raise ValueError("symbol must be a non-empty string")
        for name in ("best_bid", "best_ask", "tick_size", "market_velocity_ticks_per_sec"):
            _require_finite(getattr(self, name), name)
        if self.tick_size <= 0:
            raise ValueError(f"tick_size must be > 0, got {self.tick_size}")
        if self.best_bid <= 0 or self.best_ask <= 0:
            raise ValueError("best_bid and best_ask must both be > 0")
        if self.market_velocity_ticks_per_sec < 0:
            raise ValueError("market_velocity_ticks_per_sec must be >= 0")


@dataclass(frozen=True)
class OrderRequest:
    """A Post-Only limit order the caller intends to submit.

    ``reprice_attempts`` is the number of reprices already spent on this order
    cycle. The engine does **not** mutate it; carry the value forward using
    :meth:`FastMarketRepriceReport.next_attempt`, otherwise the churn cap never
    engages.
    """

    order_id: str
    side: str
    quantity: float
    desired_price: float
    reprice_attempts: int = 0

    def __post_init__(self) -> None:
        if not str(self.order_id).strip():
            raise ValueError("order_id must be a non-empty string")
        if str(self.side).upper() not in _VALID_SIDES:
            raise ValueError(f"side must be one of {_VALID_SIDES}, got {self.side!r}")
        for name in ("quantity", "desired_price"):
            _require_finite(getattr(self, name), name)
        if self.quantity <= 0:
            raise ValueError(f"quantity must be > 0, got {self.quantity}")
        if self.desired_price <= 0:
            raise ValueError(f"desired_price must be > 0, got {self.desired_price}")
        if self.reprice_attempts < 0:
            raise ValueError("reprice_attempts must be >= 0")


@dataclass(frozen=True)
class FastMarketRepriceReport:
    """Outcome of one repricing decision.

    Attributes:
        final_limit_price: Tick-aligned passive price to submit. Meaningful only
            when ``action == 'SUBMIT'``.
        action: ``'SUBMIT'`` or ``'HOLD'``. ``'HOLD'`` means no order should be
            sent on this pass.
        rejection_churn_prevented: True when this call stopped a message that
            would have crossed (repriced), or stopped a message entirely
            (attempts exhausted, locked book, no valid passive price).
        reprice_attempts_used: Attempt count to carry into the next submission.
    """

    order_id: str
    symbol: str
    side: str
    original_desired_price: float
    final_limit_price: float
    is_repriced: bool
    is_fast_market: bool
    rejection_churn_prevented: bool
    status: str
    audit_notes: str
    action: str = ACTION_SUBMIT
    reprice_attempts_used: int = 0
    tick_size: float = 0.01
    offset_ticks_applied: int = 0

    def next_attempt(self, order: OrderRequest) -> OrderRequest:
        """Return ``order`` re-stamped with this report's attempt count.

        Feed the result back into
        :meth:`FastMarketPostOnlyRepricer.process_order` on the next quote
        update. Without this the churn cap cannot engage.
        """
        return OrderRequest(
            order_id=order.order_id,
            side=order.side,
            quantity=order.quantity,
            desired_price=order.desired_price,
            reprice_attempts=self.reprice_attempts_used,
        )


def _dec(value: float) -> Decimal:
    """Convert a float to the decimal literal the caller meant.

    ``Decimal(str(x))`` rather than ``Decimal(x)``: the latter preserves binary
    representation error (``Decimal(0.1)`` is ``0.1000...5551``), which then
    survives tick alignment and produces an off-tick price.
    """
    return Decimal(str(value))


def align_to_tick(price: float, tick_size: float, side: str) -> float:
    """Align ``price`` to ``tick_size``, always rounding *away from the touch*.

    A BUY is floored and a SELL is ceiled. Nearest-rounding is unsafe here: it
    can move a BUY *up* onto or through the best ask (and a SELL down onto the
    best bid), manufacturing exactly the crossing Post-Only order this module
    exists to prevent. Venues validate the increment strictly -- Binance's
    ``PRICE_FILTER`` requires ``price % tickSize == 0``.

    Args:
        price: Unaligned price, must be finite and > 0.
        tick_size: Venue minimum price increment for the symbol, must be > 0.
        side: ``'BUY'`` (floor) or ``'SELL'`` (ceil).

    Returns:
        The aligned price as a float. Note this can be ``0.0`` when a BUY price
        below one tick is floored -- callers must treat a non-positive result as
        "no passive price available" rather than submitting it.
        :meth:`FastMarketPostOnlyRepricer.process_order` does this for you.

    Raises:
        ValueError: On a non-finite or non-positive price, a non-positive tick,
            an unrecognised side, or a price/tick ratio too extreme to quantize.
    """
    _require_finite(price, "price")
    _require_finite(tick_size, "tick_size")
    if price <= 0:
        raise ValueError(f"price must be > 0, got {price}")
    if tick_size <= 0:
        raise ValueError(f"tick_size must be > 0, got {tick_size}")
    side_upper = str(side).upper()
    if side_upper not in _VALID_SIDES:
        raise ValueError(f"side must be one of {_VALID_SIDES}, got {side!r}")

    tick = _dec(tick_size)
    rounding = ROUND_FLOOR if side_upper == BUY else ROUND_CEILING
    try:
        aligned = (_dec(price) / tick).to_integral_value(rounding=rounding) * tick
        return float(aligned.quantize(tick))
    except DecimalException as exc:
        # e.g. a corrupt feed price of 1e300 against a 0.01 tick needs more
        # significant digits than the decimal context carries. Surface it as a
        # rejected input rather than letting a decimal error escape into the
        # order path.
        raise ValueError(
            f"cannot align price {price} to tick {tick_size}: ratio out of range ({exc})"
        ) from exc


class FastMarketPostOnlyRepricer:
    """Keeps Post-Only limit orders passive and tick-aligned in fast markets."""

    def __init__(self, config: Optional[Config] = None) -> None:
        self.config = config or Config()

    def process_order(
        self, market: MarketState, order: OrderRequest
    ) -> FastMarketRepriceReport:
        """Decide the passive, tick-aligned price for one Post-Only order.

        Args:
            market: Current top-of-book snapshot for ``order``'s symbol.
            order: The Post-Only order the caller intends to submit.

        Returns:
            A :class:`FastMarketRepriceReport`. Submit only when
            ``report.action == 'SUBMIT'``.
        """
        is_fast_market = (
            market.market_velocity_ticks_per_sec >= self.config.fast_market_velocity_threshold
        )
        side = order.side.upper()
        tick = market.tick_size

        if order.reprice_attempts >= self.config.max_reprice_attempts:
            return self._withhold(
                market, order, side, is_fast_market, STATUS_ATTEMPTS_EXCEEDED,
                f"Reprice attempts exhausted "
                f"({order.reprice_attempts}/{self.config.max_reprice_attempts}). "
                f"Withholding order to prevent rejection churn; cancel or re-evaluate the quote.",
                order.reprice_attempts,
            )

        # A locked (bid == ask) or crossed (bid > ask) book has no passive price
        # on at least one side: repricing a BUY to best_bid would still sit at or
        # above best_ask. Fast markets and multi-venue feeds produce these
        # regularly, so treat it as an expected market state, not an error.
        if market.best_bid >= market.best_ask:
            return self._withhold(
                market, order, side, is_fast_market, STATUS_BOOK_LOCKED_OR_CROSSED,
                f"Book locked/crossed (bid {market.best_bid} >= ask {market.best_ask}); "
                f"no passive price exists. Withholding order until the book uncrosses.",
                order.reprice_attempts,
            )

        # Crossing test. A BUY takes liquidity at or above the best ask; a SELL
        # takes at or below the best bid. Resting strictly inside the spread is
        # passive and is left untouched.
        crosses = (
            (side == BUY and order.desired_price >= market.best_ask)
            or (side == SELL and order.desired_price <= market.best_bid)
        )

        offset_ticks = (
            self.config.fast_market_offset_ticks if (crosses and is_fast_market) else 0
        )
        if crosses:
            target = market.best_bid if side == BUY else market.best_ask
            target += -offset_ticks * tick if side == BUY else offset_ticks * tick
        else:
            target = order.desired_price

        if target <= 0:
            return self._withhold(
                market, order, side, is_fast_market, STATUS_NO_PASSIVE_PRICE,
                f"Passive target {target} is non-positive after applying "
                f"{offset_ticks} offset tick(s). Withholding order.",
                order.reprice_attempts,
            )

        final_price = align_to_tick(target, tick, side)

        # Post-alignment invariant. Flooring a BUY can only move it further from
        # the ask, but a coarse tick or an off-tick quote can still leave no
        # passive price available -- and flooring a sub-tick bid reaches zero --
        # so the guarantee is asserted, not assumed.
        if (final_price <= 0
                or (side == BUY and final_price >= market.best_ask)
                or (side == SELL and final_price <= market.best_bid)):
            return self._withhold(
                market, order, side, is_fast_market, STATUS_NO_PASSIVE_PRICE,
                f"No on-tick passive price available for {side} at tick {tick} "
                f"inside BBO {market.best_bid}/{market.best_ask}. Withholding order.",
                order.reprice_attempts,
            )

        is_repriced = crosses
        status = STATUS_PASSIVE_REPRICED if is_repriced else STATUS_ACCEPTED_PASSIVE
        attempts_used = order.reprice_attempts + (1 if is_repriced else 0)
        notes = (
            f"[{market.symbol} {status}] side={side} desired={order.desired_price} "
            f"final={final_price} bbo={market.best_bid}/{market.best_ask} tick={tick} "
            f"offset_ticks={offset_ticks} fast_market={is_fast_market} "
            f"velocity={market.market_velocity_ticks_per_sec} ticks/s attempts={attempts_used}"
        )
        logger.info(notes)

        return FastMarketRepriceReport(
            order_id=order.order_id,
            symbol=market.symbol,
            side=side,
            original_desired_price=order.desired_price,
            final_limit_price=final_price,
            is_repriced=is_repriced,
            is_fast_market=is_fast_market,
            rejection_churn_prevented=is_repriced,
            status=status,
            audit_notes=notes,
            action=ACTION_SUBMIT,
            reprice_attempts_used=attempts_used,
            tick_size=tick,
            offset_ticks_applied=offset_ticks,
        )

    def _withhold(
        self,
        market: MarketState,
        order: OrderRequest,
        side: str,
        is_fast_market: bool,
        status: str,
        notes: str,
        attempts_used: int,
    ) -> FastMarketRepriceReport:
        """Build a HOLD report -- no order should be sent on this pass."""
        logger.warning("[%s %s] order_id=%s %s", market.symbol, status, order.order_id, notes)
        return FastMarketRepriceReport(
            order_id=order.order_id,
            symbol=market.symbol,
            side=side,
            original_desired_price=order.desired_price,
            final_limit_price=order.desired_price,
            is_repriced=False,
            is_fast_market=is_fast_market,
            rejection_churn_prevented=True,
            status=status,
            audit_notes=notes,
            action=ACTION_HOLD,
            reprice_attempts_used=attempts_used,
            tick_size=market.tick_size,
            offset_ticks_applied=0,
        )
