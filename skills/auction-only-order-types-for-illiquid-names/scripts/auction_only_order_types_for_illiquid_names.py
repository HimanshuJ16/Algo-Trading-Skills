"""
auction-only-order-types-for-illiquid-names:
Execution algorithm that routes large orders in illiquid names to the Closing Auction
(LOC) to minimize continuous market impact.

An LOC (Limit-on-Close) order is a *limit* order designated for the closing auction
(NYSE Rule 7.31(c)(2)(A); Nasdaq Equity 4 Rule 4702(b)(12)(A)) and therefore REQUIRES
a limit price. When a reference price and slippage tolerance are supplied, this engine
derives a suggested limit price, rounded to a permissible minimum price increment
(17 CFR 242.612) and always *away* from the aggressive side, so the caller's slippage
tolerance is a hard bound rather than an approximation. When they are omitted the
suggested limit price is None and the caller is responsible for setting it before
submission.

Closing-auction deadlines are enforced relative to the *scheduled session close*, not
as fixed wall-clock constants: NYSE defines its MOC/LOC deadline as the Closing
Auction Imbalance Freeze Time, i.e. ten minutes before the scheduled end of Core
Trading Hours (NYSE Rule 7.35(a)(8)), which moves on early-close (half) days. All
timestamps are converted to America/New_York before comparison.
"""
import dataclasses
import logging
import math
from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

US_EASTERN = ZoneInfo("America/New_York")


class OrderType(Enum):
    CONTINUOUS_VWAP = "CONTINUOUS_VWAP"
    LIMIT_ON_CLOSE = "LIMIT_ON_CLOSE"
    MARKET_ON_CLOSE = "MARKET_ON_CLOSE"


class AuctionVenue(Enum):
    """Primary listing venue whose closing-auction rules govern acceptance."""

    NYSE = "NYSE"
    NASDAQ = "NASDAQ"


# --- Session geometry -------------------------------------------------------
#
# Regular-session close for US equities. Early-close (half) days close at
# 1:00 p.m. ET; pass that close through ``market_close_et`` rather than relying
# on the default.
REGULAR_SESSION_CLOSE_ET: time = time(16, 0)
EARLY_CLOSE_SESSION_CLOSE_ET: time = time(13, 0)

# --- Closing-auction cutoffs, expressed relative to the scheduled close -----
#
# NYSE (Rule 7.35B, DMM-Facilitated Closing Auctions): all MOC/LOC orders must be
# entered by the Closing Auction Imbalance Freeze Time, defined as ten minutes
# before the scheduled end of Core Trading Hours (Rule 7.35(a)(8)) -- 3:50 p.m.
# on a 4:00 p.m. close. After that time the Exchange accepts only MOC/LOC orders
# opposite a *published* Significant Closing Imbalance, and rejects them all if
# no Significant Closing Imbalance was published. MOC/LOC orders may not be
# cancelled or reduced after that time, even to correct a legitimate error
# (sole exception: Rule 7.35B(j)(2)(B)).
#
# Nasdaq (Equity 4 Rules 4702(b)(11), 4702(b)(12)): MOC entry is rejected at or
# after 3:55 p.m. ET; LOC entry is rejected at or after 3:58 p.m. ET, and an LOC
# entered between 3:55 and 3:58 is accepted only if a First or Second Reference
# Price exists, and is rejected or re-priced to it when more aggressive. Free
# cancel/modify of both ends at 3:50 p.m. ET; between 3:50 and 3:58 only
# legitimate-error corrections are permitted, and none at or after 3:58 p.m. ET.
#
# The Nasdaq rule text states absolute clock times against a 4:00 p.m. close.
# Encoding them as offsets is deliberately conservative: on an early close the
# offset-derived deadline is always earlier than the absolute one, so a caller
# who has not confirmed the venue's half-day schedule cannot be pushed *past* a
# real deadline by this module.
NYSE_ENTRY_CUTOFF_BEFORE_CLOSE: timedelta = timedelta(minutes=10)       # 15:50 on a 16:00 close
NASDAQ_MOC_ENTRY_CUTOFF_BEFORE_CLOSE: timedelta = timedelta(minutes=5)  # 15:55
NASDAQ_LOC_ENTRY_CUTOFF_BEFORE_CLOSE: timedelta = timedelta(minutes=2)  # 15:58
CANCEL_MODIFY_FREEZE_BEFORE_CLOSE: timedelta = timedelta(minutes=10)    # 15:50, both venues

