"""Smart Order Router venue-outage failover engine.

Selects a routing destination among fragmented venues while excluding venues
that are unreachable, quoting stale prices, or behind an open circuit breaker,
and records an auditable trail of every venue it bypassed.

Design notes
------------
1. **Exclusion is evidence-based, not price-based.** A venue is excluded only
   for a stated reason (open breaker, stale quote, invalid quote, no liquidity),
   and every exclusion is returned to the caller in
   ``SORRoutingResult.excluded_venues``. A silent exclusion is indistinguishable
   from a routing bug.

2. **The breaker does not self-clear on a stray success.** Once
   ``CIRCUIT_BROKEN_OUTAGE`` is set, only the elapsed cooldown moves the venue
   to ``RECOVERY_PROBE``, and only a success *in that probe state* closes it.
   A late acknowledgement for an order sent before the outage must not
   resurrect a dead venue.

3. **A recovering venue never wins on price.** ``RECOVERY_PROBE`` venues rank
   strictly last. A venue that just failed is precisely the venue whose quote is
   most likely stale, so letting its price lead the ranking reintroduces the
   failure this engine exists to prevent.

4. **Elapsed time uses ``time.monotonic()``.** Wall clock is kept alongside it
   for the audit log only; an NTP step backwards must not extend or collapse a
   cooldown.

5. **A broad outage is treated as a suspected local fault.** SEC Release
   34-51808 (the Regulation NMS adopting release) requires a trading center
   electing the Rule 611(b)(1) self-help exception to "assess ... whether the
   cause of a problem lies with its own systems". Every venue failing at once is
   far more often a local NIC, DNS, or credential failure than a simultaneous
   market-wide outage. See ``references/standards.md``.

This engine does **not** reconcile in-flight orders at the failed venue, and it
never re-routes residual quantity on its own. Residual is reported; the caller
owns it. See ``SKILL.md``.
"""

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class Config:
    """Legacy Config container for backward compatibility."""
    enabled: bool = True


class Engine:
    """Legacy Engine class for backward compatibility."""
    def __init__(self, config: Config):
        self.config = config

    def execute(self) -> bool:
        return self.config.enabled


class NoEligibleVenueError(RuntimeError):
    """Raised when no venue can accept the order.

    Subclasses ``RuntimeError`` so existing ``except RuntimeError`` handlers keep
    working. Carries the per-venue exclusion reasons so an operator can tell a
    market-wide outage from a local fault.
    """

    def __init__(
        self,
        message: str,
        excluded_venues: Dict[str, str],
        suspected_local_fault: bool,
    ) -> None:
        super().__init__(message)
        self.excluded_venues = excluded_venues
        self.suspected_local_fault = suspected_local_fault


