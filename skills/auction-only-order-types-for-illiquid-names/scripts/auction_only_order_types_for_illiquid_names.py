"""
auction-only-order-types-for-illiquid-names:
Execution algorithm that routes large orders in illiquid names to the Closing Auction
(LOC) to minimize continuous market impact.

An LOC (Limit-on-Close) order is a *limit* order designated for the closing auction
(see NYSE Rule 7.35(B) and Nasdaq Equity Rule 4) and therefore REQUIRES a limit
price. When a reference price and slippage tolerance are supplied, this engine
derives a suggested limit price so the returned routing plan is directly
submittable. When they are omitted the suggested limit price is None and the
caller is responsible for setting it before submission.
"""
import dataclasses
import logging
import math
from datetime import datetime, time
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class OrderType(Enum):
    CONTINUOUS_VWAP = "CONTINUOUS_VWAP"
    LIMIT_ON_CLOSE = "LIMIT_ON_CLOSE"
    MARKET_ON_CLOSE = "MARKET_ON_CLOSE"


# Closing-auction cutoffs (US Eastern Time).
#
# NYSE: MOC/LOC orders may be entered, modified, or canceled until 3:50 p.m. ET.
#       After 3:50 p.m. only contra-side offsetting orders are accepted and
#       existing MOC/LOC orders cannot be modified or canceled (NYSE Rule 7.35B).
# Nasdaq: MOC/LOC orders may be entered until 3:58 p.m. ET, but they may not be
#         canceled or modified after 3:50 p.m. ET (Nasdaq Equity Rule 4).
#
# 3:50 p.m. ET is therefore the conservative, exchange-portable cutoff used here
# for both entry and cancel/modify planning. Callers targeting Nasdaq only may
# relax the entry cutoff to NASDAQ_LOC_ENTRY_CUTOFF_ET.
CLOSING_AUCTION_CUTOFF_ET: time = time(15, 50)
NASDAQ_LOC_ENTRY_CUTOFF_ET: time = time(15, 58)


@dataclasses.dataclass
class IlliquidExecutionConfig:
    # Order size >= this fraction of ADV triggers 100% auction allocation.
    severe_illiquidity_threshold_pct: float = 0.05  # 5%
    # Order size >= this fraction of ADV triggers a hybrid (VWAP + Auction) split.
    moderate_illiquidity_threshold_pct: float = 0.01  # 1%
    # Fraction of the parent order routed to the auction in the hybrid tier.
    hybrid_auction_allocation_pct: float = 0.50  # 50% to auction, 50% to continuous
    # Default slippage tolerance (in basis points) used to derive a suggested LOC
    # limit price from a reference price when none is supplied explicitly.
    default_slippage_tolerance_bps: float = 50.0  # 50 bps = 0.50%


@dataclasses.dataclass
class ExecutionRoutingPlan:
    symbol: str
    total_qty: int
    continuous_qty: int
    auction_qty: int
    # Order type used for the auction portion. When auction_qty == 0 this field
    # reflects the continuous strategy (CONTINUOUS_VWAP) and no auction order is
    # placed.
    auction_order_type: OrderType
    reason: str
    # Suggested LOC limit price. Required by the exchange for LOC submission.
    # None when no reference price was supplied (caller must set it before submit),
    # or when the plan routes purely to continuous trading.
    suggested_limit_price: Optional[float] = None


