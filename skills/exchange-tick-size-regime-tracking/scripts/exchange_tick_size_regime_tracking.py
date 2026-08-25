"""Exchange tick size regime tracking, price alignment, and order tick compliance auditing.

Scope and sourcing (verified 2026-08-24):

* **US NMS stocks — SEC Rule 612 (17 CFR 242.612).** Rule 612(b) prohibits a national
  securities exchange, national securities association, ATS, vendor, or broker-dealer
  from *displaying, ranking, or accepting* a bid or offer, an order, or an indication
  of interest priced in an increment finer than ``$0.01`` at or above ``$1.00``, or
  finer than ``$0.0001`` below ``$1.00``. It does **not** govern execution prices:
  sub-penny executions arising from price improvement or midpoint matching remain
  permissible. The 2024 amendments (Release 34-101070, effective 2024-12-09) add a
  ``$0.005`` increment for stocks whose Time Weighted Average Quoted Spread (TWAQS)
  over an Evaluation Period is ``<= $0.015``; that increment is *assigned per symbol
  semiannually by the primary listing exchange* and cannot be derived from price.
  Compliance with the amended increment is deferred: SEC exemptive relief of
  2026-06-11 extended it to the first business day of November 2027. Until then the
  operative US regime is the ``$0.01`` / ``$0.0001`` pair, which is what
  ``US_EQUITIES`` returns unless ``tick_constrained=True`` is passed explicitly.

* **EU shares, depositary receipts and ETFs — MiFID II RTS 11 (Commission Delegated
  Regulation (EU) 2017/588).** The tick size is a *two-dimensional* function of the
  price of the submitted order and the instrument's liquidity band, derived from the
  average daily number of transactions (ADNT) published by ESMA/NCAs. The Annex table
  is 19 price ranges x 6 liquidity bands and is reproduced verbatim in
  ``RTS11_TICK_TABLE``. A price alone is *not* sufficient to determine an RTS 11 tick,
  so ``EU_RTS11`` (alias ``EU_XETRA``) requires ``liquidity_band``.

* **DFM (Dubai Financial Market) — Circular 02/2026, effective 2026-04-06.** Five AED
  price bands covering listed equities, ETFs and REITs.

The regulatory tables in this module are **minimum** increments. RTS 11 requires ticks
"equal to or greater than" the Annex value and venues may publish coarser ticks, so a
venue's own reference data always wins: pass it as ``venue_assigned_tick``.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from decimal import Decimal, DivisionByZero, InvalidOperation, Overflow
from enum import Enum
from typing import Dict, List, Mapping, Optional, Sequence, Tuple, Union

logger = logging.getLogger(__name__)

#: Any numeric form accepted for prices and tick sizes. ``str`` and ``Decimal`` are
#: exact; ``float`` is interpreted through its shortest round-tripping repr
#: (``0.1 + 0.2`` therefore reads as ``0.30000000000000004``, not ``0.3``).
PriceInput = Union[Decimal, float, int, str]

STATUS_COMPLIANT = "TICK_COMPLIANT"
STATUS_ALIGNED = "OFF_TICK_ALIGNED"
STATUS_REJECTED = "OFF_TICK_REJECTED"

#: Sentinel band key used by venues whose tick table does not depend on liquidity.
_BAND_INDEPENDENT = 0

_MAX_ALIGNMENT_PASSES = 4


class TickRegimeError(ValueError):
    """Raised for any invalid tick-regime input or unsatisfiable alignment."""


class UnknownVenueError(TickRegimeError):
    """Raised when a venue has no registered tick regime.

    Never fall back to a default tick here: an unmapped venue silently priced at
    ``$0.01`` produces off-tick orders on every venue with a finer or coarser step.
    """


class LiquidityBandRequiredError(TickRegimeError):
    """Raised when an RTS 11 venue is queried without a liquidity band."""


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class TickRoundingPolicy(str, Enum):
    """How an off-tick price is moved onto a valid tick step.

    ``NEAREST``     round half up to the closest valid step; ignores side.
    ``PASSIVE``     never more aggressive than proposed: BUY rounds down, SELL rounds
                    up. Use this for live limit orders — it cannot breach the client's
                    limit price or turn a resting order into a spread-crossing taker.
    ``AGGRESSIVE``  BUY rounds up, SELL rounds down. Only for deliberate marketable
                    repricing, and it *does* pay more (or receive less) than proposed.
    """

    NEAREST = "NEAREST"
    PASSIVE = "PASSIVE"
    AGGRESSIVE = "AGGRESSIVE"


def _to_decimal(value: PriceInput, label: str) -> Decimal:
    """Convert a supported numeric input to an exact, finite ``Decimal``."""
    if isinstance(value, bool):
        raise TickRegimeError(f"{label} must be a number, got bool {value!r}")
    if isinstance(value, Decimal):
        candidate = value
    elif isinstance(value, (int, str, float)):
        try:
            candidate = Decimal(str(value))
        except InvalidOperation as exc:
            raise TickRegimeError(f"{label} is not a valid decimal: {value!r}") from exc
    else:
        raise TickRegimeError(f"{label} must be Decimal, int, float or str, got {type(value).__name__}")

    if not candidate.is_finite():
        raise TickRegimeError(f"{label} must be finite, got {value!r}")
    return candidate


def _to_bound(value: PriceInput, label: str) -> Decimal:
    """Convert an upper price-band bound, accepting positive infinity."""
    if isinstance(value, float) and math.isinf(value) and value > 0:
        return Decimal("Infinity")
    if isinstance(value, Decimal) and value.is_infinite() and value > 0:
        return Decimal("Infinity")
    if isinstance(value, str) and value.strip().lower().lstrip("+") in ("inf", "infinity"):
        return Decimal("Infinity")
    return _to_decimal(value, label)


def _coerce_side(side: Optional[Union[OrderSide, str]]) -> Optional[OrderSide]:
    if side is None:
        return None
    if isinstance(side, OrderSide):
        return side
    try:
        return OrderSide(str(side).upper())
    except ValueError as exc:
        raise TickRegimeError(f"side must be 'BUY' or 'SELL', got {side!r}") from exc


def _coerce_policy(policy: Union[TickRoundingPolicy, str]) -> TickRoundingPolicy:
    if isinstance(policy, TickRoundingPolicy):
        return policy
    try:
        return TickRoundingPolicy(str(policy).upper())
    except ValueError as exc:
        raise TickRegimeError(
            f"rounding policy must be one of {[p.value for p in TickRoundingPolicy]}, got {policy!r}"
        ) from exc


@dataclass
class PriceBandTickRule:
    """One ``[min_price, max_price)`` price band and the tick size that applies in it.

    Bounds and tick are stored as ``Decimal``; ``float`` inputs are converted through
    their shortest repr so ``0.0001`` is exactly ``Decimal('0.0001')``.
    """

    min_price: PriceInput
    max_price: PriceInput
    tick_size: PriceInput

    def __post_init__(self) -> None:
        self.min_price = _to_decimal(self.min_price, "min_price")
        self.max_price = _to_bound(self.max_price, "max_price")
        self.tick_size = _to_decimal(self.tick_size, "tick_size")

        if self.min_price < 0:
            raise TickRegimeError(f"min_price must be >= 0, got {self.min_price}")
        if self.max_price <= self.min_price:
            raise TickRegimeError(f"max_price ({self.max_price}) must exceed min_price ({self.min_price})")
        if self.tick_size <= 0:
            raise TickRegimeError(f"tick_size must be > 0, got {self.tick_size}")

    def contains(self, price: Decimal) -> bool:
        return self.min_price <= price < self.max_price


@dataclass(frozen=True)
class VenueTickRegime:
    """A venue's tick table plus the provenance needed for a compliance audit trail."""

    venue_id: str
    currency: str
    source: str
    rules_by_band: Mapping[int, Tuple[PriceBandTickRule, ...]]
    requires_liquidity_band: bool = False
    notes: str = ""


