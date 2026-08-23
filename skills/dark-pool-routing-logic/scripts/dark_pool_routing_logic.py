"""Dark pool / ATS child-order routing with toxicity filtering and anti-pinging MinQty.

Scores non-displayed venues by historical fill rate discounted by measured
post-trade adverse selection (markout toxicity, in bps), drops venues above a
toxicity ceiling, allocates a parent block proportionally to the resulting
scores, and attaches a Minimum Quantity (FIX ``MinQty`` tag 110) instruction to
every child directive so a counterparty cannot probe the resting size with a
one-share ping.

FIX mapping of the emitted directives (FIX 4.4 / 5.0 SP2):
- ``min_qty_instruction`` -> ``MinQty(110)``, "Minimum quantity of an order to
  be executed". On the ATSs that publish their handling the constraint binds on
  *each* execution, and a residual below MinQty is either cancelled or turned
  into an AON (IEX, "Minimum Execution Size with All or None Functionality",
  2014). A MinQty above the order quantity therefore describes an order that
  can never trade -- the engine holds ``min_qty_instruction <=
  allocated_quantity`` as a hard invariant.
- Midpoint pricing is the caller's responsibility: ``ExecInst(18)='M'`` or
  ``PegPriceType(1094)=2``, "Mid-price peg (midprice of inside quote)". This
  module emits sizes and MinQty only -- it does not price orders.

Evidence and limitations (deliberate, documented):
- Access restrictions in dark venues causally reduce information leakage and
  adverse selection, at the cost of execution probability (Brugler &
  Comerton-Forde, "Differential access to dark markets and execution outcomes",
  Journal of Financial Economics, 2025 -- Australian venue setting).
- ``toxicity_score_bps`` must be measured from the router's own post-trade
  markouts. Venue-published toxicity / liquidity-profiling scores have been the
  subject of SEC enforcement for misrepresentation (SEC Press Release 2016-16,
  Barclays LX and Credit Suisse Crossfinder, 31 Jan 2016).
- Every numeric default here (5.0 bps ceiling, 0.05 fill-rate floor, 50.0 bps
  toxicity decay, 20% child MinQty fraction, 200-share MinQty floor) is an
  engineering default, not a regulatory or venue-imposed constant. Calibrate
  per universe and per venue before automating.
- There is no venue capacity / ADV model here: the engine splits by score, not
  by how much size a pool can absorb, and it has no view of the lit book.
- The engine does not check waiver eligibility. Under MiFIR as amended by
  Regulation (EU) 2024/791 a name suspended under the single volume cap (7%,
  reference-price waiver only) cannot trade dark under that waiver; run that
  gate upstream (see the ``esma-double-volume-cap-mechanism`` skill).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# --- Engineering defaults (tunable; none of these are regulatory constants) ---

# Post-trade markout ceiling above which a venue is dropped entirely.
DEFAULT_MAX_TOXICITY_BPS = 5.0
# Absolute MinQty floor applied to every dark child order, so a child cannot be
# pinged out one lot at a time.
DEFAULT_MIN_QTY = 200
# Venues filling less often than this are dropped as opportunity-cost sinks.
DEFAULT_MIN_FILL_RATE = 0.05
# Toxicity is discounted linearly over this range: a venue at
# `toxicity_decay_bps` scores zero. Purely a shaping constant.
DEFAULT_TOXICITY_DECAY_BPS = 50.0
# MinQty is also scaled to the child size, so large children are not probed by
# small fills.
DEFAULT_MIN_QTY_CHILD_FRACTION = 0.20

VALID_SIDES = frozenset({"BUY", "SELL"})


class DarkPoolRoutingError(ValueError):
    """Raised on an invalid venue profile, engine configuration, or parent order."""


def _require_finite(value: float, name: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise DarkPoolRoutingError(f"{name} must be finite, got {value!r}.")
    return number


@dataclass
class DarkPoolVenueProfile:
    """A single dark pool / ATS candidate.

    Args:
        venue_id: Stable internal identifier for the venue.
        venue_name: Human-readable name, used in logs and exclusion reasons.
        historical_fill_rate: Realised fill probability in [0.0, 1.0], measured
            by the router (filled qty / qty sent), not quoted by the venue.
        toxicity_score_bps: Post-trade adverse-selection markout in bps, signed
            so that **higher is worse**. Negative values (favourable markout)
            are accepted but treated as 0.0 for scoring, so a favourable markout
            cannot inflate a venue's weight without bound.
        min_qty_threshold: The venue's own minimum executable quantity in
            shares, combined with the engine's ``default_min_qty`` floor.
        is_active: Set False to suspend a venue without deregistering it.
    """

    venue_id: str
    venue_name: str
    historical_fill_rate: float
    toxicity_score_bps: float
    min_qty_threshold: int
    is_active: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.venue_id, str) or not self.venue_id.strip():
            raise DarkPoolRoutingError("venue_id must be a non-empty string.")
        if not isinstance(self.venue_name, str) or not self.venue_name.strip():
            raise DarkPoolRoutingError(
                f"[{self.venue_id}] venue_name must be a non-empty string."
            )

        self.historical_fill_rate = _require_finite(
            self.historical_fill_rate, f"[{self.venue_id}] historical_fill_rate"
        )
        if not 0.0 <= self.historical_fill_rate <= 1.0:
            raise DarkPoolRoutingError(
                f"[{self.venue_id}] historical_fill_rate must be in [0.0, 1.0], got "
                f"{self.historical_fill_rate}. A rate above 1.0 is a measurement error "
                "and would over-weight the venue."
            )

        self.toxicity_score_bps = _require_finite(
            self.toxicity_score_bps, f"[{self.venue_id}] toxicity_score_bps"
        )

        if isinstance(self.min_qty_threshold, bool) or not isinstance(self.min_qty_threshold, int):
            raise DarkPoolRoutingError(
                f"[{self.venue_id}] min_qty_threshold must be an int (shares), got "
                f"{type(self.min_qty_threshold).__name__}."
            )
        if self.min_qty_threshold < 0:
            raise DarkPoolRoutingError(
                f"[{self.venue_id}] min_qty_threshold must be >= 0, got {self.min_qty_threshold}."
            )


@dataclass
class DarkChildOrderDirective:
    """One dark child order: size plus the MinQty instruction to attach to it.

    Invariant: ``min_qty_instruction <= allocated_quantity``, because a MinQty
    above the order quantity can never be satisfied.

    ``allocation_weight_pct`` is the *realised* share of the parent
    (``allocated_quantity / parent``), not the pre-rounding score weight, so the
    percentages sum to 100 unless a concentration cap left a residual.
    """

    venue_id: str
    venue_name: str
    allocated_quantity: int
    min_qty_instruction: int
    allocation_weight_pct: float
    venue_score: float


@dataclass
class DarkPoolRoutingReport:
    """Audit record of one parent-order routing decision.

    ``unallocated_quantity`` is the parent residual this engine did **not**
    place in the dark; it is the caller's to work on the lit book, re-slice
    later, or hold. ``unallocated_reason`` records why it exists.
    """

    symbol: str
    side: str
    total_parent_quantity: int
    allocated_quantity: int
    unallocated_quantity: int
    child_directives: List[DarkChildOrderDirective]
    excluded_venues: List[str]
    unallocated_reason: str = ""


class DarkPoolRoutingEngine:
    """Routes parent block orders across dark pools / ATS venues.

    Filters venues by measured toxicity and fill rate, allocates the parent
    quantity proportionally to a fill-rate x toxicity-discount score, and
    attaches an anti-pinging MinQty instruction to every child order.

    All thresholds are engineering defaults, not regulatory or venue constants.
    """

    def __init__(
        self,
        max_acceptable_toxicity_bps: float = DEFAULT_MAX_TOXICITY_BPS,
        default_min_qty: int = DEFAULT_MIN_QTY,
        min_fill_rate: float = DEFAULT_MIN_FILL_RATE,
        toxicity_decay_bps: float = DEFAULT_TOXICITY_DECAY_BPS,
        min_qty_child_fraction: float = DEFAULT_MIN_QTY_CHILD_FRACTION,
        max_venue_allocation_pct: Optional[float] = None,
    ) -> None:
        """
        Args:
            max_acceptable_toxicity_bps: Venues with a markout strictly above
                this are excluded.
            default_min_qty: Absolute MinQty floor in shares, applied on top of
                each venue's own ``min_qty_threshold``.
            min_fill_rate: Venues filling below this rate are excluded.
            toxicity_decay_bps: Toxicity at which the score discount reaches
                zero. Must be > 0.
            min_qty_child_fraction: Fraction of the child size also required as
                MinQty, in [0.0, 1.0].
            max_venue_allocation_pct: Optional concentration cap in (0.0, 1.0]:
                the maximum share of the parent any single venue may receive.
                ``None`` (default) means no cap -- set it explicitly if
                over-allocating one pool is a concern. With a cap set, the
                capped remainder is reported as unallocated rather than being
                pushed onto another venue.
        """
        self.max_acceptable_toxicity_bps = _require_finite(
            max_acceptable_toxicity_bps, "max_acceptable_toxicity_bps"
        )
        if (
            isinstance(default_min_qty, bool)
            or not isinstance(default_min_qty, int)
            or default_min_qty < 0
        ):
            raise DarkPoolRoutingError(
                f"default_min_qty must be an int >= 0, got {default_min_qty!r}."
            )
        self.default_min_qty = default_min_qty

        self.min_fill_rate = _require_finite(min_fill_rate, "min_fill_rate")
        if not 0.0 <= self.min_fill_rate <= 1.0:
            raise DarkPoolRoutingError(f"min_fill_rate must be in [0.0, 1.0], got {min_fill_rate}.")

        self.toxicity_decay_bps = _require_finite(toxicity_decay_bps, "toxicity_decay_bps")
        if self.toxicity_decay_bps <= 0.0:
            raise DarkPoolRoutingError(f"toxicity_decay_bps must be > 0, got {toxicity_decay_bps}.")

        self.min_qty_child_fraction = _require_finite(
            min_qty_child_fraction, "min_qty_child_fraction"
        )
        if not 0.0 <= self.min_qty_child_fraction <= 1.0:
            raise DarkPoolRoutingError(
                f"min_qty_child_fraction must be in [0.0, 1.0], got {min_qty_child_fraction}."
            )

        if max_venue_allocation_pct is not None:
            max_venue_allocation_pct = _require_finite(
                max_venue_allocation_pct, "max_venue_allocation_pct"
            )
            if not 0.0 < max_venue_allocation_pct <= 1.0:
                raise DarkPoolRoutingError(
                    "max_venue_allocation_pct must be in (0.0, 1.0], got "
                    f"{max_venue_allocation_pct}."
                )
        self.max_venue_allocation_pct = max_venue_allocation_pct

        self.venues: Dict[str, DarkPoolVenueProfile] = {}

    # --- venue registry -------------------------------------------------

    def register_venue(self, venue: DarkPoolVenueProfile) -> None:
        """Registers (or replaces) a venue profile. Replacement is logged."""
        if not isinstance(venue, DarkPoolVenueProfile):
            raise DarkPoolRoutingError(
                f"register_venue expects a DarkPoolVenueProfile, got {type(venue).__name__}."
            )
        if venue.venue_id in self.venues:
            logger.warning(
                "DARK POOL VENUE REPLACED [%s]: an existing profile for this venue_id was "
                "overwritten; verify this is a metrics refresh, not a duplicate registration.",
                venue.venue_id,
            )
        self.venues[venue.venue_id] = venue

    # --- scoring --------------------------------------------------------

    def _venue_score(self, venue: DarkPoolVenueProfile) -> float:
        """Score = fill rate x linear toxicity discount, in [0.0, 1.0].

        A favourable (negative) markout is floored at 0.0 toxicity so it cannot
        push the discount above 1.0 and dominate the allocation.
        """
        effective_toxicity = max(0.0, venue.toxicity_score_bps)
        toxicity_discount = max(0.0, 1.0 - (effective_toxicity / self.toxicity_decay_bps))
        return venue.historical_fill_rate * toxicity_discount

    def _effective_min_qty(self, venue: DarkPoolVenueProfile) -> int:
        """Smallest child size this venue can legitimately be sent.

        Floored at 1 share: with both minimums configured to 0 a venue would
        otherwise qualify for a zero-quantity child order.
        """
        return max(1, venue.min_qty_threshold, self.default_min_qty)

    def _min_qty_instruction(self, venue: DarkPoolVenueProfile, allocated_quantity: int) -> int:
        """MinQty for a child order, never exceeding the child quantity itself."""
        scaled = math.ceil(allocated_quantity * self.min_qty_child_fraction)
        return min(max(self._effective_min_qty(venue), scaled), allocated_quantity)

    # --- routing --------------------------------------------------------

    def route_parent_order(
        self,
        symbol: str,
        side: str,
        total_quantity: int,
        custom_max_toxicity_bps: Optional[float] = None,
    ) -> DarkPoolRoutingReport:
        """Allocates a parent block across eligible dark venues.

        Args:
            symbol: Instrument identifier, for the audit record.
            side: ``'BUY'`` or ``'SELL'`` (case-insensitive).
            total_quantity: Parent quantity to place in the dark, in shares.
                Cap dark participation upstream -- this engine places the whole
                quantity it is given.
            custom_max_toxicity_bps: Per-order override of the toxicity ceiling.

        Returns:
            A ``DarkPoolRoutingReport``. When no venue can be funded the report
            carries no directives and the full quantity is unallocated with a
            reason -- callers must handle that path rather than assuming a fill.

        Raises:
            DarkPoolRoutingError: On an invalid symbol, side, quantity, or
                toxicity override.
        """
        if not isinstance(symbol, str) or not symbol.strip():
            raise DarkPoolRoutingError("symbol must be a non-empty string.")
        if not isinstance(side, str) or side.strip().upper() not in VALID_SIDES:
            raise DarkPoolRoutingError(f"side must be one of {sorted(VALID_SIDES)}, got {side!r}.")
        side = side.strip().upper()
        if isinstance(total_quantity, bool) or not isinstance(total_quantity, int):
            raise DarkPoolRoutingError(
                f"total_quantity must be an int (shares), got {type(total_quantity).__name__}."
            )
        if total_quantity <= 0:
            raise DarkPoolRoutingError(f"total_quantity must be > 0, got {total_quantity}.")

        max_tox = self.max_acceptable_toxicity_bps
        if custom_max_toxicity_bps is not None:
            max_tox = _require_finite(custom_max_toxicity_bps, "custom_max_toxicity_bps")

        candidates, excluded_venues = self._screen_venues(max_tox)

        if not candidates:
            logger.warning(
                "DARK POOL ROUTING [%s %s]: no eligible dark venue for %d shares. "
                "Parent left unallocated.",
                symbol, side, total_quantity,
            )
            return DarkPoolRoutingReport(
                symbol=symbol, side=side, total_parent_quantity=total_quantity,
                allocated_quantity=0, unallocated_quantity=total_quantity,
                child_directives=[], excluded_venues=excluded_venues,
                unallocated_reason="NO_ELIGIBLE_VENUE",
            )

        allocations, dropped = self._allocate(candidates, total_quantity)
        excluded_venues.extend(dropped)

        child_directives: List[DarkChildOrderDirective] = []
        allocated_qty = 0
        for venue, score, quantity in allocations:
            child_directives.append(
                DarkChildOrderDirective(
                    venue_id=venue.venue_id,
                    venue_name=venue.venue_name,
                    allocated_quantity=quantity,
                    min_qty_instruction=self._min_qty_instruction(venue, quantity),
                    allocation_weight_pct=round(100.0 * quantity / total_quantity, 2),
                    venue_score=round(score, 4),
                )
            )
            allocated_qty += quantity

        unallocated_qty = total_quantity - allocated_qty
        if not child_directives:
            reason = "PARENT_TOO_SMALL_FOR_VENUE_MIN_QTY"
            logger.warning(
                "DARK POOL ROUTING [%s %s]: parent of %d shares is below the minimum child "
                "size of every eligible venue. Parent left unallocated.",
                symbol, side, total_quantity,
            )
        elif unallocated_qty > 0:
            reason = "VENUE_CONCENTRATION_CAP"
        else:
            reason = ""

        logger.info(
            "DARK POOL ROUTING [%s %s]: Parent=%d -> Allocated=%d across %d dark venues. "
            "Unallocated=%d.",
            symbol, side, total_quantity, allocated_qty, len(child_directives), unallocated_qty,
        )

        return DarkPoolRoutingReport(
            symbol=symbol,
            side=side,
            total_parent_quantity=total_quantity,
            allocated_quantity=allocated_qty,
            unallocated_quantity=unallocated_qty,
            child_directives=child_directives,
            excluded_venues=excluded_venues,
            unallocated_reason=reason,
        )

    # --- internals ------------------------------------------------------

    def _screen_venues(
        self, max_tox: float
    ) -> Tuple[List[Tuple[DarkPoolVenueProfile, float]], List[str]]:
        """Applies the active / toxicity / fill-rate / zero-score screens.

        Returns the surviving (venue, score) pairs sorted by descending score
        (ties broken by ``venue_id`` for determinism), plus exclusion reasons.
        """
        candidates: List[Tuple[DarkPoolVenueProfile, float]] = []
        excluded: List[str] = []

        for venue in self.venues.values():
            if not venue.is_active:
                excluded.append(f"{venue.venue_name} (Inactive)")
                continue

            if venue.toxicity_score_bps > max_tox:
                excluded.append(
                    f"{venue.venue_name} (Toxic: {venue.toxicity_score_bps:.2f}bps "
                    f"> {max_tox:.2f}bps)"
                )
                logger.warning(
                    "DARK POOL EXCLUDED [%s]: toxicity %.2fbps exceeds threshold %.2fbps.",
                    venue.venue_name, venue.toxicity_score_bps, max_tox,
                )
                continue

            if venue.historical_fill_rate < self.min_fill_rate:
                excluded.append(
                    f"{venue.venue_name} (Low Fill Rate: {venue.historical_fill_rate:.2f} "
                    f"< {self.min_fill_rate:.2f})"
                )
                continue

            score = self._venue_score(venue)
            if score <= 0.0:
                # Reachable when toxicity >= toxicity_decay_bps (only if the
                # ceiling was raised that far) or the fill rate is exactly 0.
                excluded.append(f"{venue.venue_name} (Zero Score)")
                continue

            candidates.append((venue, score))

        candidates.sort(key=lambda pair: (-pair[1], pair[0].venue_id))
        return candidates, excluded

    def _allocate(
        self,
        candidates: List[Tuple[DarkPoolVenueProfile, float]],
        total_quantity: int,
    ) -> Tuple[List[Tuple[DarkPoolVenueProfile, float, int]], List[str]]:
        """Score-proportional allocation that respects every venue's minimum.

        A venue whose proportional share falls below its effective minimum is
        dropped and the weights renormalised over the survivors, repeatedly,
        rather than silently shrinking the routed quantity. The integer residual
        left by truncation is handed out by largest fractional remainder to
        venues that are already funded, so the MinQty invariant still holds.
        """
        working = list(candidates)
        dropped: List[str] = []
        exact: List[Tuple[DarkPoolVenueProfile, float, float]] = []

        while working:
            total_score = sum(score for _, score in working)
            if total_score <= 0.0:  # defensive: zero scores are screened upstream
                dropped.extend(f"{venue.venue_name} (Zero Score)" for venue, _ in working)
                return [], dropped

            cap = self.max_venue_allocation_pct
            exact = []
            for venue, score in working:
                weight = score / total_score
                if cap is not None:
                    weight = min(weight, cap)
                exact.append((venue, score, total_quantity * weight))

            underfunded = [
                (venue, score, target)
                for venue, score, target in exact
                if math.floor(target) < self._effective_min_qty(venue)
            ]
            if not underfunded:
                break

            # Drop only the single worst-funded venue and retry: dropping all of
            # them at once discards venues the renormalisation would have funded.
            venue, _, target = min(underfunded, key=lambda item: (item[2], item[0].venue_id))
            dropped.append(
                f"{venue.venue_name} (Allocation {math.floor(target)} < MinQty "
                f"{self._effective_min_qty(venue)})"
            )
            working = [(v, s) for v, s in working if v.venue_id != venue.venue_id]
            exact = []

        if not working:
            return [], dropped

        allocations: List[Tuple[DarkPoolVenueProfile, float, int]] = [
            (venue, score, int(math.floor(target))) for venue, score, target in exact
        ]
        residual = total_quantity - sum(quantity for _, _, quantity in allocations)

        if residual > 0 and self.max_venue_allocation_pct is None:
            # Largest fractional remainder first; ties broken by venue_id.
            order = sorted(
                range(len(allocations)),
                key=lambda i: (
                    -(exact[i][2] - math.floor(exact[i][2])),
                    allocations[i][0].venue_id,
                ),
            )
            for i in order:
                if residual == 0:
                    break
                venue, score, quantity = allocations[i]
                allocations[i] = (venue, score, quantity + 1)
                residual -= 1

        return allocations, dropped
