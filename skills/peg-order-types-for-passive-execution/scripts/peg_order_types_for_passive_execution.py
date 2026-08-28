"""Pegged limit-price computation for passive execution in US NMS equities.

Resolves the reference price for Primary, Midpoint and Market pegs against the
NBBO, applies a side-relative discretionary offset, and then clamps the result
against every protective bound that applies on that side of the market before
quantizing to the instrument's minimum price variation.

Design note -- every protective bound in this module points the same direction.
For a BUY, the passivity limit, the LULD upper band and the limit cap are all
*ceilings*; for a SELL, the passivity limit, the Regulation SHO Rule 201 floor,
the LULD lower band and the limit cap are all *floors*. Two protective bounds
can therefore never contradict each other: the binding one is simply the
tightest, and clamping always moves the order less aggressive, never more.

Jurisdiction: US NMS equities (SEC Regulation NMS, Regulation SHO, the LULD
Plan). Reference prices and offsets follow Nasdaq Equity 4, Rule 4703(d)
semantics; see references/standards.md for citations.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from enum import Enum
from typing import Optional, Tuple, Union

logger = logging.getLogger(__name__)
# Library convention: emit nothing unless the host application configures
# logging. Operators running this in production must attach a handler -- the
# suspension warnings are the only signal that pegging has stopped.
logger.addHandler(logging.NullHandler())

PriceLike = Union[Decimal, int, float, str]

__all__ = [
    "PegError",
    "PegSpecError",
    "Side",
    "PegType",
    "RoundDirection",
    "PegStatus",
    "SuspendReason",
    "PegPricingConfig",
    "PegOrder",
    "NBBOQuote",
    "PegOrderReport",
    "RepriceDecision",
    "PegOrderTypesForPassiveExecutionEngine",
]


class PegError(ValueError):
    """Base class for every error raised by this module."""


class PegSpecError(PegError):
    """The order, quote or config specification is invalid.

    Raised only for caller-side programming errors (an unknown side, a
    non-positive tick, a NaN offset). Adverse *market state* -- a crossed
    book, a stale or non-positive quote -- is never raised; it is returned as a
    ``SUSPENDED`` report so the caller can log and skip the tick.
    """


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class PegType(str, Enum):
    """Peg reference, per Nasdaq Equity 4 Rule 4703(d).

    Values match FIX ``PegPriceType(1094)``: PRIMARY = 5, MIDPOINT = 2,
    MARKET = 4.
    """

    PRIMARY = "PRIMARY"
    MIDPOINT = "MIDPOINT"
    MARKET = "MARKET"


class RoundDirection(str, Enum):
    """Tick rounding direction, per FIX ``PegRoundDirection(838)``.

    PASSIVE (838=2): buy rounds down, sell rounds up -- never crosses a bound.
    AGGRESSIVE (838=1): buy rounds up, sell rounds down. Aggressive rounding is
    re-clamped against the protective bound afterwards, so it can never round
    an order through its cap, its LULD band or its Rule 201 floor.
    """

    PASSIVE = "PASSIVE"
    AGGRESSIVE = "AGGRESSIVE"


class PegStatus(str, Enum):
    PRICED = "PRICED"
    PRICED_CLAMPED = "PRICED_CLAMPED"
    SUSPENDED = "SUSPENDED"


class SuspendReason(str, Enum):
    SYMBOL_MISMATCH = "SYMBOL_MISMATCH"
    NON_FINITE_QUOTE = "NON_FINITE_QUOTE"
    NON_POSITIVE_QUOTE = "NON_POSITIVE_QUOTE"
    CROSSED_MARKET = "CROSSED_MARKET"
    UNPRICEABLE = "UNPRICEABLE"


# Constraint names, in the order used to break ties when two bounds are equally
# tight. Regulatory bounds are named first so an audit trail attributes a clamp
# to the rule that compelled it rather than to a coincident house limit.
CONSTRAINT_PRECEDENCE: Tuple[str, ...] = (
    "SHORT_SALE_201",
    "LULD_BAND",
    "LIMIT_CAP",
    "PASSIVITY",
)


def _to_decimal(value: PriceLike, label: str) -> Decimal:
    """Convert to Decimal without inheriting binary float dust.

    ``float`` inputs go through ``str`` so 0.07 becomes Decimal('0.07') rather
    than Decimal('0.070000000000000006938893903907228377647697925567626953125').
    """
    if isinstance(value, Decimal):
        candidate = value
    elif isinstance(value, bool):
        raise PegSpecError(f"{label} must be numeric, got bool")
    elif isinstance(value, (int, float, str)):
        if isinstance(value, float) and not math.isfinite(value):
            raise PegSpecError(f"{label} must be finite, got {value!r}")
        try:
            candidate = Decimal(str(value))
        except InvalidOperation as exc:
            raise PegSpecError(f"{label} is not a valid number: {value!r}") from exc
    else:
        raise PegSpecError(f"{label} must be numeric, got {type(value).__name__}")

    if not candidate.is_finite():
        raise PegSpecError(f"{label} must be finite, got {value!r}")
    return candidate


def _coerce_enum(value, enum_cls, label: str):
    """Accept either an enum member or its case-insensitive string name."""
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(str(value).upper())
    except ValueError as exc:
        raise PegSpecError(
            f"{label} must be one of {[m.value for m in enum_cls]}, got {value!r}"
        ) from exc


def _increment_exponent(increment: Decimal) -> Decimal:
    """Smallest power of ten that can represent `increment` exactly."""
    return Decimal(1).scaleb(increment.as_tuple().exponent)


def _round_to_increment(
    price: Decimal, increment: Decimal, direction: RoundDirection, side: Side
) -> Decimal:
    """Snap `price` onto the `increment` lattice in the requested direction."""
    if direction is RoundDirection.PASSIVE:
        rounding = ROUND_FLOOR if side is Side.BUY else ROUND_CEILING
    else:
        rounding = ROUND_CEILING if side is Side.BUY else ROUND_FLOOR
    steps = (price / increment).to_integral_value(rounding=rounding)
    return (steps * increment).quantize(_increment_exponent(increment))


@dataclass(frozen=True)
class PegPricingConfig:
    """Engine-wide pegging policy.

    Attributes:
        enforce_non_marketable: Clamp any pegged price that would lock or cross
            the contra quote back to the most aggressive resting price. This is
            what makes a Market peg usable in a passive strategy; leave it on
            unless the caller genuinely intends to take liquidity.
        round_direction: Tick rounding direction, per FIX PegRoundDirection.
        default_tick_size: Minimum price variation used when a quote does not
            carry one. $0.01 is the Rule 612 increment for NMS stocks priced at
            or above $1.00; sub-dollar names require $0.0001 and must set it
            explicitly on the quote.
        reprice_threshold_ticks: Minimum move, in ticks, before `should_reprice`
            authorises a cancel/replace. Guards against message-rate churn.
        allow_subpenny_midpoint: Permit a non-displayed Midpoint peg to price on
            the half-tick lattice, which is required to sit at the midpoint of a
            one-tick spread.
    """

    enforce_non_marketable: bool = True
    round_direction: RoundDirection = RoundDirection.PASSIVE
    default_tick_size: Decimal = Decimal("0.01")
    reprice_threshold_ticks: int = 1
    allow_subpenny_midpoint: bool = True

    def __post_init__(self) -> None:
        tick = _to_decimal(self.default_tick_size, "default_tick_size")
        if tick <= 0:
            raise PegSpecError(f"default_tick_size must be positive, got {tick}")
        object.__setattr__(self, "default_tick_size", tick)
        if not isinstance(self.reprice_threshold_ticks, int) or isinstance(
            self.reprice_threshold_ticks, bool
        ):
            raise PegSpecError("reprice_threshold_ticks must be an int")
        if self.reprice_threshold_ticks < 1:
            raise PegSpecError(
                f"reprice_threshold_ticks must be >= 1, got {self.reprice_threshold_ticks}"
            )
        object.__setattr__(
            self,
            "round_direction",
            _coerce_enum(self.round_direction, RoundDirection, "round_direction"),
        )


@dataclass
class PegOrder:
    """A pegged order instruction.

    Offset convention: `offset` is **side-relative and aggressive-positive**. A
    positive offset moves a BUY up and a SELL down (toward the contra side); a
    negative offset moves it away. This matches Nasdaq Rule 4703(d), where a buy
    with Primary Pegging and an aggressive $0.02 offset against an $11.00 inside
    bid prices at $11.02 and a passive $0.05 offset prices at $10.95.

    It is NOT the FIX convention: ``PegOffsetValue(211)`` is a signed amount
    added to the peg irrespective of side, so a passive SELL offset is negative
    there and positive here. Negate the offset for SELL orders when translating
    a report into a FIX ``PegInstructions`` block.

    Attributes:
        limit_cap: Hard price bound in the passive direction -- a maximum for a
            BUY, a minimum for a SELL. Equivalent to FIX ``PegLimitType(837)=1``
            (strict limit). Leaving it None removes the only protection against
            a peg chasing a runaway quote.
        is_displayed: False for a non-displayed (dark) order. Only a
            non-displayed Midpoint peg may price in sub-pennies under Rule 612.
        is_short_sale: Marks the sell as a short sale so the Regulation SHO
            Rule 201 floor is applied when the quote reports an active
            short-sale price test.
    """

    order_id: str
    symbol: str
    side: Side
    peg_type: PegType
    offset: Decimal = Decimal("0")
    limit_cap: Optional[Decimal] = None
    quantity: Decimal = Decimal("100")
    is_displayed: bool = True
    is_short_sale: bool = False

    def __post_init__(self) -> None:
        if not str(self.order_id).strip():
            raise PegSpecError("order_id must be a non-empty string")
        if not str(self.symbol).strip():
            raise PegSpecError("symbol must be a non-empty string")

        self.side = _coerce_enum(self.side, Side, "side")
        self.peg_type = _coerce_enum(self.peg_type, PegType, "peg_type")

        self.offset = _to_decimal(self.offset, "offset")
        self.quantity = _to_decimal(self.quantity, "quantity")
        if self.quantity <= 0:
            raise PegSpecError(f"quantity must be positive, got {self.quantity}")
        if self.limit_cap is not None:
            self.limit_cap = _to_decimal(self.limit_cap, "limit_cap")
            if self.limit_cap <= 0:
                raise PegSpecError(f"limit_cap must be positive, got {self.limit_cap}")
        if self.is_short_sale and self.side is not Side.SELL:
            raise PegSpecError("is_short_sale is only meaningful on a SELL order")


@dataclass
class NBBOQuote:
    """Consolidated top-of-book state for one instrument.

    Attributes:
        tick_size: Minimum price variation for this security. Falls back to the
            engine config when None.
        luld_upper_band / luld_lower_band: Current Limit Up-Limit Down price
            bands, when the caller tracks them. A pegged order priced more
            aggressively than its band is repriced to the band price.
        short_sale_restricted: True while the Rule 201 short-sale circuit
            breaker is active for this security.
    """

    symbol: str
    best_bid: Decimal
    best_ask: Decimal
    tick_size: Optional[Decimal] = None
    luld_upper_band: Optional[Decimal] = None
    luld_lower_band: Optional[Decimal] = None
    short_sale_restricted: bool = False

    def __post_init__(self) -> None:
        if not str(self.symbol).strip():
            raise PegSpecError("symbol must be a non-empty string")
        # Quote prices are market data, not a caller specification: a NaN or a
        # non-numeric field is normalised to a sentinel and surfaced as a
        # SUSPENDED report rather than raised, so one bad tick cannot abort a
        # replay loop.
        self.best_bid = self._coerce_quote(self.best_bid)
        self.best_ask = self._coerce_quote(self.best_ask)
        if self.tick_size is not None:
            self.tick_size = _to_decimal(self.tick_size, "tick_size")
            if self.tick_size <= 0:
                raise PegSpecError(f"tick_size must be positive, got {self.tick_size}")
        for label in ("luld_upper_band", "luld_lower_band"):
            value = getattr(self, label)
            if value is not None:
                value = _to_decimal(value, label)
                if value <= 0:
                    raise PegSpecError(f"{label} must be positive, got {value}")
                setattr(self, label, value)
        if (
            self.luld_upper_band is not None
            and self.luld_lower_band is not None
            and self.luld_lower_band > self.luld_upper_band
        ):
            raise PegSpecError(
                f"luld_lower_band ({self.luld_lower_band}) exceeds "
                f"luld_upper_band ({self.luld_upper_band})"
            )

    @staticmethod
    def _coerce_quote(value: PriceLike) -> Optional[Decimal]:
        try:
            candidate = _to_decimal(value, "quote")
        except PegSpecError:
            return None
        return None if candidate.is_nan() else candidate


@dataclass(frozen=True)
class PegOrderReport:
    """Outcome of one pegging evaluation.

    `effective_limit_price` is None whenever `status` is SUSPENDED; there is no
    safe price to submit and callers must not fall back to a default.
    """

    order_id: str
    symbol: str
    side: Side
    peg_type: PegType
    status: PegStatus
    reference_price: Optional[Decimal] = None
    calculated_price: Optional[Decimal] = None
    effective_limit_price: Optional[Decimal] = None
    binding_constraint: Optional[str] = None
    clamps: Tuple[str, ...] = ()
    is_cap_active: bool = False
    is_marketable: bool = False
    tick_size: Optional[Decimal] = None
    price_increment: Optional[Decimal] = None
    suspend_reason: Optional[SuspendReason] = None
    audit_notes: str = ""


@dataclass(frozen=True)
class RepriceDecision:
    """Whether an active pegged order should be cancel/replaced."""

    should_reprice: bool
    reason: str
    delta_ticks: Optional[Decimal] = None
    threshold_ticks: int = 1


class PegOrderTypesForPassiveExecutionEngine:
    """Computes pegged limit prices and repricing decisions.

    The engine is stateless with respect to orders: every call is a pure
    function of the order, the quote and the config, which keeps replay and live
    pricing identical.
    """

    def __init__(self, config: Optional[PegPricingConfig] = None):
        self.config = config or PegPricingConfig()

    # ------------------------------------------------------------------ pricing

    def calculate_pegged_price(self, order: PegOrder, nbbo: NBBOQuote) -> PegOrderReport:
        """Resolve the pegged limit price for `order` against `nbbo`.

        Raises:
            PegSpecError: the order or quote specification is invalid.
        """
        if not isinstance(order, PegOrder):
            raise PegSpecError(f"order must be a PegOrder, got {type(order).__name__}")
        if not isinstance(nbbo, NBBOQuote):
            raise PegSpecError(f"nbbo must be an NBBOQuote, got {type(nbbo).__name__}")

        suspension = self._validate_market_state(order, nbbo)
        if suspension is not None:
            return suspension

        bid, ask = nbbo.best_bid, nbbo.best_ask
        tick = nbbo.tick_size or self.config.default_tick_size
        increment = self._price_increment(order, tick)

        reference = self._reference_price(order, bid, ask)
        raw = reference + order.offset if order.side is Side.BUY else reference - order.offset

        bounds = self._protective_bounds(order, nbbo, tick, increment)
        unbounded = _round_to_increment(raw, increment, self.config.round_direction, order.side)
        priced, binding, clamps = self._apply_bounds(
            order.side, raw, unbounded, increment, bounds
        )

        if priced <= 0:
            return self._suspend(
                order,
                nbbo,
                SuspendReason.UNPRICEABLE,
                f"Constraints resolve to a non-positive limit price ({priced}); "
                f"reference=${reference}, binding={binding or 'none'}.",
                reference=reference,
                calculated=raw,
                tick=tick,
                increment=increment,
            )

        marketable = priced >= ask if order.side is Side.BUY else priced <= bid
        status = PegStatus.PRICED_CLAMPED if clamps else PegStatus.PRICED
        notes = (
            f"PEG {order.peg_type.value} {order.side.value} {order.symbol} "
            f"[{order.order_id}] {status.value}: ref=${reference} offset={order.offset:+} "
            f"raw=${raw} -> limit=${priced} (increment={increment}, "
            f"clamps={list(clamps) or 'none'}, binding={binding or 'none'}, "
            f"marketable={marketable})."
        )
        logger.info(notes)

        return PegOrderReport(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            peg_type=order.peg_type,
            status=status,
            reference_price=reference,
            calculated_price=raw,
            effective_limit_price=priced,
            binding_constraint=binding,
            clamps=clamps,
            is_cap_active="LIMIT_CAP" in clamps,
            is_marketable=marketable,
            tick_size=tick,
            price_increment=increment,
            audit_notes=notes,
        )

    # ---------------------------------------------------------------- repricing

    def should_reprice(
        self,
        active_limit_price: Optional[PriceLike],
        report: PegOrderReport,
        threshold_ticks: Optional[int] = None,
    ) -> RepriceDecision:
        """Decide whether an active order warrants a cancel/replace.

        A pegged order that chases every sub-tick quote flicker burns the venue
        message budget and forfeits queue position on each replace, so a move is
        only actioned once it reaches `threshold_ticks` full ticks.
        """
        threshold = (
            self.config.reprice_threshold_ticks if threshold_ticks is None else threshold_ticks
        )
        if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 1:
            raise PegSpecError(f"threshold_ticks must be an int >= 1, got {threshold!r}")

        if report.status is PegStatus.SUSPENDED or report.effective_limit_price is None:
            return RepriceDecision(False, "NO_VALID_PRICE", None, threshold)
        if active_limit_price is None:
            return RepriceDecision(True, "NO_ACTIVE_ORDER", None, threshold)

        active = _to_decimal(active_limit_price, "active_limit_price")
        tick = report.tick_size or self.config.default_tick_size
        delta_ticks = abs(report.effective_limit_price - active) / tick
        if delta_ticks >= threshold:
            return RepriceDecision(True, "THRESHOLD_MET", delta_ticks, threshold)
        return RepriceDecision(False, "BELOW_THRESHOLD", delta_ticks, threshold)

    # ----------------------------------------------------------------- internals

    def _validate_market_state(
        self, order: PegOrder, nbbo: NBBOQuote
    ) -> Optional[PegOrderReport]:
        if order.symbol != nbbo.symbol:
            return self._suspend(
                order,
                nbbo,
                SuspendReason.SYMBOL_MISMATCH,
                f"Order symbol {order.symbol!r} does not match quote symbol "
                f"{nbbo.symbol!r}; refusing to peg to another instrument.",
            )
        if nbbo.best_bid is None or nbbo.best_ask is None:
            return self._suspend(
                order,
                nbbo,
                SuspendReason.NON_FINITE_QUOTE,
                "NBBO contains a non-numeric or NaN price; pegging suspended.",
            )
        if nbbo.best_bid <= 0 or nbbo.best_ask <= 0:
            return self._suspend(
                order,
                nbbo,
                SuspendReason.NON_POSITIVE_QUOTE,
                f"NBBO is not positive (bid=${nbbo.best_bid}, ask=${nbbo.best_ask}); "
                "pegging suspended.",
            )
        if nbbo.best_bid > nbbo.best_ask:
            return self._suspend(
                order,
                nbbo,
                SuspendReason.CROSSED_MARKET,
                f"NBBO is crossed (bid=${nbbo.best_bid} > ask=${nbbo.best_ask}); "
                "a crossed consolidated quote indicates stale or bad market data, "
                "so pegging is suspended rather than priced.",
            )
        return None

    def _price_increment(self, order: PegOrder, tick: Decimal) -> Decimal:
        """Lattice the final price is snapped to.

        Rule 612 bars displaying, ranking or accepting an order priced finer
        than the MPV, but a non-displayed Midpoint peg is permitted to price in
        sub-pennies to reach the midpoint of a one-tick spread. Half the tick is
        exact for that case: the midpoint of two tick-aligned quotes always
        lands on the half-tick lattice.
        """
        if (
            order.peg_type is PegType.MIDPOINT
            and not order.is_displayed
            and self.config.allow_subpenny_midpoint
        ):
            return tick / 2
        return tick

    @staticmethod
    def _reference_price(order: PegOrder, bid: Decimal, ask: Decimal) -> Decimal:
        if order.peg_type is PegType.PRIMARY:
            return bid if order.side is Side.BUY else ask
        if order.peg_type is PegType.MARKET:
            return ask if order.side is Side.BUY else bid
        # A locked book (bid == ask) yields the locking price, which is what a
        # midpoint peg is defined to be there.
        return (bid + ask) / 2

    def _protective_bounds(
        self, order: PegOrder, nbbo: NBBOQuote, tick: Decimal, increment: Decimal
    ) -> Tuple[Tuple[str, Decimal], ...]:
        """Every bound that applies on this order's side, tightest-wins.

        BUY bounds are ceilings, SELL bounds are floors; a clamp therefore always
        makes the order less aggressive.
        """
        bounds = []
        if self.config.enforce_non_marketable:
            bounds.append(
                (
                    "PASSIVITY",
                    nbbo.best_ask - increment
                    if order.side is Side.BUY
                    else nbbo.best_bid + increment,
                )
            )
        if order.side is Side.SELL and order.is_short_sale and nbbo.short_sale_restricted:
            # Rule 201 permits a short sale only at a price above the current
            # NBB; venues reprice to one minimum increment above it.
            bounds.append(("SHORT_SALE_201", nbbo.best_bid + tick))
        band = nbbo.luld_upper_band if order.side is Side.BUY else nbbo.luld_lower_band
        if band is not None:
            bounds.append(("LULD_BAND", band))
        if order.limit_cap is not None:
            bounds.append(("LIMIT_CAP", order.limit_cap))
        return tuple(bounds)

    @staticmethod
    def _apply_bounds(
        side: Side,
        raw: Decimal,
        unbounded: Decimal,
        increment: Decimal,
        bounds: Tuple[Tuple[str, Decimal], ...],
    ) -> Tuple[Decimal, Optional[str], Tuple[str, ...]]:
        """Clamp `unbounded` to the tightest protective bound.

        Each bound is first snapped onto the price lattice in the passive
        direction, so an AGGRESSIVE rounding of the raw peg price can never end
        up a fraction of a tick through a cap, a band or the Rule 201 floor.

        A bound is reported in `clamps` when it constrains either the raw peg
        price (the caller's economic intent was cut) or the rounded price (the
        bound is what produced the number actually submitted).
        """
        if not bounds:
            return unbounded, None, ()

        aligned = tuple(
            (name, _round_to_increment(value, increment, RoundDirection.PASSIVE, side))
            for name, value in bounds
        )
        if side is Side.BUY:
            tightest = min(value for _, value in aligned)
            priced = min(unbounded, tightest)
            clamped = {
                name
                for (name, raw_value), (_, aligned_value) in zip(bounds, aligned)
                if raw_value < raw or aligned_value < unbounded
            }
        else:
            tightest = max(value for _, value in aligned)
            priced = max(unbounded, tightest)
            clamped = {
                name
                for (name, raw_value), (_, aligned_value) in zip(bounds, aligned)
                if raw_value > raw or aligned_value > unbounded
            }

        at_bound = {name for name, value in aligned if value == tightest}
        # Fallback keeps a bound added later, but not listed in the precedence
        # tuple, from raising instead of pricing.
        binding = next(
            (name for name in CONSTRAINT_PRECEDENCE if name in at_bound), sorted(at_bound)[0]
        )
        ordered = tuple(name for name in CONSTRAINT_PRECEDENCE if name in clamped)
        return priced, (binding if ordered else None), ordered

    @staticmethod
    def _suspend(
        order: PegOrder,
        nbbo: NBBOQuote,
        reason: SuspendReason,
        message: str,
        reference: Optional[Decimal] = None,
        calculated: Optional[Decimal] = None,
        tick: Optional[Decimal] = None,
        increment: Optional[Decimal] = None,
    ) -> PegOrderReport:
        notes = f"PEG SUSPENDED [{order.order_id}] {reason.value}: {message}"
        logger.warning(notes)
        return PegOrderReport(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            peg_type=order.peg_type,
            status=PegStatus.SUSPENDED,
            reference_price=reference,
            calculated_price=calculated,
            effective_limit_price=None,
            tick_size=tick,
            price_increment=increment,
            suspend_reason=reason,
            audit_notes=notes,
        )