class IlliquidAuctionExecutionEngine:
    """
    Determines the optimal routing between continuous trading and the Closing
    Auction based on the order's size relative to the asset's Average Daily
    Volume (ADV).
    """

    def __init__(self, config: IlliquidExecutionConfig = IlliquidExecutionConfig()):
        if config.hybrid_auction_allocation_pct < 0 or config.hybrid_auction_allocation_pct > 1:
            raise ValueError("hybrid_auction_allocation_pct must be within [0.0, 1.0].")
        if config.severe_illiquidity_threshold_pct <= 0:
            raise ValueError("severe_illiquidity_threshold_pct must be strictly positive.")
        if config.moderate_illiquidity_threshold_pct <= 0:
            raise ValueError("moderate_illiquidity_threshold_pct must be strictly positive.")
        if config.severe_illiquidity_threshold_pct <= config.moderate_illiquidity_threshold_pct:
            raise ValueError(
                "severe_illiquidity_threshold_pct must be greater than "
                "moderate_illiquidity_threshold_pct."
            )
        self.config = config

    def generate_routing_plan(
        self,
        symbol: str,
        total_qty: int,
        average_daily_volume: int,
        reference_price: Optional[float] = None,
        slippage_tolerance_bps: Optional[float] = None,
        side: str = "BUY",
    ) -> ExecutionRoutingPlan:
        """Route a parent order between continuous trading and the closing auction.

        Args:
            symbol: Instrument identifier; must be a non-empty string.
            total_qty: Parent order quantity in shares; must be a positive integer.
            average_daily_volume: 30-day ADV in shares; must be strictly positive.
            reference_price: Optional fair-value reference (e.g. current mid-price)
                used to derive a suggested LOC limit price. If None, no suggested
                limit price is produced and the caller must set one before
                submitting any LOC order.
            slippage_tolerance_bps: Optional tolerance in basis points applied to
                ``reference_price`` to derive the suggested limit price. Defaults
                to ``config.default_slippage_tolerance_bps`` when a reference
                price is supplied.
            side: "BUY" or "SELL". Buy limit is reference*(1+tol); sell limit is
                reference*(1-tol). Defaults to "BUY".

        Returns:
            An ExecutionRoutingPlan describing the continuous/auction split,
            auction order type, and (when computable) suggested LOC limit price.
        """
        if not symbol or not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("symbol must be a non-empty string.")
        if total_qty <= 0:
            raise ValueError("total_qty must be a positive integer.")
        if average_daily_volume <= 0:
            raise ValueError("Average Daily Volume (ADV) must be strictly positive.")
        side_upper = side.upper()
        if side_upper not in ("BUY", "SELL"):
            raise ValueError("side must be 'BUY' or 'SELL'.")

        adv_percentage = total_qty / average_daily_volume

        # Derive a suggested LOC limit price when a reference price is available.
        # An LOC order is a limit order (NYSE Rule 7.35(B)) and requires one.
        suggested_limit_price = self._compute_limit_price(
            reference_price=reference_price,
            slippage_tolerance_bps=slippage_tolerance_bps,
            side=side_upper,
        )

        if adv_percentage >= self.config.severe_illiquidity_threshold_pct:
            # Dangerously illiquid relative to our size.
            # Trading this in continuous will walk the book and cause massive slippage.
            # Route 100% to Limit-On-Close. MOC is too dangerous (no price protection).
            plan = ExecutionRoutingPlan(
                symbol=symbol,
                total_qty=total_qty,
                continuous_qty=0,
                auction_qty=total_qty,
                auction_order_type=OrderType.LIMIT_ON_CLOSE,
                suggested_limit_price=suggested_limit_price,
                reason=(
                    f"Size is {adv_percentage:.2%} of ADV (Severe). "
                    f"Routing 100% to LOC to minimize impact."
                ),
            )
        elif adv_percentage >= self.config.moderate_illiquidity_threshold_pct:
            # Moderate impact. Use a hybrid approach.
            auction_qty = int(total_qty * self.config.hybrid_auction_allocation_pct)
            continuous_qty = total_qty - auction_qty
            plan = ExecutionRoutingPlan(
                symbol=symbol,
                total_qty=total_qty,
                continuous_qty=continuous_qty,
                auction_qty=auction_qty,
                auction_order_type=OrderType.LIMIT_ON_CLOSE,
                suggested_limit_price=suggested_limit_price,
                reason=(
                    f"Size is {adv_percentage:.2%} of ADV (Moderate). "
                    f"Hybrid routing: {continuous_qty} Continuous, {auction_qty} LOC."
                ),
            )
        else:
            # Liquid relative to our size. Safe to trade purely via continuous VWAP/TWAP.
            # No auction order is placed; auction_order_type reflects the continuous
            # strategy actually used (CONTINUOUS_VWAP). suggested_limit_price is not
            # applicable to a continuous VWAP routing and is left as None.
            plan = ExecutionRoutingPlan(
                symbol=symbol,
                total_qty=total_qty,
                continuous_qty=total_qty,
                auction_qty=0,
                auction_order_type=OrderType.CONTINUOUS_VWAP,
                suggested_limit_price=None,
                reason=(
                    f"Size is {adv_percentage:.2%} of ADV (Liquid). "
                    f"Routing 100% to Continuous."
                ),
            )

        logger.info("[%s] %s", symbol, plan.reason)
        return plan

    def _compute_limit_price(
        self,
        reference_price: Optional[float],
        slippage_tolerance_bps: Optional[float],
        side: str,
    ) -> Optional[float]:
        """Derive a suggested LOC limit price from a reference price and tolerance.

        Buy: reference * (1 + tolerance)  -- willing to pay up to this.
        Sell: reference * (1 - tolerance) -- willing to receive at least this.

        Returns None when no reference price is supplied (caller must set the
        limit price before submitting the LOC order).
        """
        if reference_price is None:
            return None
        if not math.isfinite(reference_price) or reference_price <= 0:
            raise ValueError("reference_price must be a finite, strictly positive value when supplied.")
        tolerance_bps = (
            slippage_tolerance_bps
            if slippage_tolerance_bps is not None
            else self.config.default_slippage_tolerance_bps
        )
        if tolerance_bps < 0:
            raise ValueError("slippage_tolerance_bps must be non-negative.")
        tolerance = tolerance_bps / 10000.0
        if side == "BUY":
            limit_price = reference_price * (1.0 + tolerance)
        else:  # SELL
            limit_price = reference_price * (1.0 - tolerance)
        # Guard against non-finite results (e.g. overflow on extreme inputs).
        if not (limit_price > 0):
            raise ValueError("Computed limit price is non-positive; check inputs.")
        return round(limit_price, 4)


