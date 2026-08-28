"""opening-auction-imbalance-based-execution.

Consumes opening-auction imbalance feeds (Nasdaq Opening Cross NOII, NYSE Core
Open Auction imbalance publication) and derives a contra-side on-open order that
is legal to enter at the intended submission time on the listing venue.

Venue timing rules encoded here (all times US/Eastern, cross at 09:30:00):

Nasdaq Opening Cross (Nasdaq Equity 4 Rules 4702(b)(8)-(10) and 4752; SEC order
approving SR-NASDAQ-2021-004, 86 FR 18349, implemented 2021-04-26; Nasdaq
Opening and Closing Crosses FAQ, 2025; Nasdaq TotalView-ITCH 5.0 spec s1.6):
  * Imbalance dissemination begins at 09:25, every 10 seconds until 09:28, then
    every second until the cross.
  * Between 09:25 and 09:28 only the Current Reference Price, Paired Shares,
    Imbalance Shares and Imbalance Direction are disseminated. The Near and Far
    Indicative Clearing Prices are NOT published before 09:28.
  * MOO orders must be received before 09:28; entries after 09:28 are rejected.
  * LOO orders may be entered until 09:29:30. An LOO entered after 09:28 is
    accepted at its limit price unless that limit is more aggressive than the
    09:28 Current Reference Price or the prior day's Nasdaq Official Closing
    Price, in which case the venue re-prices it to the more aggressive of the
    two and converts DAY to IOC.
  * OIO (Opening Imbalance Only) orders are limit-priced, execute only in the
    Opening Cross and only against MOO/LOO/Early Market Hours orders, and may be
    entered until the cross executes.
  * On-open orders may only be modified or cancelled before 09:25.

NYSE Core Open Auction (NYSE Rule 7.31(c)(1) and the Rule 7.35 series; NYSE
opening auction timetable, 2026):
  * Opening imbalance information is published from 08:00, every second when
    changed, until the security opens.
  * MOO and LOO orders are accepted until the DMM opens the security.
  * From 09:29 requests to cancel or cancel-and-replace MOO and LOO orders are
    rejected, and the Core Open Auction Imbalance Freeze runs 09:29:55-09:30.
  * NYSE has no Opening Imbalance Only order type.

The cancel/modify freeze is the dominant risk of this strategy: on Nasdaq an
on-open order placed at 09:26 cannot be pulled at 09:27, so sizing and price
protection must already be correct at submission time. Every report states
whether the order it describes is still cancellable.

This module derives order parameters. It does not submit, amend or cancel
orders, and it does not model fills.
"""
from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

US_EASTERN = ZoneInfo("America/New_York")

#: Scheduled US equity opening cross / Core Open Auction time.
MARKET_OPEN_ET = time(9, 30, 0)

# ITCH 5.0 NOII Imbalance Direction codes (Nasdaq TotalView-ITCH 5.0 spec,
# section 1.6). 'O' and 'P' are non-actionable states, not imbalance sides.
IMBALANCE_DIRECTIONS = frozenset({"B", "S", "N", "O", "P"})
ACTIONABLE_DIRECTIONS = frozenset({"B", "S"})

# ITCH 5.0 NOII Cross Type codes. Only 'O' (Nasdaq Opening Cross) is in scope;
# 'C' (closing), 'H' (IPO/halt) and 'A' (Extended Trading Close) crosses have
# different order types, cutoffs and price protections.
OPENING_CROSS_TYPE = "O"


class AuctionVenue(Enum):
    """Primary listing venue whose opening-auction rules apply."""

    NASDAQ = "NASDAQ"
    NYSE = "NYSE"


class OnOpenOrderType(Enum):
    """On-open order type to generate.

    MOO is unpriced and therefore has no price protection at the cross; LOO and
    OIO carry a limit price. OIO is Nasdaq-only.
    """

    MOO = "MOO"
    LOO = "LOO"
    OIO = "OIO"


class PriceBasis(Enum):
    """Which disseminated price the limit is derived from."""

    FAR = "FAR"    # hypothetical clearing price for cross orders only
    NEAR = "NEAR"  # hypothetical clearing price for cross plus continuous orders
    REF = "REF"    # Current Reference Price