@dataclass
class TickRegimeAuditReport:
    """Result of auditing one proposed order price against a venue tick regime.

    ``aligned_price`` / ``active_tick_size`` are floats for backwards compatibility;
    the ``*_decimal`` fields carry the exact values and are what should be forwarded
    to an order gateway.
    """

    venue_id: str
    symbol: str
    proposed_price: float
    active_tick_size: float
    aligned_price: float
    is_on_tick: bool
    status: str
    audit_notes: str
    proposed_price_decimal: Optional[Decimal] = None
    active_tick_size_decimal: Optional[Decimal] = None
    aligned_price_decimal: Optional[Decimal] = None
    liquidity_band: Optional[int] = None
    side: Optional[str] = None
    rounding_policy: str = TickRoundingPolicy.NEAREST.value
    regulatory_source: str = ""
    crossed_price_band: bool = False


# --------------------------------------------------------------------------------------
# MiFID II RTS 11 — Commission Delegated Regulation (EU) 2017/588, Annex.
# Rows are (upper bound exclusive, ticks for liquidity bands 1..6).
# Liquidity bands are defined on the average daily number of transactions (ADNT).
# --------------------------------------------------------------------------------------
RTS11_LIQUIDITY_BANDS: Tuple[Tuple[int, Decimal, Decimal], ...] = (
    (1, Decimal("0"), Decimal("10")),
    (2, Decimal("10"), Decimal("80")),
    (3, Decimal("80"), Decimal("600")),
    (4, Decimal("600"), Decimal("2000")),
    (5, Decimal("2000"), Decimal("9000")),
    (6, Decimal("9000"), Decimal("Infinity")),
)