_ENTRY_CUTOFF_BEFORE_CLOSE = {
    (AuctionVenue.NYSE, OrderType.MARKET_ON_CLOSE): NYSE_ENTRY_CUTOFF_BEFORE_CLOSE,
    (AuctionVenue.NYSE, OrderType.LIMIT_ON_CLOSE): NYSE_ENTRY_CUTOFF_BEFORE_CLOSE,
    (AuctionVenue.NASDAQ, OrderType.MARKET_ON_CLOSE): NASDAQ_MOC_ENTRY_CUTOFF_BEFORE_CLOSE,
    (AuctionVenue.NASDAQ, OrderType.LIMIT_ON_CLOSE): NASDAQ_LOC_ENTRY_CUTOFF_BEFORE_CLOSE,
}

# Conservative, venue- and order-type-portable cutoff on a regular 4:00 p.m. ET
# close: the earliest deadline any US primary listing venue imposes on MOC/LOC
# entry, and the point at which both venues freeze free cancel/modify.
CLOSING_AUCTION_CUTOFF_ET: time = time(15, 50)
# Nasdaq-specific absolute entry cutoffs on a regular 4:00 p.m. ET close.
NASDAQ_MOC_ENTRY_CUTOFF_ET: time = time(15, 55)
NASDAQ_LOC_ENTRY_CUTOFF_ET: time = time(15, 58)

# Minimum price variation for NMS stocks under 17 CFR 242.612 (Reg NMS Rule 612):
# $0.01 for quotations/orders priced at or above $1.00, $0.0001 below $1.00.
# The 2024 Rule 612 amendments add a $0.005 increment for certain NMS stocks
# priced at or above $1.00, assigned per security from the Time Weighted Average
# Quoted Spread; the SEC exemptive order of October 31, 2025 moved that
# compliance date to the first business day of November 2026. Because the
# applicable increment is per-security and time-varying, it is a caller input.
DEFAULT_TICK_SIZE: float = 0.01
SUB_DOLLAR_TICK_SIZE: float = 0.0001


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
    # Suggested LOC limit price, rounded to ``tick_size`` away from the aggressive
    # side. Required by the exchange for LOC submission. None when no reference
    # price was supplied (caller must set it before submit), or when the plan
    # routes purely to continuous trading.
    suggested_limit_price: Optional[float] = None