class ExecutionStatus(str, Enum):
    """Outcome of processing one imbalance observation.

    Subclasses ``str`` so reports stay JSON-serialisable and comparable against
    the plain string statuses used by earlier versions of this skill.
    """

    ORDER_GENERATED = "ORDER_GENERATED"
    ENGINE_DISABLED = "ENGINE_DISABLED"
    INVALID_INPUT = "INVALID_INPUT"
    WRONG_CROSS_TYPE = "WRONG_CROSS_TYPE"
    SECURITY_PAUSED = "SECURITY_PAUSED"
    IMBALANCE_NOT_CALCULABLE = "IMBALANCE_NOT_CALCULABLE"
    NO_IMBALANCE_TRIGGER = "NO_IMBALANCE_TRIGGER"
    STALE_IMBALANCE_DATA = "STALE_IMBALANCE_DATA"
    CUTOFF_EXCEEDED = "CUTOFF_EXCEEDED"
    ORDER_TYPE_UNSUPPORTED_BY_VENUE = "ORDER_TYPE_UNSUPPORTED_BY_VENUE"
    UNPRICED_ORDER_NOT_PERMITTED = "UNPRICED_ORDER_NOT_PERMITTED"
    INDICATIVE_PRICE_UNAVAILABLE = "INDICATIVE_PRICE_UNAVAILABLE"
    QUANTITY_BELOW_MINIMUM = "QUANTITY_BELOW_MINIMUM"
    DUPLICATE_SUPPRESSED = "DUPLICATE_SUPPRESSED"


@dataclass(frozen=True)
class VenueAuctionRules:
    """Venue-published deadlines, expressed as seconds before the 09:30 cross.

    Larger values are earlier in the morning: 300.0 is 09:25, 0.0 is 09:30.
    """

    venue: AuctionVenue
    #: First dissemination of opening imbalance information.
    imbalance_feed_start_s: float
    #: Point at which on-open orders can no longer be cancelled or modified.
    #: At or after it the order is committed capital.
    cancel_modify_freeze_s: float
    #: Entry deadline per order type. ``None`` means the venue does not offer it.
    entry_cutoff_s: Dict[OnOpenOrderType, Optional[float]]
    #: Point from which indicative clearing prices are published, or ``None``
    #: when the venue publishes no such fixed time and only the disseminated
    #: value itself can be trusted.
    indicative_price_start_s: Optional[float] = None
    #: Window in which a late LOO is subject to venue re-pricing, as
    #: ``(start_s, end_s)`` with ``start_s > end_s``. ``None`` when unavailable.
    late_loo_window_s: Optional[Tuple[float, float]] = None


#: Nasdaq: 09:25 = 300s, 09:28 = 120s, 09:29:30 = 30s before the 09:30 cross.
NASDAQ_RULES = VenueAuctionRules(
    venue=AuctionVenue.NASDAQ,
    imbalance_feed_start_s=300.0,
    cancel_modify_freeze_s=300.0,
    entry_cutoff_s={
        OnOpenOrderType.MOO: 120.0,
        OnOpenOrderType.LOO: 30.0,
        OnOpenOrderType.OIO: 0.0,
    },
    indicative_price_start_s=120.0,
    late_loo_window_s=(120.0, 30.0),
)

#: NYSE: imbalance from 08:00 = 5400s; cancel/replace rejected from 09:29 = 60s;
#: MOO/LOO accepted until the DMM opens the security.
NYSE_RULES = VenueAuctionRules(
    venue=AuctionVenue.NYSE,
    imbalance_feed_start_s=5400.0,
    cancel_modify_freeze_s=60.0,
    entry_cutoff_s={
        OnOpenOrderType.MOO: 0.0,
        OnOpenOrderType.LOO: 0.0,
        OnOpenOrderType.OIO: None,
    },
    indicative_price_start_s=None,
    late_loo_window_s=None,
)

VENUE_RULES: Dict[AuctionVenue, VenueAuctionRules] = {
    AuctionVenue.NASDAQ: NASDAQ_RULES,
    AuctionVenue.NYSE: NYSE_RULES,
}