class VenueHealthState(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    CIRCUIT_BROKEN_OUTAGE = "CIRCUIT_BROKEN_OUTAGE"
    RECOVERY_PROBE = "RECOVERY_PROBE"


# Preference among venues that are *equally priced*. Price still leads the sort:
# a DEGRADED venue quoting a better price is a better execution than a HEALTHY
# venue quoting a worse one, and skipping it would be a trade-through taken to
# avoid a venue that has had a single timeout.
_HEALTH_RANK: Dict[str, int] = {
    VenueHealthState.HEALTHY.value: 0,
    VenueHealthState.DEGRADED.value: 1,
    VenueHealthState.RECOVERY_PROBE.value: 2,
    VenueHealthState.CIRCUIT_BROKEN_OUTAGE.value: 3,
}


@dataclass
class TradingVenue:
    """One routable execution venue with its last known health and quote.

    ``quote_monotonic_ts`` must come from ``time.monotonic()`` -- use
    :meth:`SmartOrderRouterFailoverEngine.update_quote`, which stamps it.
    ``None`` means "age unknown": the engine then cannot detect a stale quote
    and says so in ``SORRoutingResult.stale_quote_check_skipped`` (or refuses to
    route the venue, when ``require_quote_timestamp`` is set).
    """
    venue_id: str                         # e.g., 'NASDAQ', 'NYSE', 'BATS', 'EDGX'
    venue_name: str
    state: VenueHealthState = VenueHealthState.HEALTHY
    consecutive_error_count: int = 0
    max_error_threshold: int = 3
    last_error_time: Optional[float] = None        # wall clock, audit only
    last_error_monotonic: Optional[float] = None   # monotonic, used for arithmetic
    circuit_opened_monotonic: Optional[float] = None
    consecutive_trips: int = 0                     # drives cooldown backoff
    bid_price: float = 0.0
    ask_price: float = 0.0
    available_qty: float = 0.0
    latency_ms: float = 5.0
    quote_monotonic_ts: Optional[float] = None

    def __post_init__(self) -> None:
        if not self.venue_id:
            raise ValueError("venue_id must be a non-empty string")
        if self.max_error_threshold < 1:
            raise ValueError(
                f"venue '{self.venue_id}': max_error_threshold must be >= 1, "
                f"got {self.max_error_threshold}"
            )


@dataclass
class SORRoutingResult:
    """Outcome of one routing decision, including everything that was bypassed.

    New fields carry defaults and are appended, so existing positional
    construction keeps working.
    """
    order_id: str
    target_venue_id: str
    routed_quantity: float
    routed_price: float
    is_failover_triggered: bool
    fallback_venues_used: List[str]
    audit_notes: str
    unrouted_quantity: float = 0.0
    price_improvement_forgone: float = 0.0
    excluded_venues: Dict[str, str] = field(default_factory=dict)
    suspected_local_fault: bool = False
    stale_quote_check_skipped: List[str] = field(default_factory=list)


class SmartOrderRouterFailoverEngine:
    """Smart Order Router with venue health tracking, circuit breakers, and failover.

    Thread-safe: venue health is normally reported from FIX/session threads while
    routing runs on a strategy thread, so every read and write of venue state is
    taken under one re-entrant lock.

    Args:
        venues: Initial venues. A duplicate ``venue_id`` is rejected.
        cooldown_seconds: Base time a tripped venue stays in
            ``CIRCUIT_BROKEN_OUTAGE`` before a single ``RECOVERY_PROBE`` is
            admitted. An engineering default, not a regulatory figure.
        max_quote_age_seconds: A quote older than this is ineligible to lead
            price selection. Checked only for venues carrying a timestamp.
        require_quote_timestamp: Production setting. When True, a venue with no
            quote timestamp is excluded rather than assumed fresh.
        backoff_multiplier: Cooldown escalation per consecutive trip.
        max_cooldown_seconds: Cap on the escalated cooldown.
        local_fault_threshold_ratio: Fraction of venues simultaneously tripped at
            which a local fault is suspected instead of a market-wide outage.
    """

    def __init__(
        self,
        venues: Optional[List[TradingVenue]] = None,
        cooldown_seconds: float = 60.0,
        max_quote_age_seconds: float = 1.0,
        require_quote_timestamp: bool = False,
        backoff_multiplier: float = 2.0,
        max_cooldown_seconds: float = 600.0,
        local_fault_threshold_ratio: float = 0.5,
    ) -> None:
        if cooldown_seconds <= 0:
            raise ValueError("cooldown_seconds must be > 0")
        if max_quote_age_seconds <= 0:
            raise ValueError("max_quote_age_seconds must be > 0")
        if backoff_multiplier < 1.0:
            raise ValueError("backoff_multiplier must be >= 1.0")
        if max_cooldown_seconds < cooldown_seconds:
            raise ValueError("max_cooldown_seconds must be >= cooldown_seconds")
        if not 0.0 < local_fault_threshold_ratio <= 1.0:
            raise ValueError("local_fault_threshold_ratio must be in (0.0, 1.0]")

        self.cooldown_seconds = cooldown_seconds
        self.max_quote_age_seconds = max_quote_age_seconds
        self.require_quote_timestamp = require_quote_timestamp
        self.backoff_multiplier = backoff_multiplier
        self.max_cooldown_seconds = max_cooldown_seconds
        self.local_fault_threshold_ratio = local_fault_threshold_ratio

        self._lock = threading.RLock()
        self.venues: Dict[str, TradingVenue] = {}
        if venues:
            for v in venues:
                self.add_venue(v)

    # ------------------------------------------------------------------ venues

    def add_venue(self, venue: TradingVenue) -> None:
        """Registers a venue, rejecting a duplicate id rather than replacing it.

        Silently replacing would discard the existing venue's error counters and
        breaker state, re-enabling a venue that is currently tripped.
        """
        with self._lock:
            if venue.venue_id in self.venues:
                raise ValueError(
                    f"venue '{venue.venue_id}' is already registered; use "
                    f"update_quote() or mutate the existing venue instead"
                )
            self.venues[venue.venue_id] = venue

    def update_quote(
        self,
        venue_id: str,
        bid_price: float,
        ask_price: float,
        available_qty: float,
        monotonic_ts: Optional[float] = None,
    ) -> None:
        """Applies a fresh top-of-book quote, stamped with the monotonic clock."""
        with self._lock:
            venue = self._require_venue(venue_id)
            venue.bid_price = bid_price
            venue.ask_price = ask_price
            venue.available_qty = available_qty
            venue.quote_monotonic_ts = (
                time.monotonic() if monotonic_ts is None else monotonic_ts
            )

    def _require_venue(self, venue_id: str) -> TradingVenue:
        venue = self.venues.get(venue_id)
        if venue is None:
            raise KeyError(
                f"unknown venue '{venue_id}'; registered venues: {sorted(self.venues)}"
            )
        return venue

    # ------------------------------------------------------------------ health

    def report_venue_error(self, venue_id: str, error_msg: str) -> None:
        """Records a transport/API error and trips the breaker at the threshold.

        Raises ``KeyError`` for an unregistered venue. Swallowing the call would
        mean a typo in a venue id silently disables outage detection for that
        venue for the life of the process.
        """
        with self._lock:
            venue = self._require_venue(venue_id)
            now_mono = time.monotonic()
            venue.last_error_time = time.time()
            venue.last_error_monotonic = now_mono

            # A failed probe re-opens the breaker immediately, whatever the counter says.
            if venue.state == VenueHealthState.RECOVERY_PROBE:
                venue.consecutive_error_count += 1
                self._trip(venue, now_mono, error_msg, probe_failed=True)
                return

            if venue.state == VenueHealthState.CIRCUIT_BROKEN_OUTAGE:
                venue.consecutive_error_count += 1
                logger.warning(
                    f"Venue '{venue_id}' already CIRCUIT_BROKEN_OUTAGE; error noted "
                    f"(count = {venue.consecutive_error_count}). Error: {error_msg}"
                )
                return

            venue.consecutive_error_count += 1
            if venue.consecutive_error_count >= venue.max_error_threshold:
                self._trip(venue, now_mono, error_msg, probe_failed=False)
            else:
                venue.state = VenueHealthState.DEGRADED
                logger.warning(
                    f"Venue '{venue_id}' degraded. Error count = "
                    f"{venue.consecutive_error_count}/{venue.max_error_threshold}."
                )

    def _trip(
        self,
        venue: TradingVenue,
        now_mono: float,
        error_msg: str,
        probe_failed: bool,
    ) -> None:
        venue.state = VenueHealthState.CIRCUIT_BROKEN_OUTAGE
        venue.circuit_opened_monotonic = now_mono
        venue.consecutive_trips += 1
        reason = (
            "recovery probe failed" if probe_failed
            else f"{venue.consecutive_error_count} errors"
        )
        logger.error(
            f"CIRCUIT BREAKER TRIPPED for venue '{venue.venue_id}' after {reason}. "
            f"Cooldown = {self._cooldown_for(venue):.1f}s. Error: {error_msg}"
        )
        if self.diagnose_suspected_local_fault():
            down = sum(
                1 for v in self.venues.values()
                if v.state in (
                    VenueHealthState.CIRCUIT_BROKEN_OUTAGE,
                    VenueHealthState.RECOVERY_PROBE,
                )
            )
            logger.error(
                f"SUSPECTED LOCAL FAULT: {down} of {len(self.venues)} venues are "
                f"simultaneously unavailable. Check local connectivity, credentials "
                f"and clock before treating this as a market-wide outage "
                f"(SEC Rel. 34-51808 self-help self-diagnosis)."
            )

    def report_venue_success(self, venue_id: str) -> None:
        """Records a successful execution or heartbeat.

        A success on an open breaker deliberately does **not** close it: an
        acknowledgement for an order sent before the outage arrives after the
        trip and would otherwise resurrect a dead venue. Only a success while the
        venue is in ``RECOVERY_PROBE`` closes the breaker.
        """
        with self._lock:
            venue = self._require_venue(venue_id)

            if venue.state == VenueHealthState.CIRCUIT_BROKEN_OUTAGE:
                logger.info(
                    f"Success reported for venue '{venue_id}' while its circuit is "
                    f"open; ignored until the cooldown admits a recovery probe."
                )
                return

            if venue.state == VenueHealthState.RECOVERY_PROBE:
                logger.info(
                    f"Recovery probe succeeded for venue '{venue_id}'; circuit closed."
                )
                venue.consecutive_trips = 0

            venue.consecutive_error_count = 0
            venue.circuit_opened_monotonic = None
            venue.state = VenueHealthState.HEALTHY

    def _cooldown_for(self, venue: TradingVenue) -> float:
        # The exponent is capped before the power is taken: a venue that has
        # tripped thousands of times over a long-running process would otherwise
        # raise OverflowError here rather than simply saturating at the cap.
        trips = min(max(venue.consecutive_trips - 1, 0), 64)
        return min(
            self.cooldown_seconds * (self.backoff_multiplier ** trips),
            self.max_cooldown_seconds,
        )

    def refresh_venue_states(self) -> None:
        """Promotes any open breaker whose cooldown has elapsed to ``RECOVERY_PROBE``."""
        with self._lock:
            now_mono = time.monotonic()
            for venue in self.venues.values():
                if venue.state != VenueHealthState.CIRCUIT_BROKEN_OUTAGE:
                    continue
                opened = venue.circuit_opened_monotonic
                if opened is None:
                    continue
                if now_mono - opened >= self._cooldown_for(venue):
                    venue.state = VenueHealthState.RECOVERY_PROBE
                    logger.info(
                        f"Venue '{venue.venue_id}' cooldown elapsed; admitting a single "
                        f"RECOVERY_PROBE. The probe order carries outage risk."
                    )

    def diagnose_suspected_local_fault(self) -> bool:
        """True when enough venues are unavailable at once to suspect a local fault.

        Implements the self-diagnosis step the Regulation NMS adopting release
        attaches to the Rule 611(b)(1) self-help exception: before treating away
        venues as broken, check whether the problem is yours.
        """
        with self._lock:
            total = len(self.venues)
            if total < 2:
                return False
            down = sum(
                1 for v in self.venues.values()
                if v.state in (
                    VenueHealthState.CIRCUIT_BROKEN_OUTAGE,
                    VenueHealthState.RECOVERY_PROBE,
                )
            )
            return down >= max(2, math.ceil(self.local_fault_threshold_ratio * total))

    # ----------------------------------------------------------------- routing

    def _quote_for(self, venue: TradingVenue, side: str) -> float:
        return venue.ask_price if side == "BUY" else venue.bid_price

    def _eligibility(
        self, venue: TradingVenue, side: str, now_mono: float
    ) -> Optional[str]:
        """Returns the exclusion reason, or ``None`` if the venue may be routed to."""
        if venue.state == VenueHealthState.CIRCUIT_BROKEN_OUTAGE:
            return "CIRCUIT_BROKEN_OUTAGE"

        price = self._quote_for(venue, side)
        if not math.isfinite(price) or price <= 0.0:
            # The dataclass default is 0.0, so an unquoted venue -- or one whose
            # book was wiped on reconnect -- lands here. Without this check it
            # wins min(ask) on every BUY and the order routes at $0.00.
            return f"INVALID_QUOTE (price={price})"

        if not math.isfinite(venue.available_qty) or venue.available_qty <= 0.0:
            return f"NO_LIQUIDITY (available_qty={venue.available_qty})"

        ts = venue.quote_monotonic_ts
        if ts is None:
            if self.require_quote_timestamp:
                return "QUOTE_TIMESTAMP_MISSING"
        else:
            age = now_mono - ts
            if age < 0:
                # ``time.monotonic()`` never goes backwards, so a future-dated
                # quote means the caller stamped it from a different clock --
                # almost always ``time.time()``. Left unflagged, the subtraction
                # yields a large negative age and staleness checking is silently
                # disabled for the life of the process, which is precisely the
                # failure the check exists to prevent.
                return f"QUOTE_TIMESTAMP_IN_FUTURE (age={age:.3f}s; not time.monotonic()?)"
            if age > self.max_quote_age_seconds:
                return (
                    f"STALE_QUOTE (age={age:.3f}s > {self.max_quote_age_seconds}s)"
                )
        return None

    def route_order(
        self,
        order_id: str,
        side: str,                       # 'BUY' or 'SELL'
        quantity: float,
        preferred_venue_id: Optional[str] = None,
    ) -> SORRoutingResult:
        """Routes an order to the best eligible venue, failing over around outages.

        Args:
            order_id: Caller's order identifier, echoed into the audit trail.
            side: ``'BUY'`` or ``'SELL'``. Any other value raises ``ValueError``
                rather than silently defaulting to a sell.
            quantity: Positive, finite quantity.
            preferred_venue_id: Optional override. Honoured only if that venue is
                eligible; any price given up versus the best eligible venue is
                recorded in ``price_improvement_forgone`` for best-execution review.

        Raises:
            ValueError: invalid ``side`` or ``quantity``.
            KeyError: ``preferred_venue_id`` is not registered.
            NoEligibleVenueError: no venue can accept the order.
        """
        if not isinstance(side, str):
            raise ValueError(f"side must be a string, got {type(side).__name__}")
        normalized_side = side.upper()
        if normalized_side not in ("BUY", "SELL"):
            raise ValueError(f"side must be 'BUY' or 'SELL', got {side!r}")
        if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
            raise ValueError(f"quantity must be numeric, got {type(quantity).__name__}")
        if not math.isfinite(quantity) or quantity <= 0:
            raise ValueError(f"quantity must be finite and > 0, got {quantity}")

        with self._lock:
            if not self.venues:
                raise NoEligibleVenueError(
                    "SOR FAILOVER FAILURE: no venues registered.", {}, False
                )

            self.refresh_venue_states()
            now_mono = time.monotonic()
            if preferred_venue_id is not None:
                self._require_venue(preferred_venue_id)

            eligible: List[TradingVenue] = []
            excluded: Dict[str, str] = {}
            undated: List[str] = []
            for venue in self.venues.values():
                reason = self._eligibility(venue, normalized_side, now_mono)
                if reason is None:
                    eligible.append(venue)
                    if venue.quote_monotonic_ts is None:
                        undated.append(venue.venue_id)
                else:
                    excluded[venue.venue_id] = reason

            suspected_local_fault = self.diagnose_suspected_local_fault()

            if not eligible:
                raise NoEligibleVenueError(
                    "SOR FAILOVER FAILURE: All venues are in CIRCUIT_BROKEN_OUTAGE "
                    f"state or otherwise ineligible. Reasons: {excluded}. "
                    f"Suspected local fault: {suspected_local_fault}.",
                    excluded,
                    suspected_local_fault,
                )

            best_venue = self._select(eligible, normalized_side)
            best_price = self._quote_for(best_venue, normalized_side)

            selected_venue = best_venue
            preferred_override = False
            preferred_unavailable = False
            if preferred_venue_id is not None:
                pref = self.venues[preferred_venue_id]
                if preferred_venue_id in excluded:
                    preferred_unavailable = True
                    logger.warning(
                        f"Preferred venue '{preferred_venue_id}' unavailable "
                        f"({excluded[preferred_venue_id]}). Triggering failover."
                    )
                elif pref is not best_venue:
                    selected_venue = pref
                    preferred_override = True

            price = self._quote_for(selected_venue, normalized_side)
            # Buying above, or selling below, the best eligible quote.
            forgone = (
                price - best_price if normalized_side == "BUY" else best_price - price
            )
            price_improvement_forgone = max(forgone, 0.0)
            if preferred_override and price_improvement_forgone > 0:
                logger.warning(
                    f"Preferred venue '{selected_venue.venue_id}' honoured at an "
                    f"inferior price: {price} vs best eligible {best_price} at "
                    f"'{best_venue.venue_id}' "
                    f"({price_improvement_forgone:.6f}/share forgone). This is a "
                    f"best-execution exception and must be justifiable."
                )

            # Every venue we bypassed whose last known quote was at least as good
            # as the one we routed to. This is the self-help audit record, and it
            # is populated whether or not a preferred venue was supplied. An
            # explicitly requested venue that turned out to be ineligible is
            # always recorded, even if its quote was worse than the fill: the
            # caller asked for it and did not get it.
            bypassed_set = {
                vid for vid in excluded
                if self._was_price_competitive(self.venues[vid], normalized_side, price)
            }
            if preferred_unavailable and preferred_venue_id is not None:
                bypassed_set.add(preferred_venue_id)
            bypassed = sorted(bypassed_set)
            is_failover = bool(bypassed) or preferred_override or preferred_unavailable

            routed_qty = min(quantity, selected_venue.available_qty)
            unrouted_qty = quantity - routed_qty

            notes = (
                f"SOR ROUTE [{order_id}]: {routed_qty} of {quantity} {normalized_side} "
                f"to '{selected_venue.venue_id}' @ {price} "
                f"(state={VenueHealthState(selected_venue.state).value}, "
                f"failover={is_failover}, bypassed={bypassed}, "
                f"unrouted={unrouted_qty}, forgone={price_improvement_forgone:.6f}, "
                f"excluded={excluded}, suspected_local_fault={suspected_local_fault})."
            )
            if unrouted_qty > 0:
                logger.warning(
                    f"Order '{order_id}': {unrouted_qty} of {quantity} not routed -- "
                    f"'{selected_venue.venue_id}' shows only "
                    f"{selected_venue.available_qty} available. The caller owns the "
                    f"residual."
                )
            logger.info(notes)

            return SORRoutingResult(
                order_id=order_id,
                target_venue_id=selected_venue.venue_id,
                routed_quantity=routed_qty,
                routed_price=price,
                is_failover_triggered=is_failover,
                fallback_venues_used=bypassed,
                audit_notes=notes,
                unrouted_quantity=unrouted_qty,
                price_improvement_forgone=price_improvement_forgone,
                excluded_venues=excluded,
                suspected_local_fault=suspected_local_fault,
                stale_quote_check_skipped=sorted(undated),
            )

    def _was_price_competitive(
        self, venue: TradingVenue, side: str, routed_price: float
    ) -> bool:
        """True if the excluded venue's last quote was at least as good as the fill."""
        price = self._quote_for(venue, side)
        if not math.isfinite(price) or price <= 0.0:
            return False
        return price <= routed_price if side == "BUY" else price >= routed_price

    def _select(self, eligible: List[TradingVenue], side: str) -> TradingVenue:
        """Ranks eligible venues: probes last, then best price, health, latency.

        Price leads the ranking among non-probe venues so a DEGRADED venue at a
        better price is not skipped -- that would be a trade-through taken to
        avoid a venue with a single timeout. ``RECOVERY_PROBE`` venues are
        demoted below every other venue regardless of price, because a venue that
        just failed is the one most likely to be quoting a stale price.
        """
        def key(v: TradingVenue) -> Tuple[int, float, int, float]:
            probe_last = 1 if v.state == VenueHealthState.RECOVERY_PROBE else 0
            health = _HEALTH_RANK[VenueHealthState(v.state).value]
            price = self._quote_for(v, side)
            signed_price = price if side == "BUY" else -price
            return (probe_last, signed_price, health, v.latency_ms)

        return min(eligible, key=key)