def is_past_closing_auction_cutoff(
    submission_time: datetime,
    cutoff: time = CLOSING_AUCTION_CUTOFF_ET,
) -> bool:
    """Return True if ``submission_time`` is at or past the auction cutoff.

    ``submission_time`` must be timezone-aware and is compared by wall-clock
    time-of-day, intended to be US/Eastern. Naive datetimes are rejected to
    prevent silent timezone misinterpretation (e.g. UTC vs ET).

    Args:
        submission_time: Timezone-aware datetime of the intended submission.
        cutoff: Cutoff time-of-day; defaults to 15:50 (NYSE/Nasdaq conservative
            MOC/LOC entry and cancel/modify cutoff).

    Raises:
        ValueError: if ``submission_time`` is naive (tzinfo is None).
    """
    if submission_time.tzinfo is None:
        raise ValueError(
            "submission_time must be timezone-aware (expected US/Eastern)."
        )
    # Compare wall-clock time-of-day only. The caller is responsible for
    # ensuring the datetime is expressed in US/Eastern time.
    return submission_time.time() >= cutoff


def validate_submission_window(
    submission_time: datetime,
    cutoff: time = CLOSING_AUCTION_CUTOFF_ET,
) -> None:
    """Raise ValueError if ``submission_time`` is past the auction cutoff.

    Convenience wrapper around :func:`is_past_closing_auction_cutoff` for
    pre-trade enforcement. After the cutoff, MOC/LOC orders cannot be reliably
    entered or modified on NYSE and cannot be canceled/modified on Nasdaq.
    """
    if is_past_closing_auction_cutoff(submission_time, cutoff):
        raise ValueError(
            f"Submission time {submission_time.isoformat()} is past the closing "
            f"auction cutoff {cutoff.isoformat()}; MOC/LOC entry is not permitted "
            f"after this deadline (NYSE Rule 7.35B / Nasdaq Equity Rule 4)."
        )