RTS11_TICK_TABLE: Tuple[Tuple[str, Tuple[str, str, str, str, str, str]], ...] = (
    ("0.1", ("0.0005", "0.0002", "0.0001", "0.0001", "0.0001", "0.0001")),
    ("0.2", ("0.001", "0.0005", "0.0002", "0.0001", "0.0001", "0.0001")),
    ("0.5", ("0.002", "0.001", "0.0005", "0.0002", "0.0001", "0.0001")),
    ("1", ("0.005", "0.002", "0.001", "0.0005", "0.0002", "0.0001")),
    ("2", ("0.01", "0.005", "0.002", "0.001", "0.0005", "0.0002")),
    ("5", ("0.02", "0.01", "0.005", "0.002", "0.001", "0.0005")),
    ("10", ("0.05", "0.02", "0.01", "0.005", "0.002", "0.001")),
    ("20", ("0.1", "0.05", "0.02", "0.01", "0.005", "0.002")),
    ("50", ("0.2", "0.1", "0.05", "0.02", "0.01", "0.005")),
    ("100", ("0.5", "0.2", "0.1", "0.05", "0.02", "0.01")),
    ("200", ("1", "0.5", "0.2", "0.1", "0.05", "0.02")),
    ("500", ("2", "1", "0.5", "0.2", "0.1", "0.05")),
    ("1000", ("5", "2", "1", "0.5", "0.2", "0.1")),
    ("2000", ("10", "5", "2", "1", "0.5", "0.2")),
    ("5000", ("20", "10", "5", "2", "1", "0.5")),
    ("10000", ("50", "20", "10", "5", "2", "1")),
    ("20000", ("100", "50", "20", "10", "5", "2")),
    ("50000", ("200", "100", "50", "20", "10", "5")),
    ("Infinity", ("500", "200", "100", "50", "20", "10")),
)