@dataclass
class OpeningAuctionImbalanceBasedExecutionConfig:
    """Strategy parameters.

    Only the deadlines in :data:`VENUE_RULES` are venue-mandated. Every
    threshold below is a strategy choice, not an exchange requirement.
    """

    enabled: bool = True
    venue: AuctionVenue = AuctionVenue.NASDAQ
    order_type: OnOpenOrderType = OnOpenOrderType.OIO
    price_basis: PriceBasis = PriceBasis.FAR
    #: Hard upper bound on generated order quantity, in shares.
    size: int = 5_000
    #: Fraction of the published imbalance this strategy is willing to absorb.
    participation_pct: float = 0.10
    #: Cap on the order as a fraction of total auction interest (paired + imbalance).
    max_pct_of_auction_volume: float = 0.05
    #: Minimum imbalance ratio required to act.
    imbalance_ratio_threshold: float = 0.20
    #: Minimum published imbalance, in shares, required to act.
    min_imbalance_qty: int = 10_000
    #: Margin added to the venue entry cutoff, covering strategy compute plus the
    #: broker/exchange hop. The deadline applies when the exchange receives the
    #: order, not when this function returns.
    entry_safety_buffer_seconds: float = 5.0
    #: Reject observations older than this. Nasdaq publishes every 10s before
    #: 09:28 and every second after; NYSE every second when changed.
    max_feed_age_seconds: float = 15.0
    #: Passive offset applied to the limit, in basis points. Positive prices the
    #: order away from the indicative clearing price (buy lower, sell higher),
    #: trading fill probability for a wider liquidity premium.
    price_offset_bps: float = 0.0
    tick_size: float = 0.01
    lot_size: int = 100
    #: Orders smaller than this after capping and lot rounding are not sent.
    min_order_qty: int = 100
    #: MOO carries no price protection at the cross, so generating one requires
    #: an explicit opt-in.
    allow_unpriced_moo: bool = False
    #: Namespace for deterministic client order IDs.
    strategy_id: str = "open-auction-imb"

    def __post_init__(self) -> None:
        """Reject a configuration that would silently corrupt sizing or pricing.

        A NaN participation rate makes every ``min()`` comparison unreliable and
        a negative cap yields a negative quantity, so these are caught at
        construction rather than at the first live imbalance.
        """
        if self.venue not in VENUE_RULES:
            raise ValueError(f"unsupported venue: {self.venue!r}")
        for name in ("participation_pct", "max_pct_of_auction_volume",
                     "imbalance_ratio_threshold"):
            value = getattr(self, name)
            if not _is_finite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be a finite fraction in [0, 1], got {value!r}")
        for name in ("size", "min_imbalance_qty", "lot_size", "min_order_qty"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative int, got {value!r}")
        for name in ("entry_safety_buffer_seconds", "max_feed_age_seconds",
                     "price_offset_bps", "tick_size"):
            value = getattr(self, name)
            if not _is_finite(value) or value < 0:
                raise ValueError(
                    f"{name} must be a finite, non-negative number, got {value!r}")


@dataclass
class AuctionImbalanceData:
    """One opening-auction imbalance observation.

    ``seconds_to_open`` is the time remaining until the 09:30:00 ET cross at the
    moment the observation was taken; use :func:`seconds_to_open_from` to derive
    it from a timezone-aware clock. ``far_price`` and ``near_price`` are the
    indicative clearing prices; a non-positive value means the venue has not
    disseminated one, which is the normal state on Nasdaq before 09:28.
    """

    symbol: str
    paired_qty: int
    imbalance_qty: int
    imbalance_side: str
    far_price: float
    near_price: float
    ref_price: float
    seconds_to_open: float
    cross_type: str = OPENING_CROSS_TYPE
    #: Age of this observation at the moment it is being acted on.
    feed_age_seconds: float = 0.0
    #: Trading session date (``YYYY-MM-DD``), used to scope idempotency keys.
    session_date: str = ""


@dataclass
class AuctionExecutionReport:
    """Auditable outcome of one :meth:`process_auction_imbalance` call."""

    symbol: str
    imbalance_ratio: float
    order_generated: Optional[Dict[str, Any]]
    status: str
    audit_notes: str
    venue: str = ""
    order_type: str = ""
    #: True while the venue would still accept a cancel/modify of this order.
    is_cancellable: bool = False
    #: Set when a Nasdaq LOO would arrive inside the 09:28-09:29:30 window and
    #: may therefore be re-priced by the venue.
    late_loo_reprice_risk: bool = False
    #: Non-fatal inconsistencies observed in the feed.
    feed_warnings: List[str] = field(default_factory=list)


def seconds_to_open_from(now: datetime) -> float:
    """Seconds remaining until the 09:30:00 ET opening cross.

    Rejects naive datetimes: a UTC-stamped feed compared raw against an Eastern
    deadline is how orders get sent after the cutoff.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware; naive datetimes are ambiguous")
    et_now = now.astimezone(US_EASTERN)
    open_dt = et_now.replace(
        hour=MARKET_OPEN_ET.hour,
        minute=MARKET_OPEN_ET.minute,
        second=MARKET_OPEN_ET.second,
        microsecond=0,
    )
    return (open_dt - et_now).total_seconds()


def imbalance_ratio(paired_qty: int, imbalance_qty: int) -> float:
    """Share of total auction interest that is unpaired.

    ``imbalance / (paired + imbalance)``. Returns ``0.0`` for an empty book
    rather than dividing by zero.
    """
    total = paired_qty + imbalance_qty
    if total <= 0:
        return 0.0
    return imbalance_qty / float(total)


def _is_finite(value: Any) -> bool:
    """True for a real, finite int/float (``bool`` excluded)."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


#: Tolerance for snapping a tick count that is already integral. ``100.07 /
#: 0.01`` is 10006.999999999998 in binary floating point, so a bare ceil() would
#: push a price that is already on a tick up by a full tick.
_TICK_EPSILON = 1e-9


def _round_away_from_aggressive(price: float, side: str, tick_size: float) -> float:
    """Round a limit to a permissible increment without becoming more aggressive.

    A buy limit rounds down and a sell limit rounds up, so tick rounding can
    never silently widen the price the strategy is willing to pay. A price that
    is already on a tick is left alone rather than moved by float error.
    """
    if tick_size <= 0:
        return price
    ticks = price / tick_size
    nearest = round(ticks)
    if abs(ticks - nearest) < _TICK_EPSILON:
        rounded = nearest
    else:
        rounded = math.floor(ticks) if side == "BUY" else math.ceil(ticks)
    return round(rounded * tick_size, 10)


class OpeningAuctionImbalanceBasedExecutionEngine:
    """Derives contra-side on-open orders from opening-auction imbalance data.

    The engine is single-threaded and stateful: it remembers the order intents it
    has already emitted this session so that re-processing the same imbalance on
    successive feed updates does not produce duplicate orders.
    """

    def __init__(self, config: OpeningAuctionImbalanceBasedExecutionConfig) -> None:
        self.config = config
        self.state = "INITIALIZED"
        self.orders: List[Dict[str, Any]] = []
        self._emitted_intents: Dict[str, Dict[str, Any]] = {}

    # -- public API ---------------------------------------------------------

    def process_auction_imbalance(
        self, imbalance: AuctionImbalanceData
    ) -> AuctionExecutionReport:
        """Evaluate one imbalance observation and derive a contra-side order.

        Gates are applied in order: engine enabled, input validity, cross type,
        imbalance state, feed freshness, trigger thresholds, venue entry cutoff,
        order-type support, pricing, sizing, idempotency.
        """
        cfg = self.config
        rules = VENUE_RULES[cfg.venue]

        if not cfg.enabled:
            return self._report(
                imbalance, 0.0, None, ExecutionStatus.ENGINE_DISABLED,
                "Execution engine is disabled.")

        invalid = self._validate(imbalance)
        if invalid is not None:
            logger.warning("INVALID_INPUT [%s]: %s", imbalance.symbol, invalid)
            return self._report(
                imbalance, 0.0, None, ExecutionStatus.INVALID_INPUT, invalid)

        if imbalance.cross_type != OPENING_CROSS_TYPE:
            notes = (
                f"Cross type {imbalance.cross_type!r} is not the opening cross "
                f"({OPENING_CROSS_TYPE!r}); closing, IPO/halt and Extended Trading "
                f"Close crosses use different order types and cutoffs.")
            logger.warning("WRONG_CROSS_TYPE [%s]: %s", imbalance.symbol, notes)
            return self._report(
                imbalance, 0.0, None, ExecutionStatus.WRONG_CROSS_TYPE, notes)

        if imbalance.imbalance_side == "P":
            notes = (
                "Security is paused or halted (ITCH imbalance direction 'P'); "
                "no auction order.")
            logger.warning("SECURITY_PAUSED [%s]: %s", imbalance.symbol, notes)
            return self._report(
                imbalance, 0.0, None, ExecutionStatus.SECURITY_PAUSED, notes)

        if imbalance.imbalance_side == "O":
            notes = (
                "Venue reports insufficient orders to calculate an imbalance (ITCH "
                "imbalance direction 'O'); the published quantities are not a "
                "tradable imbalance.")
            logger.info("IMBALANCE_NOT_CALCULABLE [%s]: %s", imbalance.symbol, notes)
            return self._report(
                imbalance, 0.0, None, ExecutionStatus.IMBALANCE_NOT_CALCULABLE, notes)

        ratio = imbalance_ratio(imbalance.paired_qty, imbalance.imbalance_qty)

        if imbalance.feed_age_seconds > cfg.max_feed_age_seconds:
            notes = (
                f"Imbalance observation is {imbalance.feed_age_seconds:.1f}s old, past "
                f"the {cfg.max_feed_age_seconds:.1f}s limit; the auction book has "
                f"moved on.")
            logger.warning("STALE_IMBALANCE_DATA [%s]: %s", imbalance.symbol, notes)
            return self._report(
                imbalance, ratio, None, ExecutionStatus.STALE_IMBALANCE_DATA, notes)

        if (imbalance.imbalance_side not in ACTIONABLE_DIRECTIONS
                or ratio < cfg.imbalance_ratio_threshold
                or imbalance.imbalance_qty < cfg.min_imbalance_qty):
            notes = (
                f"No trigger [{imbalance.symbol}]: side={imbalance.imbalance_side!r}, "
                f"ratio={ratio:.2%} (min {cfg.imbalance_ratio_threshold:.2%}), "
                f"qty={imbalance.imbalance_qty} (min {cfg.min_imbalance_qty}).")
            logger.info("NO_IMBALANCE_TRIGGER: %s", notes)
            return self._report(
                imbalance, ratio, None, ExecutionStatus.NO_IMBALANCE_TRIGGER, notes)

        # Venue entry cutoff, evaluated against the projected arrival time at the
        # exchange rather than the observation time.
        cutoff = rules.entry_cutoff_s.get(cfg.order_type)
        if cutoff is None:
            notes = (
                f"{cfg.order_type.value} orders are not offered by {cfg.venue.value} "
                f"for the opening auction.")
            logger.warning(
                "ORDER_TYPE_UNSUPPORTED_BY_VENUE [%s]: %s", imbalance.symbol, notes)
            return self._report(
                imbalance, ratio, None,
                ExecutionStatus.ORDER_TYPE_UNSUPPORTED_BY_VENUE, notes)

        arrival_s = imbalance.seconds_to_open - cfg.entry_safety_buffer_seconds
        if arrival_s <= cutoff:
            notes = (
                f"AUCTION CUTOFF EXCEEDED [{imbalance.symbol}]: projected arrival at "
                f"{arrival_s:.1f}s to open (observed {imbalance.seconds_to_open:.1f}s "
                f"less {cfg.entry_safety_buffer_seconds:.1f}s buffer) is at or past the "
                f"{cfg.venue.value} {cfg.order_type.value} entry cutoff of "
                f"{cutoff:.1f}s.")
            logger.warning("CUTOFF_EXCEEDED: %s", notes)
            return self._report(
                imbalance, ratio, None, ExecutionStatus.CUTOFF_EXCEEDED, notes)

        if cfg.order_type is OnOpenOrderType.MOO and not cfg.allow_unpriced_moo:
            notes = (
                "MOO executes at the cross price with no price protection; set "
                "allow_unpriced_moo=True to accept that, or use a limit-priced "
                "LOO/OIO instead.")
            logger.warning(
                "UNPRICED_ORDER_NOT_PERMITTED [%s]: %s", imbalance.symbol, notes)
            return self._report(
                imbalance, ratio, None,
                ExecutionStatus.UNPRICED_ORDER_NOT_PERMITTED, notes)

        contra_side = "SELL" if imbalance.imbalance_side == "B" else "BUY"
        warnings = self._feed_warnings(imbalance, rules)

        limit_price: Optional[float] = None
        if cfg.order_type is not OnOpenOrderType.MOO:
            basis = self._price_basis_value(imbalance, rules)
            if basis is None or basis <= 0.0:
                notes = (
                    f"{cfg.price_basis.value} price is not disseminated for "
                    f"{imbalance.symbol} at {imbalance.seconds_to_open:.1f}s to open; "
                    f"refusing to price a {cfg.order_type.value} off an absent value.")
                logger.warning("INDICATIVE_PRICE_UNAVAILABLE: %s", notes)
                return self._report(
                    imbalance, ratio, None,
                    ExecutionStatus.INDICATIVE_PRICE_UNAVAILABLE, notes,
                    warnings=warnings)
            limit_price = self._limit_price(basis, contra_side)

        qty = self._order_qty(imbalance)
        if qty < cfg.min_order_qty:
            notes = (
                f"Capped quantity {qty} for {imbalance.symbol} is below the "
                f"{cfg.min_order_qty}-share minimum; no order sent.")
            logger.info("QUANTITY_BELOW_MINIMUM: %s", notes)
            return self._report(
                imbalance, ratio, None, ExecutionStatus.QUANTITY_BELOW_MINIMUM,
                notes, warnings=warnings)

        intent_key = self._intent_key(imbalance, contra_side)
        existing = self._emitted_intents.get(intent_key)
        if existing is not None:
            notes = (
                f"Order intent {existing['client_order_id']} for {imbalance.symbol} "
                f"was already emitted this session; suppressing duplicate on repeated "
                f"imbalance update.")
            logger.info("DUPLICATE_SUPPRESSED: %s", notes)
            return self._report(
                imbalance, ratio, existing, ExecutionStatus.DUPLICATE_SUPPRESSED,
                notes, warnings=warnings)

        is_cancellable = imbalance.seconds_to_open > rules.cancel_modify_freeze_s
        reprice_risk = self._late_loo_reprice_risk(arrival_s, rules)
        prices_published = self._prices_published(imbalance, rules)

        order: Dict[str, Any] = {
            "client_order_id": self._client_order_id(intent_key),
            "symbol": imbalance.symbol,
            "venue": cfg.venue.value,
            "side": contra_side,
            "qty": qty,
            "type": cfg.order_type.value,
            "limit_price": limit_price,
            "price_basis": cfg.price_basis.value if limit_price is not None else None,
            "ref_price": imbalance.ref_price,
            "near_price": imbalance.near_price if prices_published else None,
            "far_price": imbalance.far_price if prices_published else None,
            "seconds_to_open_at_decision": imbalance.seconds_to_open,
            "is_cancellable": is_cancellable,
            "late_loo_reprice_risk": reprice_risk,
        }
        self.orders.append(order)
        self._emitted_intents[intent_key] = order

        price_text = "unpriced" if limit_price is None else f"limit {limit_price:.4f}"
        notes = (
            f"AUCTION ORDER GENERATED [{imbalance.symbol}]: {contra_side} {qty} "
            f"{cfg.order_type.value} ({price_text}) on {cfg.venue.value} against a "
            f"{imbalance.imbalance_side} imbalance of {imbalance.imbalance_qty} shares "
            f"({ratio:.2%} ratio) at {imbalance.seconds_to_open:.1f}s to open. "
            f"{'Cancellable' if is_cancellable else 'NOT cancellable, committed capital'}.")
        logger.info("ORDER_GENERATED: %s", notes)

        return self._report(
            imbalance, ratio, order, ExecutionStatus.ORDER_GENERATED, notes,
            is_cancellable=is_cancellable, reprice_risk=reprice_risk,
            warnings=warnings)

    # -- internals ----------------------------------------------------------

    def _validate(self, imb: AuctionImbalanceData) -> Optional[str]:
        """Return a description of the first validation failure, or ``None``."""
        if not isinstance(imb.symbol, str) or not imb.symbol:
            return "symbol must be a non-empty string."
        for name in ("paired_qty", "imbalance_qty"):
            value = getattr(imb, name)
            if not isinstance(value, int) or isinstance(value, bool):
                return f"{name} must be an int, got {type(value).__name__}."
            if value < 0:
                return f"{name} must be non-negative, got {value}."
        if imb.imbalance_side not in IMBALANCE_DIRECTIONS:
            return (
                f"imbalance_side {imb.imbalance_side!r} is not a valid ITCH imbalance "
                f"direction {sorted(IMBALANCE_DIRECTIONS)}.")
        for name in ("far_price", "near_price", "ref_price"):
            if not _is_finite(getattr(imb, name)):
                return f"{name} must be a finite number, got {getattr(imb, name)!r}."
        # A NaN or infinite clock reading must never reach a deadline comparison:
        # every ordering test against NaN is False, which would silently open the
        # cutoff gate.
        if not _is_finite(imb.seconds_to_open):
            return (
                f"seconds_to_open must be a finite number, got "
                f"{imb.seconds_to_open!r}.")
        if not _is_finite(imb.feed_age_seconds) or imb.feed_age_seconds < 0:
            return (
                f"feed_age_seconds must be a finite, non-negative number, got "
                f"{imb.feed_age_seconds!r}.")
        return None

    def _prices_published(
        self, imb: AuctionImbalanceData, rules: VenueAuctionRules
    ) -> bool:
        """Whether the venue publishes indicative clearing prices at this time."""
        if rules.indicative_price_start_s is None:
            return True
        return imb.seconds_to_open <= rules.indicative_price_start_s

    def _feed_warnings(
        self, imb: AuctionImbalanceData, rules: VenueAuctionRules
    ) -> List[str]:
        """Non-fatal feed inconsistencies worth recording in the audit trail."""
        warnings: List[str] = []
        if imb.seconds_to_open > rules.imbalance_feed_start_s:
            warnings.append(
                f"observation timestamped {imb.seconds_to_open:.1f}s to open, before "
                f"{rules.venue.value} begins publishing opening imbalance data at "
                f"{rules.imbalance_feed_start_s:.0f}s")
        if (not self._prices_published(imb, rules)
                and (imb.near_price > 0 or imb.far_price > 0)):
            warnings.append(
                f"near/far price populated at {imb.seconds_to_open:.1f}s to open "
                f"although {rules.venue.value} publishes none before "
                f"{rules.indicative_price_start_s:.0f}s; check the feed parser")
        for text in warnings:
            logger.warning("FEED_WARNING [%s]: %s", imb.symbol, text)
        return warnings

    def _price_basis_value(
        self, imb: AuctionImbalanceData, rules: VenueAuctionRules
    ) -> Optional[float]:
        """The configured price basis, or ``None`` if the venue publishes none yet."""
        if self.config.price_basis is PriceBasis.REF:
            return imb.ref_price
        if not self._prices_published(imb, rules):
            # Treat an indicative price as absent whenever the venue does not
            # publish one yet, whatever the message happens to carry.
            return None
        if self.config.price_basis is PriceBasis.FAR:
            return imb.far_price
        return imb.near_price

    def _limit_price(self, basis: float, side: str) -> float:
        offset = self.config.price_offset_bps / 10_000.0
        raw = basis * (1.0 - offset) if side == "BUY" else basis * (1.0 + offset)
        return _round_away_from_aggressive(raw, side, self.config.tick_size)

    def _order_qty(self, imb: AuctionImbalanceData) -> int:
        """Smallest of the three caps, floored to a whole lot."""
        cfg = self.config
        total_interest = imb.paired_qty + imb.imbalance_qty
        capped = min(
            float(cfg.size),
            cfg.participation_pct * imb.imbalance_qty,
            cfg.max_pct_of_auction_volume * total_interest,
        )
        lot = max(1, cfg.lot_size)
        return int(math.floor(max(0.0, capped) / lot) * lot)

    def _late_loo_reprice_risk(
        self, arrival_s: float, rules: VenueAuctionRules
    ) -> bool:
        if self.config.order_type is not OnOpenOrderType.LOO:
            return False
        if rules.late_loo_window_s is None:
            return False
        start_s, end_s = rules.late_loo_window_s
        return end_s < arrival_s <= start_s

    def _intent_key(self, imb: AuctionImbalanceData, side: str) -> str:
        """Stable identity of one trading intent, independent of feed updates."""
        cfg = self.config
        return "|".join((
            cfg.strategy_id, cfg.venue.value, imb.session_date, imb.symbol,
            side, cfg.order_type.value,
        ))

    def _client_order_id(self, intent_key: str) -> str:
        digest = hashlib.sha1(intent_key.encode("utf-8")).hexdigest()[:16]
        return f"{self.config.strategy_id}-{digest}"

    def _report(
        self,
        imb: AuctionImbalanceData,
        ratio: float,
        order: Optional[Dict[str, Any]],
        status: ExecutionStatus,
        notes: str,
        *,
        is_cancellable: bool = False,
        reprice_risk: bool = False,
        warnings: Optional[List[str]] = None,
    ) -> AuctionExecutionReport:
        return AuctionExecutionReport(
            symbol=imb.symbol,
            imbalance_ratio=round(ratio, 4),
            order_generated=order,
            status=status.value,
            audit_notes=notes,
            venue=self.config.venue.value,
            order_type=self.config.order_type.value,
            is_cancellable=is_cancellable,
            late_loo_reprice_risk=reprice_risk,
            feed_warnings=list(warnings or []),
        )