def _is_finite_real(value: object) -> bool:
    """True for a real, finite int/float. Rejects bool, NaN and infinities."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


class IlliquidAuctionExecutionEngine:
    """
    Determines the optimal routing between continuous trading and the Closing
    Auction based on the order's size relative to the asset's Average Daily
    Volume (ADV).
    """

    def __init__(self, config: Optional[IlliquidExecutionConfig] = None):
        # A fresh config per engine: a shared mutable dataclass instance as a
        # default argument would be constructed once at import and leak
        # mutations between every default-constructed engine.
        config = config if config is not None else IlliquidExecutionConfig()
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
        average_daily_volume: float,
        reference_price: Optional[float] = None,
        slippage_tolerance_bps: Optional[float] = None,
        side: str = "BUY",
        tick_size: float = DEFAULT_TICK_SIZE,
    ) -> ExecutionRoutingPlan:
        """Route a parent order between continuous trading and the closing auction.

        Args:
            symbol: Instrument identifier; must be a non-empty string.
            total_qty: Parent order quantity in shares; must be a positive int.
                Floats are rejected: a fractional parent order cannot be split
                into whole-share child orders.
            average_daily_volume: 30-day ADV in shares; must be a finite,
                strictly positive real. Fractional values are accepted because
                an average of daily volumes generally is fractional.
            reference_price: Optional fair-value reference (e.g. current
                mid-price) used to derive a suggested LOC limit price. If None,
                no suggested limit price is produced and the caller must set one
                before submitting any LOC order.
            slippage_tolerance_bps: Optional tolerance in basis points applied to
                ``reference_price`` to derive the suggested limit price. Defaults
                to ``config.default_slippage_tolerance_bps`` when a reference
                price is supplied.
            side: "BUY" or "SELL". Buy limit is reference*(1+tol); sell limit is
                reference*(1-tol). Defaults to "BUY".
            tick_size: Minimum price variation for the instrument. Defaults to
                ``DEFAULT_TICK_SIZE`` ($0.01, the Rule 612 increment for NMS
                stocks priced at or above $1.00); pass ``SUB_DOLLAR_TICK_SIZE``
                for sub-dollar names, or the venue-assigned increment where one
                applies. The suggested limit price is rounded to a whole multiple
                of this value, away from the aggressive side, so it is
                submittable and never breaches ``slippage_tolerance_bps``.

        Returns:
            An ExecutionRoutingPlan describing the continuous/auction split,
            auction order type, and (when computable) suggested LOC limit price.

        Raises:
            ValueError: on any invalid input described above.
        """
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("symbol must be a non-empty string.")
        if isinstance(total_qty, bool) or not isinstance(total_qty, int):
            raise ValueError("total_qty must be an integer number of shares.")
        if total_qty <= 0:
            raise ValueError("total_qty must be a positive integer.")
        if not _is_finite_real(average_daily_volume) or average_daily_volume <= 0:
            raise ValueError(
                "Average Daily Volume (ADV) must be a finite, strictly positive number."
            )
        if not isinstance(side, str):
            raise ValueError("side must be 'BUY' or 'SELL'.")
        side_upper = side.upper()
        if side_upper not in ("BUY", "SELL"):
            raise ValueError("side must be 'BUY' or 'SELL'.")

        adv_percentage = total_qty / average_daily_volume

        # Derive a suggested LOC limit price when a reference price is available.
        # An LOC order is a limit order (NYSE Rule 7.31(c)(2)(A)) and requires one.
        suggested_limit_price = self._compute_limit_price(
            reference_price=reference_price,
            slippage_tolerance_bps=slippage_tolerance_bps,
            side=side_upper,
            tick_size=tick_size,
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
            # A hybrid allocation of 0% is a legal configuration; when it rounds
            # the auction leg away there is no auction order, so the plan must
            # not advertise an LOC order type or carry an LOC limit price.
            places_auction_order = auction_qty > 0
            plan = ExecutionRoutingPlan(
                symbol=symbol,
                total_qty=total_qty,
                continuous_qty=continuous_qty,
                auction_qty=auction_qty,
                auction_order_type=(
                    OrderType.LIMIT_ON_CLOSE if places_auction_order
                    else OrderType.CONTINUOUS_VWAP
                ),
                suggested_limit_price=(
                    suggested_limit_price if places_auction_order else None
                ),
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
        tick_size: float = DEFAULT_TICK_SIZE,
    ) -> Optional[float]:
        """Derive a suggested LOC limit price from a reference price and tolerance.

        Buy: reference * (1 + tolerance), rounded DOWN to ``tick_size`` -- the
             most the caller is willing to pay, never rounded further up.
        Sell: reference * (1 - tolerance), rounded UP to ``tick_size`` -- the
             least the caller is willing to receive, never rounded further down.

        Rounding away from the aggressive side keeps ``slippage_tolerance_bps`` a
        hard bound. Rounding to a whole multiple of ``tick_size`` keeps the price
        submittable: a sub-penny limit on an NMS stock priced at or above $1.00
        is not a permissible minimum increment under 17 CFR 242.612 and the venue
        will reject it. Arithmetic is done in Decimal so that, for example,
        20.00 * 1.005 does not land on 20.099999999999998 and floor to 20.09.

        Returns None when no reference price is supplied (caller must set the
        limit price before submitting the LOC order).
        """
        if reference_price is None:
            return None
        if not _is_finite_real(reference_price) or reference_price <= 0:
            raise ValueError(
                "reference_price must be a finite, strictly positive value when supplied."
            )
        if not _is_finite_real(tick_size) or tick_size <= 0:
            raise ValueError("tick_size must be a finite, strictly positive value.")
        tolerance_bps = (
            slippage_tolerance_bps
            if slippage_tolerance_bps is not None
            else self.config.default_slippage_tolerance_bps
        )
        if not _is_finite_real(tolerance_bps) or tolerance_bps < 0:
            raise ValueError("slippage_tolerance_bps must be a finite, non-negative value.")

        ref = Decimal(str(reference_price))
        tolerance = Decimal(str(tolerance_bps)) / Decimal(10000)
        tick = Decimal(str(tick_size))
        if side == "BUY":
            raw = ref * (Decimal(1) + tolerance)
            rounding = ROUND_DOWN  # never pay more than the tolerance allows
        else:  # SELL
            raw = ref * (Decimal(1) - tolerance)
            rounding = ROUND_UP    # never accept less than the tolerance allows
        ticks = (raw / tick).to_integral_value(rounding=rounding)
        limit_price = ticks * tick
        if limit_price <= 0:
            # E.g. a sell tolerance of 10,000 bps, or a buy reference price below
            # one tick. There is no submittable price respecting the tolerance.
            raise ValueError(
                "Computed limit price is non-positive after tick rounding; check "
                "reference_price, slippage_tolerance_bps and tick_size."
            )
        result = float(limit_price)
        if not math.isfinite(result):
            # Decimal has no float range limit, so an absurd reference price can
            # round-trip to inf. Never hand a non-finite price to an order gateway.
            raise ValueError(
                "Computed limit price overflows a float; check reference_price."
            )
        return result


def to_eastern(moment: datetime, field_name: str = "submission_time") -> datetime:
    """Convert a timezone-aware datetime to America/New_York.

    Naive datetimes are rejected: comparing a naive UTC timestamp against an ET
    deadline is how on-close orders get sent after the cutoff.

    Raises:
        ValueError: if ``moment`` is naive or its tzinfo yields no UTC offset.
    """
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ValueError(
            f"{field_name} must be timezone-aware so it can be converted to "
            "US/Eastern; naive datetimes are rejected."
        )
    return moment.astimezone(US_EASTERN)


def _subtract(clock: time, delta: timedelta) -> time:
    """Subtract ``delta`` from a naive time-of-day, anchored on a fixed date.

    Raises:
        ValueError: if the subtraction would wrap past midnight. A cutoff that
            wraps is silently *permissive* -- a ``market_close_et`` of 00:00
            would yield a 23:50 cutoff that every intraday timestamp clears --
            so it is rejected rather than returned. No US equity session close
            is anywhere near this bound.
    """
    if not isinstance(clock, time):
        raise ValueError("market_close_et must be a datetime.time.")
    anchor = datetime(2000, 1, 1, clock.hour, clock.minute, clock.second, clock.microsecond)
    shifted = anchor - delta
    if shifted.date() != anchor.date():
        raise ValueError(
            f"Cutoff {delta} before a {clock.isoformat()} close wraps past "
            f"midnight; market_close_et is not a plausible session close."
        )
    return shifted.time()


def entry_cutoff_for(
    venue: AuctionVenue,
    order_type: OrderType,
    market_close_et: time = REGULAR_SESSION_CLOSE_ET,
) -> time:
    """Return the on-close *entry* cutoff for a venue and order type.

    Deadlines are derived from the scheduled session close because NYSE defines
    its MOC/LOC deadline as the Closing Auction Imbalance Freeze Time -- ten
    minutes before the scheduled end of Core Trading Hours (NYSE Rule 7.35(a)(8))
    -- which moves on early-close days. Pass ``EARLY_CLOSE_SESSION_CLOSE_ET`` on
    a half day.

    Clearing the cutoff is necessary but not sufficient in the final window:
    from the freeze time to the close NYSE accepts only MOC/LOC orders contra to
    a *published* Significant Closing Imbalance (NYSE Rule 7.35B), and a Nasdaq
    LOC entered in its last three minutes is accepted only against a First or
    Second Reference Price and may be re-priced to it
    (Nasdaq Equity 4 Rule 4702(b)(12)(A)).

    Raises:
        ValueError: if ``venue``/``order_type`` is not an on-close combination.
    """
    try:
        offset = _ENTRY_CUTOFF_BEFORE_CLOSE[(venue, order_type)]
    except KeyError:
        raise ValueError(
            f"No on-close entry cutoff is defined for {venue} / {order_type}; "
            "order_type must be LIMIT_ON_CLOSE or MARKET_ON_CLOSE."
        ) from None
    return _subtract(market_close_et, offset)


def cancel_modify_freeze_for(
    market_close_et: time = REGULAR_SESSION_CLOSE_ET,
) -> time:
    """Return the time after which MOC/LOC orders can no longer be freely pulled.

    Ten minutes before the scheduled close (3:50 p.m. on a 4:00 p.m. close) on
    both NYSE (Rule 7.35B) and Nasdaq (Equity 4 Rules 4702(b)(11), 4702(b)(12)).
    Nasdaq permits legitimate-error corrections until three minutes before the
    close; NYSE does not (sole exception: Rule 7.35B(j)(2)(B)). Treat auction
    quantity resting at this time as capital committed to an unknown closing
    price.
    """
    return _subtract(market_close_et, CANCEL_MODIFY_FREEZE_BEFORE_CLOSE)


def is_past_closing_auction_cutoff(
    submission_time: datetime,
    cutoff: Optional[time] = None,
    market_close_et: time = REGULAR_SESSION_CLOSE_ET,
) -> bool:
    """Return True if ``submission_time`` is at or past the auction entry cutoff.

    ``submission_time`` must be timezone-aware; it is converted to
    America/New_York before comparison, so a UTC- or Pacific-stamped clock cannot
    be mistaken for an ET one.

    Args:
        submission_time: Timezone-aware datetime of the intended submission.
        cutoff: Explicit ET cutoff time-of-day. When None (the default) the
            cutoff is derived from ``market_close_et`` as the conservative,
            venue- and order-type-portable deadline: ten minutes before the
            scheduled close (15:50 on a regular 16:00 close). Use
            :func:`entry_cutoff_for` for a venue-specific deadline.
        market_close_et: Scheduled end of Core Trading Hours in ET. Pass
            ``EARLY_CLOSE_SESSION_CLOSE_ET`` (13:00) on an early-close day --
            NYSE deadlines are defined relative to the scheduled close and move
            with it (NYSE Rule 7.35(a)(8)).

    Raises:
        ValueError: if ``submission_time`` is naive (tzinfo is None).
    """
    effective_cutoff = (
        cutoff if cutoff is not None
        else _subtract(market_close_et, CANCEL_MODIFY_FREEZE_BEFORE_CLOSE)
    )
    return to_eastern(submission_time).time() >= effective_cutoff


def validate_submission_window(
    submission_time: datetime,
    cutoff: Optional[time] = None,
    market_close_et: time = REGULAR_SESSION_CLOSE_ET,
) -> None:
    """Raise ValueError if ``submission_time`` is past the auction entry cutoff.

    Convenience wrapper around :func:`is_past_closing_auction_cutoff` for
    pre-trade enforcement. At or after the cutoff, NYSE accepts new MOC/LOC
    orders only contra to a published Significant Closing Imbalance and no
    longer permits cancellation (NYSE Rule 7.35B), and both venues have frozen
    free cancel/modify (Nasdaq Equity 4 Rules 4702(b)(11), 4702(b)(12)).
    """
    if is_past_closing_auction_cutoff(submission_time, cutoff, market_close_et):
        effective_cutoff = (
            cutoff if cutoff is not None
            else _subtract(market_close_et, CANCEL_MODIFY_FREEZE_BEFORE_CLOSE)
        )
        raise ValueError(
            f"Submission time {to_eastern(submission_time).isoformat()} is at or past "
            f"the closing auction cutoff {effective_cutoff.isoformat()} ET for a "
            f"{market_close_et.isoformat()} ET close; unconditional MOC/LOC entry is "
            f"not permitted after this deadline (NYSE Rule 7.35B / "
            f"Nasdaq Equity 4 Rule 4702)."
        )