#: Amended Rule 612(b)(1)(ii) increment for tick-constrained NMS stocks. Not operative:
#: SEC exemptive relief of 2026-06-11 defers compliance to the first business day of
#: November 2027, and the assignment is made per symbol, not derived from price.
US_TICK_CONSTRAINED_INCREMENT = Decimal("0.005")


def _build_rts11_rules() -> Dict[int, Tuple[PriceBandTickRule, ...]]:
    rules_by_band: Dict[int, Tuple[PriceBandTickRule, ...]] = {}
    for band_index in range(6):
        band_rules: List[PriceBandTickRule] = []
        lower = Decimal("0")
        for upper_str, ticks in RTS11_TICK_TABLE:
            upper = Decimal(upper_str)
            band_rules.append(PriceBandTickRule(lower, upper, Decimal(ticks[band_index])))
            lower = upper
        rules_by_band[band_index + 1] = tuple(band_rules)
    return rules_by_band


def _validate_rules(venue_id: str, band: int, rules: Sequence[PriceBandTickRule]) -> None:
    """Reject gapped, overlapping, unordered or unbounded tick tables.

    A hand-written table with a missing top band is exactly how a venue ends up being
    quoted at a stale increment, so the table is checked at registration rather than
    at order time.
    """
    if not rules:
        raise TickRegimeError(f"{venue_id} band {band}: tick table is empty")
    if rules[0].min_price != 0:
        raise TickRegimeError(f"{venue_id} band {band}: table must start at price 0, starts at {rules[0].min_price}")
    for previous, current in zip(rules, rules[1:]):
        if current.min_price != previous.max_price:
            raise TickRegimeError(
                f"{venue_id} band {band}: price bands must be contiguous and ordered; "
                f"[{previous.min_price}, {previous.max_price}) is followed by [{current.min_price}, ...)"
            )
    if rules[-1].max_price != Decimal("Infinity"):
        raise TickRegimeError(
            f"{venue_id} band {band}: table must be unbounded above; highest band ends at {rules[-1].max_price}"
        )


class ExchangeTickSizeRegimeEngine:
    """Tracks venue tick regimes, aligns prices to valid steps, and audits compliance.

    The engine is stateless per call and holds no mutable order state; a single
    instance can be shared across strategies. All regulatory tables are minimums —
    supply ``venue_assigned_tick`` from venue reference data when it is available.
    """

    def __init__(self) -> None:
        self.regimes: Dict[str, VenueTickRegime] = {}
        self._aliases: Dict[str, str] = {}
        self._register_default_regimes()

    # ------------------------------------------------------------------ registry ----
    def _register_default_regimes(self) -> None:
        self.register_venue(
            VenueTickRegime(
                venue_id="US_EQUITIES",
                currency="USD",
                source="SEC Rule 612, 17 CFR 242.612(b) (Reg NMS minimum pricing increment)",
                rules_by_band={
                    _BAND_INDEPENDENT: (
                        PriceBandTickRule("0", "1", "0.0001"),
                        PriceBandTickRule("1", "Infinity", "0.01"),
                    )
                },
                notes=(
                    "Applies to displaying, ranking or accepting quotations, orders and "
                    "indications of interest in NMS stocks -- not to execution prices. "
                    "The amended $0.005 increment for tick-constrained stocks is not "
                    "operative (compliance deferred to the first business day of "
                    "November 2027); pass tick_constrained=True to model it."
                ),
            )
        )

        self.register_venue(
            VenueTickRegime(
                venue_id="EU_RTS11",
                currency="EUR",
                source="MiFID II RTS 11, Commission Delegated Regulation (EU) 2017/588, Annex",
                rules_by_band=_build_rts11_rules(),
                requires_liquidity_band=True,
                notes=(
                    "Liquidity band 1..6 is derived from the average daily number of "
                    "transactions published by ESMA/the relevant NCA and applies from "
                    "the annual application date -- it is not derivable from price. "
                    "ETFs whose underlyings are exclusively in-scope shares use band 6."
                ),
            ),
            aliases=("EU_XETRA",),
        )

        self.register_venue(
            VenueTickRegime(
                venue_id="DFM_DUBAI",
                currency="AED",
                source="DFM Circular 02/2026, 'Revision to Tick Size Structure - DFM Listed Securities', effective 2026-04-06",
                rules_by_band={
                    _BAND_INDEPENDENT: (
                        PriceBandTickRule("0", "1", "0.001"),
                        PriceBandTickRule("1", "10", "0.01"),
                        PriceBandTickRule("10", "50", "0.02"),
                        PriceBandTickRule("50", "100", "0.05"),
                        PriceBandTickRule("100", "Infinity", "0.10"),
                    )
                },
                notes="Applies to listed equities, ETFs and REITs.",
            )
        )

    def register_venue(self, regime: VenueTickRegime, aliases: Sequence[str] = ()) -> None:
        """Register (or replace) a venue tick regime after validating its tables."""
        if not isinstance(regime, VenueTickRegime):
            raise TickRegimeError(f"regime must be a VenueTickRegime, got {type(regime).__name__}")
        if not regime.rules_by_band:
            raise TickRegimeError(f"{regime.venue_id}: no tick tables supplied")
        if regime.requires_liquidity_band and _BAND_INDEPENDENT in regime.rules_by_band:
            raise TickRegimeError(f"{regime.venue_id}: band-dependent regime cannot define a band-independent table")

        for band, rules in regime.rules_by_band.items():
            _validate_rules(regime.venue_id, band, rules)

        venue_key = regime.venue_id.upper()
        self.regimes[venue_key] = regime
        for alias in aliases:
            self._aliases[alias.upper()] = venue_key

    def resolve_venue(self, venue_id: str) -> VenueTickRegime:
        """Return the regime for ``venue_id``; raise rather than defaulting."""
        if not isinstance(venue_id, str) or not venue_id.strip():
            raise TickRegimeError(f"venue_id must be a non-empty string, got {venue_id!r}")
        key = venue_id.strip().upper()
        key = self._aliases.get(key, key)
        try:
            return self.regimes[key]
        except KeyError as exc:
            raise UnknownVenueError(
                f"No tick regime registered for venue '{venue_id}'. "
                f"Known venues: {sorted(self.regimes)}. Register one with register_venue()."
            ) from exc

    # ------------------------------------------------------------------- lookups ----
    def get_active_tick_size_decimal(
        self,
        venue_id: str,
        price: PriceInput,
        *,
        liquidity_band: Optional[int] = None,
        tick_constrained: bool = False,
    ) -> Decimal:
        """Return the exact minimum tick size for ``price`` on ``venue_id``.

        ``liquidity_band`` is mandatory for RTS 11 venues (1 = least liquid,
        6 = most liquid). ``tick_constrained`` models the not-yet-operative amended
        Rule 612 ``$0.005`` increment and is only accepted for ``US_EQUITIES``.
        """
        regime = self.resolve_venue(venue_id)
        d_price = _to_decimal(price, "price")
        if d_price <= 0:
            raise TickRegimeError(f"price must be > 0, got {d_price}")

        if tick_constrained and regime.venue_id != "US_EQUITIES":
            raise TickRegimeError(
                f"tick_constrained models amended SEC Rule 612 and does not apply to {regime.venue_id}"
            )

        if regime.requires_liquidity_band:
            band = self._validate_band(regime, liquidity_band)
        else:
            if liquidity_band is not None:
                raise TickRegimeError(
                    f"{regime.venue_id} does not use liquidity bands; drop liquidity_band={liquidity_band!r}"
                )
            band = _BAND_INDEPENDENT

        for rule in regime.rules_by_band[band]:
            if rule.contains(d_price):
                if tick_constrained and d_price >= 1:
                    return US_TICK_CONSTRAINED_INCREMENT
                return rule.tick_size

        # _validate_rules guarantees a contiguous table from 0 to infinity.
        raise TickRegimeError(f"{regime.venue_id}: no price band covers {d_price} (band {band})")

    def get_active_tick_size(
        self,
        venue_id: str,
        price: PriceInput,
        *,
        liquidity_band: Optional[int] = None,
        tick_constrained: bool = False,
    ) -> float:
        """``get_active_tick_size_decimal`` as a float, for display and legacy callers."""
        return float(
            self.get_active_tick_size_decimal(
                venue_id, price, liquidity_band=liquidity_band, tick_constrained=tick_constrained
            )
        )

    @staticmethod
    def _validate_band(regime: VenueTickRegime, liquidity_band: Optional[int]) -> int:
        if liquidity_band is None:
            raise LiquidityBandRequiredError(
                f"{regime.venue_id} tick size depends on the RTS 11 liquidity band (ADNT), not price alone. "
                "Supply liquidity_band=1..6 from the ESMA/NCA annual calculation."
            )
        if isinstance(liquidity_band, bool) or not isinstance(liquidity_band, int):
            raise TickRegimeError(f"liquidity_band must be an int 1..6, got {liquidity_band!r}")
        if liquidity_band not in regime.rules_by_band:
            raise TickRegimeError(
                f"{regime.venue_id}: unknown liquidity band {liquidity_band}; "
                f"valid bands are {sorted(regime.rules_by_band)}"
            )
        return liquidity_band

    @staticmethod
    def liquidity_band_for_adnt(adnt: PriceInput) -> int:
        """Map an average daily number of transactions to an RTS 11 liquidity band."""
        d_adnt = _to_decimal(adnt, "adnt")
        if d_adnt < 0:
            raise TickRegimeError(f"adnt must be >= 0, got {d_adnt}")
        for band, lower, upper in RTS11_LIQUIDITY_BANDS:
            if lower <= d_adnt < upper:
                return band
        raise TickRegimeError(f"no RTS 11 liquidity band covers ADNT {d_adnt}")

    # ----------------------------------------------------------------- alignment ----
    def align_price_to_tick_decimal(
        self,
        price: PriceInput,
        tick_size: PriceInput,
        *,
        side: Optional[Union[OrderSide, str]] = None,
        policy: Union[TickRoundingPolicy, str] = TickRoundingPolicy.NEAREST,
    ) -> Decimal:
        """Move ``price`` onto a valid multiple of ``tick_size`` under ``policy``.

        ``PASSIVE`` and ``AGGRESSIVE`` require ``side``: without it there is no
        direction to be passive or aggressive in, and guessing would silently reprice
        one side of the book the wrong way.
        """
        d_price = _to_decimal(price, "price")
        d_tick = _to_decimal(tick_size, "tick_size")
        resolved_policy = _coerce_policy(policy)
        resolved_side = _coerce_side(side)

        if d_tick <= 0:
            raise TickRegimeError(f"tick_size must be > 0, got {d_tick}")
        if d_price <= 0:
            raise TickRegimeError(f"price must be > 0, got {d_price}")
        if resolved_policy is not TickRoundingPolicy.NEAREST and resolved_side is None:
            raise TickRegimeError(f"{resolved_policy.value} rounding requires side='BUY' or 'SELL'")

        try:
            steps, remainder = divmod(d_price, d_tick)
        except (InvalidOperation, DivisionByZero, Overflow) as exc:
            raise TickRegimeError(f"cannot align price {d_price} to tick {d_tick}: {exc}") from exc

        if remainder == 0:
            return self._quantize(d_price, d_tick)

        lower = steps * d_tick
        upper = lower + d_tick

        if resolved_policy is TickRoundingPolicy.NEAREST:
            aligned = upper if remainder * 2 >= d_tick else lower
        elif resolved_policy is TickRoundingPolicy.PASSIVE:
            aligned = lower if resolved_side is OrderSide.BUY else upper
        else:  # AGGRESSIVE
            aligned = upper if resolved_side is OrderSide.BUY else lower

        if aligned <= 0:
            raise TickRegimeError(
                f"aligning {d_price} to tick {d_tick} under {resolved_policy.value}/{resolved_side} "
                "produces a non-positive price"
            )
        return self._quantize(aligned, d_tick)

    def align_price_to_tick(
        self,
        price: PriceInput,
        tick_size: PriceInput,
        *,
        side: Optional[Union[OrderSide, str]] = None,
        policy: Union[TickRoundingPolicy, str] = TickRoundingPolicy.NEAREST,
    ) -> float:
        """``align_price_to_tick_decimal`` as a float, for display and legacy callers."""
        return float(self.align_price_to_tick_decimal(price, tick_size, side=side, policy=policy))

    @staticmethod
    def _quantize(value: Decimal, tick: Decimal) -> Decimal:
        """Present the aligned price at the tick's own decimal precision.

        Integer ticks (RTS 11 bands above EUR 100) quantize to whole units rather
        than to the tick's exponent, so an audit log reads ``150`` and not ``15E+1``.
        """
        target = tick if tick.as_tuple().exponent < 0 else Decimal(1)
        try:
            return value.quantize(target)
        except (InvalidOperation, Overflow):
            # Value already an exact multiple of the tick; leave it untouched.
            return value

    # --------------------------------------------------------------------- audit ----
    def audit_order_tick_compliance(
        self,
        venue_id: str,
        symbol: str,
        proposed_price: PriceInput,
        auto_align: bool = True,
        *,
        side: Optional[Union[OrderSide, str]] = None,
        policy: Union[TickRoundingPolicy, str] = TickRoundingPolicy.NEAREST,
        liquidity_band: Optional[int] = None,
        tick_constrained: bool = False,
        venue_assigned_tick: Optional[PriceInput] = None,
    ) -> TickRegimeAuditReport:
        """Audit a proposed order price against the venue's active tick regime.

        ``venue_assigned_tick`` overrides the regulatory table with the venue's own
        published tick for the instrument. It may be coarser than the regulatory
        minimum (RTS 11 ticks are floors, and venues may widen them) but never finer,
        because a finer step would breach the regime it claims to satisfy.
        """
        regime = self.resolve_venue(venue_id)
        d_proposed = _to_decimal(proposed_price, "proposed_price")
        if d_proposed <= 0:
            raise TickRegimeError(f"proposed_price must be > 0, got {d_proposed}")
        resolved_policy = _coerce_policy(policy)
        resolved_side = _coerce_side(side)

        regulatory_tick = self.get_active_tick_size_decimal(
            regime.venue_id, d_proposed, liquidity_band=liquidity_band, tick_constrained=tick_constrained
        )

        if venue_assigned_tick is not None:
            effective_tick = _to_decimal(venue_assigned_tick, "venue_assigned_tick")
            if effective_tick <= 0:
                raise TickRegimeError(f"venue_assigned_tick must be > 0, got {effective_tick}")
            if effective_tick < regulatory_tick:
                raise TickRegimeError(
                    f"venue_assigned_tick {effective_tick} is finer than the {regime.venue_id} regulatory "
                    f"minimum {regulatory_tick} at price {d_proposed} ({regime.source})"
                )
        else:
            effective_tick = regulatory_tick

        aligned, effective_tick, crossed_band = self._align_within_regime(
            regime=regime,
            price=d_proposed,
            tick=effective_tick,
            fixed_tick=venue_assigned_tick is not None,
            side=resolved_side,
            policy=resolved_policy,
            liquidity_band=liquidity_band,
            tick_constrained=tick_constrained,
        )

        is_on_tick = aligned == d_proposed
        band_note = (
            f" Alignment crossed into a different price band; final tick {effective_tick} applies at {aligned}."
            if crossed_band
            else ""
        )

        if is_on_tick:
            status = STATUS_COMPLIANT
            notes = (
                f"TICK COMPLIANT [{symbol} @ {regime.venue_id}]: price {d_proposed} is an exact multiple "
                f"of tick {effective_tick} ({regime.source})."
            )
            logger.info(notes)
        elif auto_align:
            status = STATUS_ALIGNED
            notes = (
                f"OFF-TICK ALIGNED [{symbol} @ {regime.venue_id}]: proposed {d_proposed} aligned to {aligned} "
                f"(tick={effective_tick}, policy={resolved_policy.value}, "
                f"side={resolved_side.value if resolved_side else 'N/A'}).{band_note}"
            )
            logger.warning(notes)
        else:
            status = STATUS_REJECTED
            notes = (
                f"OFF-TICK REJECTED [{symbol} @ {regime.venue_id}]: proposed {d_proposed} is not a multiple of "
                f"tick {effective_tick}; nearest valid step is {aligned}. Order not sent."
            )
            logger.error(notes)

        return TickRegimeAuditReport(
            venue_id=regime.venue_id,
            symbol=symbol,
            proposed_price=float(d_proposed),
            active_tick_size=float(effective_tick),
            aligned_price=float(aligned),
            is_on_tick=is_on_tick,
            status=status,
            audit_notes=notes,
            proposed_price_decimal=d_proposed,
            active_tick_size_decimal=effective_tick,
            aligned_price_decimal=aligned,
            liquidity_band=liquidity_band,
            side=resolved_side.value if resolved_side else None,
            rounding_policy=resolved_policy.value,
            regulatory_source=regime.source,
            crossed_price_band=crossed_band,
        )

    def _align_within_regime(
        self,
        *,
        regime: VenueTickRegime,
        price: Decimal,
        tick: Decimal,
        fixed_tick: bool,
        side: Optional[OrderSide],
        policy: TickRoundingPolicy,
        liquidity_band: Optional[int],
        tick_constrained: bool,
    ) -> Tuple[Decimal, Decimal, bool]:
        """Align, then re-check the band: the aligned price may sit in a coarser band.

        Rounding 0.99999 on US equities lands on 1.0000, where the minimum increment
        is $0.01 rather than $0.0001. The tick reported to the caller must be the one
        that governs the price actually being sent, so alignment is re-run until the
        price and its own band's tick agree.
        """
        current_tick = tick
        crossed = False

        for _ in range(_MAX_ALIGNMENT_PASSES):
            aligned = self.align_price_to_tick_decimal(price, current_tick, side=side, policy=policy)

            regulatory_at_aligned = self.get_active_tick_size_decimal(
                regime.venue_id, aligned, liquidity_band=liquidity_band, tick_constrained=tick_constrained
            )

            if fixed_tick:
                if regulatory_at_aligned > current_tick:
                    raise TickRegimeError(
                        f"{regime.venue_id}: aligning {price} with venue_assigned_tick {current_tick} produced "
                        f"{aligned}, which falls in a band requiring a minimum tick of {regulatory_at_aligned}"
                    )
                regulatory_at_price = self.get_active_tick_size_decimal(
                    regime.venue_id, price, liquidity_band=liquidity_band, tick_constrained=tick_constrained
                )
                return aligned, current_tick, regulatory_at_aligned != regulatory_at_price

            tick_at_aligned = regulatory_at_aligned
            if tick_at_aligned == current_tick:
                return aligned, current_tick, crossed

            crossed = True
            if divmod(aligned, tick_at_aligned)[1] == 0:
                # Already valid under the band it landed in (band boundaries are exact
                # multiples of both neighbouring ticks in every table registered here).
                return aligned, tick_at_aligned, crossed
            current_tick = tick_at_aligned

        raise TickRegimeError(
            f"{regime.venue_id}: price {price} could not be aligned to a stable tick within "
            f"{_MAX_ALIGNMENT_PASSES} passes; the venue tick table oscillates across a price band boundary"
        )

