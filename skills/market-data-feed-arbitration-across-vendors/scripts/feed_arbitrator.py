"""Cross-vendor market data arbitration: consensus pricing, stale-feed failover, bad-tick quarantine.

This module arbitrates between **two independent vendor price streams for the same
instrument** and answers one question per tick: *is there a price we are entitled to
trade on, and has it been cross-verified?*

What this module is NOT
-----------------------
It is **not** A/B line arbitration. An exchange that publishes two identical UDP lines
(CME MDP 3.0 disseminates every packet on both "UDP Feed A" and "UDP Feed B" so that
UDP loss on one line is recovered from the other) is arbitrated *losslessly by packet
sequence number* - first copy wins, duplicate is discarded, gap triggers recovery. Two
different vendors carry different content on different paths and have no shared
sequence space, so nothing here deduplicates messages. Use sequence-number arbitration
wherever the two feeds are copies of one stream; use this module only where they are
genuinely independent sources.

Why divergence is not the same thing as a bad tick
--------------------------------------------------
Cross-vendor price disagreement is dominated by *relative latency*, not by corruption.
The SEC's Market Data Infrastructure adopting release describes exactly this structural
gap between consolidated (SIP) data and exchange proprietary feeds: participants who
buy proprietary depth-of-book feeds and the associated connectivity "receive more
content-rich data faster" than those consuming the consolidated tapes. Two feeds
observed at two different instants are simply two different observations, and treating
every disagreement as an outlier produces false quarantines during fast markets.

Consequently, with exactly two sources, an outlier **cannot be attributed from price
alone**: a real move looks identical to a bad tick until a third piece of evidence
arrives. This module therefore separates evidence from policy:

* Evidence-based quarantine - a vendor whose own price has been frozen while the
  counterpart moved is demonstrably not tracking the market, and is quarantined.
* Policy-based fallback - a divergence that persists beyond the confirmation window
  with no distinguishing evidence resolves to the operator-configured reference vendor.
  That is a *choice*, recorded as such, not a detection.
* Everything unresolved is emitted with ``is_trusted=False`` rather than being
  silently dressed up as a consensus price.

Clock discipline
----------------
``timestamp`` is a **local receipt time on one clock** (``time.time()``, or better a
monotonic source), never a vendor- or exchange-supplied event time. Staleness is a
duration; measuring it across two vendors' clocks measures their skew instead. See
``clock-skew-correction-for-tick-timestamps``.

Regulatory context is in ``references/standards.md``. Nothing here is compliance advice.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

PRIMARY = "primary"
SECONDARY = "secondary"
VENDOR_KEYS: Tuple[str, str] = (PRIMARY, SECONDARY)

#: Emitted in ``active_vendor`` when both feeds contributed to the price.
CONSENSUS_BOTH = "CONSENSUS_BOTH"
#: Emitted in ``active_vendor`` when no feed is usable.
NO_ACTIVE_VENDOR = "NONE"


class VendorStatus(str, Enum):
    """Per-vendor health as of the arbitration that produced the result."""

    HEALTHY = "HEALTHY"
    #: Divergence observed but not yet attributable to this vendor.
    DIVERGENT_UNCONFIRMED = "DIVERGENT_UNCONFIRMED"
    #: Quarantined: excluded from pricing until the recovery condition is met.
    DIVERGENT_OUTLIER = "DIVERGENT_OUTLIER"
    #: Still delivering ticks, but its price has not moved while the counterpart's did.
    FROZEN_PRICE = "FROZEN_PRICE"
    #: No tick within ``max_stale_seconds``.
    STALE_TIMEOUT = "STALE_TIMEOUT"
    #: Never delivered a tick for this symbol.
    NO_DATA = "NO_DATA"


class ArbitrationDecision(str, Enum):
    """What the arbitrator concluded, independent of which vendor won."""

    #: Both feeds fresh, simultaneous and within tolerance. The only cross-verified state.
    CONSENSUS = "CONSENSUS"
    #: Only one vendor has ever ticked for this symbol. Not cross-verified.
    SINGLE_FEED = "SINGLE_FEED"
    #: Counterpart stale; running on the surviving feed.
    FAILOVER = "FAILOVER"
    #: Feeds disagree, but their observations are too far apart in time to compare.
    LATENCY_SKEW_UNVERIFIED = "LATENCY_SKEW_UNVERIFIED"
    #: Simultaneous observations disagree; no evidence yet identifies the outlier.
    DIVERGENCE_UNRESOLVED = "DIVERGENCE_UNRESOLVED"
    #: A vendor is quarantined; pricing from the surviving feed.
    QUARANTINE_ACTIVE = "QUARANTINE_ACTIVE"
    #: No usable price: every feed is stale or absent.
    NO_TRUSTED_FEED = "NO_TRUSTED_FEED"
    #: The submitted tick was older than this vendor's last observation and was dropped.
    TICK_REJECTED_OUT_OF_ORDER = "TICK_REJECTED_OUT_OF_ORDER"


@dataclass(frozen=True)
class VendorTick:
    """One vendor observation, timestamped on the **local receipt clock**."""

    vendor_id: str
    symbol: str
    price: float
    timestamp: float


@dataclass
class ArbitratedTickResult:
    """Outcome of one arbitration.

    Two independent flags, because "usable" and "verified" are different claims:

    * ``is_trusted`` - a price is available and no unresolved integrity conflict stands
      against it. ``False`` means the caller must not open new risk on this tick.
    * ``is_cross_verified`` - two independent, simultaneous, fresh feeds agreed within
      tolerance. Only ``CONSENSUS`` sets this. A failover price is trusted but never
      cross-verified: there is nothing left to check it against.

    ``consensus_price`` is ``None`` only when no feed is usable, and
    ``relative_divergence_pct`` is ``None`` whenever no comparison was performed -
    never ``0.0``, which would read downstream as "the feeds agreed exactly".
    """

    symbol: str
    consensus_price: Optional[float]
    is_arbitrated: bool
    primary_vendor_status: VendorStatus
    secondary_vendor_status: VendorStatus
    relative_divergence_pct: Optional[float]
    active_vendor: str
    message: str
    decision: ArbitrationDecision = ArbitrationDecision.CONSENSUS
    is_trusted: bool = True
    is_cross_verified: bool = False
    quarantined_vendor: Optional[str] = None
    #: Seconds between the two feeds' last observations. ``None`` if only one exists.
    feed_age_gap_seconds: Optional[float] = None


@dataclass
class _VendorState:
    last_tick: Optional[VendorTick] = None
    #: Receipt time at which this vendor's price last *changed*, for frozen-feed detection.
    price_changed_at: Optional[float] = None


@dataclass
class _SymbolState:
    vendors: Dict[str, _VendorState] = field(
        default_factory=lambda: {PRIMARY: _VendorState(), SECONDARY: _VendorState()}
    )
    quarantined_vendor: Optional[str] = None
    #: Why the quarantine was raised, so a frozen feed is not later relabelled as a
    #: policy fallback (and vice versa) in every subsequent result.
    quarantine_status: VendorStatus = VendorStatus.DIVERGENT_OUTLIER
    #: When the standing quarantine was raised, so a frozen feed must demonstrably move
    #: again before price agreement is allowed to count toward its release.
    quarantined_at: Optional[float] = None
    divergence_started_at: Optional[float] = None
    clean_comparisons: int = 0
    #: Last decision logged, so a fast market does not produce one log line per tick.
    last_logged_decision: Optional[ArbitrationDecision] = None
    #: When that log was emitted, so a decision *alternating* every tick is throttled too.
    last_logged_at: Optional[float] = None


def _require_finite_price(price: float, label: str) -> float:
    """Reject NaN, infinity and non-positive prices before they enter the state.

    An unchecked NaN survives every comparison here (``nan <= tolerance`` is ``False``),
    routes to the divergence branch and is emitted as a tradeable price. A zero or
    negative pair also makes the midpoint denominator vanish.
    """
    try:
        value = float(price)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a real number, got {price!r}.") from exc
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite, got {value!r}.")
    if value <= 0.0:
        raise ValueError(f"{label} must be strictly positive, got {value!r}.")
    return value


def _require_finite_timestamp(timestamp: float, label: str) -> float:
    try:
        value = float(timestamp)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a real number, got {timestamp!r}.") from exc
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite, got {value!r}.")
    return value


class MarketDataFeedArbitrator:
    """Arbitrates two redundant vendor price streams for the same instrument.

    Args:
        max_divergence_pct: Tolerance for cross-vendor disagreement, in **percent**
            (``0.05`` = 5 bps). There is no universal correct value: it must be at least
            the instrument's minimum price increment expressed in percent, or a single
            legal one-tick disagreement breaches it. For a US NMS stock quoted in
            $0.01 increments, 5 bps is narrower than one tick below $20. Calibrate from
            recorded cross-vendor history per instrument.
        max_stale_seconds: A feed with no tick for longer than this is stale. Must
            exceed the instrument's own quiet periods, otherwise an illiquid symbol is
            permanently "stale" between genuine ticks.
        max_comparison_age_seconds: Two observations further apart than this are not
            treated as simultaneous, so a disagreement between them is attributed to
            latency skew rather than to a vendor.
        divergence_confirmation_seconds: How long a simultaneous, unexplained divergence
            must persist before the reference-vendor fallback policy is applied. A
            confirmation window of zero reproduces the unsafe "quarantine on the first
            disagreeing tick" behaviour and will flap during fast markets.
        recovery_consecutive_ticks: Consecutive clean comparisons required to release a
            quarantine. Hysteresis; releasing on the first agreement flaps.
        frozen_price_seconds: A vendor whose price has not changed for this long while
            the counterpart's has is quarantined on evidence.
        reference_vendor: Which feed wins an unexplained, confirmed divergence. This is
            operator policy, not outlier detection.
        log_throttle_seconds: Minimum interval between feed-state log lines. Transition-
            only logging is not sufficient on its own: a divergence hovering at the
            tolerance produces CONSENSUS/UNRESOLVED on alternate ticks, and every one of
            them is a transition. Escalations to a quarantine or to no-trusted-feed
            bypass the throttle, so the worst-case alerting delay applies only to
            non-escalating states and stays well inside the five-second real-time alert
            ceiling in RTS 6 Article 16.

    Thread safety: feed handlers typically run one thread per vendor session, so every
    public method takes an internal re-entrant lock. State is per-instance; construct
    one arbitrator per logical feed pair rather than sharing one across pairs.
    """

    def __init__(
        self,
        max_divergence_pct: float = 0.05,
        max_stale_seconds: float = 2.0,
        max_comparison_age_seconds: float = 0.25,
        divergence_confirmation_seconds: float = 1.0,
        recovery_consecutive_ticks: int = 3,
        frozen_price_seconds: float = 5.0,
        reference_vendor: str = PRIMARY,
        log_throttle_seconds: float = 1.0,
    ) -> None:
        if max_divergence_pct <= 0.0 or not math.isfinite(max_divergence_pct):
            raise ValueError("max_divergence_pct must be a finite positive percentage.")
        if max_stale_seconds <= 0.0 or not math.isfinite(max_stale_seconds):
            raise ValueError("max_stale_seconds must be a finite positive number of seconds.")
        if max_comparison_age_seconds < 0.0 or not math.isfinite(max_comparison_age_seconds):
            raise ValueError("max_comparison_age_seconds must be a finite non-negative number.")
        if divergence_confirmation_seconds < 0.0 or not math.isfinite(divergence_confirmation_seconds):
            raise ValueError("divergence_confirmation_seconds must be a finite non-negative number.")
        if recovery_consecutive_ticks < 1:
            raise ValueError("recovery_consecutive_ticks must be at least 1.")
        if frozen_price_seconds <= 0.0 or not math.isfinite(frozen_price_seconds):
            raise ValueError("frozen_price_seconds must be a finite positive number of seconds.")
        if log_throttle_seconds < 0.0 or not math.isfinite(log_throttle_seconds):
            raise ValueError("log_throttle_seconds must be a finite non-negative number.")
        if reference_vendor not in VENDOR_KEYS:
            raise ValueError(f"reference_vendor must be one of {VENDOR_KEYS}, got {reference_vendor!r}.")

        self.max_divergence_pct = float(max_divergence_pct)
        self.max_stale_seconds = float(max_stale_seconds)
        self.max_comparison_age_seconds = float(max_comparison_age_seconds)
        self.divergence_confirmation_seconds = float(divergence_confirmation_seconds)
        self.recovery_consecutive_ticks = int(recovery_consecutive_ticks)
        self.frozen_price_seconds = float(frozen_price_seconds)
        self.reference_vendor = reference_vendor
        self.log_throttle_seconds = float(log_throttle_seconds)

        self._lock = threading.RLock()
        self._symbols: Dict[str, _SymbolState] = {}

    # ------------------------------------------------------------------ ingest

    def process_vendor_tick(
        self,
        vendor_id: str,
        symbol: str,
        price: float,
        timestamp: Optional[float] = None,
    ) -> ArbitratedTickResult:
        """Record one vendor tick and arbitrate the symbol.

        Args:
            vendor_id: ``'primary'`` or ``'secondary'`` (case-insensitive). Any other
                value raises rather than being silently routed to a default feed.
            symbol: Instrument identifier; upper-cased for keying.
            price: Positive, finite price on the same basis for both vendors - two
                vendors' *last trade* or two vendors' *quote midpoint*, never one of
                each. Mixing bases guarantees a permanent spread-sized divergence.
            timestamp: Local receipt time in seconds. Defaults to ``time.time()``.
                ``0.0`` is a valid timestamp and is honoured as given.

        Returns:
            The arbitration result for this symbol as of this tick.

        Raises:
            ValueError: On an unknown vendor, empty symbol, or non-finite/non-positive
                price or timestamp.
        """
        v_key = self._normalise_vendor(vendor_id)
        sym = self._normalise_symbol(symbol)
        checked_price = _require_finite_price(price, "price")
        now = time.time() if timestamp is None else _require_finite_timestamp(timestamp, "timestamp")

        with self._lock:
            state = self._symbols.setdefault(sym, _SymbolState())
            vendor_state = state.vendors[v_key]
            previous = vendor_state.last_tick

            # A late or replayed tick must not overwrite a newer observation: doing so
            # rewinds the vendor's age and can un-stale a feed that has actually died.
            if previous is not None and now < previous.timestamp:
                logger.warning(
                    "Dropping out-of-order tick for %s from %s: t=%.6f is older than last observation t=%.6f.",
                    sym, v_key, now, previous.timestamp,
                )
                latest_known = max(
                    v.last_tick.timestamp for v in state.vendors.values() if v.last_tick is not None
                )
                result = self._arbitrate(sym, state, latest_known)
                return self._with_message(
                    result,
                    ArbitrationDecision.TICK_REJECTED_OUT_OF_ORDER,
                    f"Out-of-order tick from {v_key} dropped; state unchanged.",
                )

            if previous is None or previous.price != checked_price:
                vendor_state.price_changed_at = now
            vendor_state.last_tick = VendorTick(
                vendor_id=v_key, symbol=sym, price=checked_price, timestamp=now
            )

            return self._arbitrate(sym, state, now)

    def evaluate_feed_health(
        self, symbol: str, now: Optional[float] = None
    ) -> ArbitratedTickResult:
        """Re-arbitrate a symbol **without** a tick, for heartbeat-driven monitoring.

        Staleness cannot be discovered by tick arrival alone: the arriving tick is
        always fresh, so a vendor is only ever seen as stale by its counterpart's
        traffic. When *both* vendors go silent - the outage this whole component exists
        for - no tick arrives and nothing is evaluated. A supervisor must call this on a
        timer, at an interval well below ``max_stale_seconds``, to detect a blackout.

        Args:
            symbol: Instrument to evaluate.
            now: Local clock reading; defaults to ``time.time()``.
        """
        sym = self._normalise_symbol(symbol)
        at = time.time() if now is None else _require_finite_timestamp(now, "now")
        with self._lock:
            # A probe for a symbol that has never ticked must not allocate permanent
            # state: a monitor sweeping a large watchlist would otherwise grow the map
            # without a single tick ever arriving.
            state = self._symbols.get(sym)
            if state is None:
                return self._arbitrate(sym, _SymbolState(), at)
            return self._arbitrate(sym, state, at)

    def reset(self, symbol: Optional[str] = None) -> None:
        """Clear arbitration state for one symbol, or all symbols.

        Call at a session boundary: an overnight gap otherwise looks like a stale feed
        plus a large divergence when the next session opens.
        """
        with self._lock:
            if symbol is None:
                self._symbols.clear()
            else:
                self._symbols.pop(self._normalise_symbol(symbol), None)

    # --------------------------------------------------------------- internals

    @staticmethod
    def _normalise_vendor(vendor_id: str) -> str:
        if not isinstance(vendor_id, str):
            raise ValueError(f"vendor_id must be a string, got {vendor_id!r}.")
        v_key = vendor_id.strip().lower()
        if v_key not in VENDOR_KEYS:
            raise ValueError(f"vendor_id must be one of {VENDOR_KEYS}, got {vendor_id!r}.")
        return v_key

    @staticmethod
    def _normalise_symbol(symbol: str) -> str:
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"symbol must be a non-empty string, got {symbol!r}.")
        return symbol.strip().upper()

    @staticmethod
    def _other(vendor: str) -> str:
        return SECONDARY if vendor == PRIMARY else PRIMARY

    #: Escalations that must never wait for the log throttle.
    _ALWAYS_LOG = (
        ArbitrationDecision.NO_TRUSTED_FEED,
        ArbitrationDecision.QUARANTINE_ACTIVE,
    )

    def _log_transition(
        self, symbol: str, state: _SymbolState, result: ArbitratedTickResult, now: float
    ) -> None:
        """Log on state change, throttled. A per-tick log line is a storm on a hot path.

        Transition-only logging alone does not bound the rate: a divergence sitting on
        the tolerance alternates CONSENSUS/DIVERGENCE_UNRESOLVED on every tick, and each
        one is a transition. Escalations bypass the throttle.
        """
        if state.last_logged_decision == result.decision:
            return
        throttled = (
            result.decision not in self._ALWAYS_LOG
            and state.last_logged_at is not None
            and (now - state.last_logged_at) < self.log_throttle_seconds
        )
        state.last_logged_decision = result.decision
        if throttled:
            return
        state.last_logged_at = now
        if result.decision in (
            ArbitrationDecision.CONSENSUS,
            ArbitrationDecision.SINGLE_FEED,
        ):
            logger.info("[%s] %s", symbol, result.message)
        elif result.decision in (
            ArbitrationDecision.NO_TRUSTED_FEED,
            ArbitrationDecision.QUARANTINE_ACTIVE,
        ):
            logger.error("[%s] %s", symbol, result.message)
        else:
            logger.warning("[%s] %s", symbol, result.message)

    def _with_message(
        self, result: ArbitratedTickResult, decision: ArbitrationDecision, message: str
    ) -> ArbitratedTickResult:
        result.decision = decision
        result.message = f"{message} (last arbitration: {result.message})"
        return result

    def _arbitrate(self, symbol: str, state: _SymbolState, now: float) -> ArbitratedTickResult:
        p_tick = state.vendors[PRIMARY].last_tick
        s_tick = state.vendors[SECONDARY].last_tick

        if p_tick is None and s_tick is None:
            result = ArbitratedTickResult(
                symbol=symbol,
                consensus_price=None,
                is_arbitrated=False,
                primary_vendor_status=VendorStatus.NO_DATA,
                secondary_vendor_status=VendorStatus.NO_DATA,
                relative_divergence_pct=None,
                active_vendor=NO_ACTIVE_VENDOR,
                message="No vendor has delivered a tick for this symbol.",
                decision=ArbitrationDecision.NO_TRUSTED_FEED,
                is_trusted=False,
            )
            self._log_transition(symbol, state, result, now)
            return result

        # Exactly one vendor has ever ticked. Usable, but nothing checks it.
        if p_tick is None or s_tick is None:
            live_key = PRIMARY if p_tick is not None else SECONDARY
            live_tick = p_tick if p_tick is not None else s_tick
            assert live_tick is not None  # narrowed above; keeps type checkers honest
            stale = (now - live_tick.timestamp) > self.max_stale_seconds
            live_status = VendorStatus.STALE_TIMEOUT if stale else VendorStatus.HEALTHY
            statuses = {live_key: live_status, self._other(live_key): VendorStatus.NO_DATA}
            result = ArbitratedTickResult(
                symbol=symbol,
                consensus_price=None if stale else live_tick.price,
                is_arbitrated=False,
                primary_vendor_status=statuses[PRIMARY],
                secondary_vendor_status=statuses[SECONDARY],
                relative_divergence_pct=None,
                active_vendor=NO_ACTIVE_VENDOR if stale else live_key,
                message=(
                    f"Only feed ({live_key}) is stale ({now - live_tick.timestamp:.3f}s "
                    f"> {self.max_stale_seconds}s). No price available."
                    if stale
                    else f"Single feed active ({live_key}); no counterpart tick to cross-verify against."
                ),
                decision=(
                    ArbitrationDecision.NO_TRUSTED_FEED if stale else ArbitrationDecision.SINGLE_FEED
                ),
                is_trusted=not stale,
            )
            self._log_transition(symbol, state, result, now)
            return result

        p_age = now - p_tick.timestamp
        s_age = now - s_tick.timestamp
        p_stale = p_age > self.max_stale_seconds
        s_stale = s_age > self.max_stale_seconds
        age_gap = abs(p_tick.timestamp - s_tick.timestamp)

        if p_stale and s_stale:
            state.divergence_started_at = None
            state.clean_comparisons = 0
            result = ArbitratedTickResult(
                symbol=symbol,
                consensus_price=None,
                is_arbitrated=True,
                primary_vendor_status=VendorStatus.STALE_TIMEOUT,
                secondary_vendor_status=VendorStatus.STALE_TIMEOUT,
                relative_divergence_pct=None,
                active_vendor=NO_ACTIVE_VENDOR,
                message=(
                    f"Both vendor feeds stale (primary {p_age:.3f}s, secondary {s_age:.3f}s "
                    f"> {self.max_stale_seconds}s). No trusted price."
                ),
                decision=ArbitrationDecision.NO_TRUSTED_FEED,
                is_trusted=False,
                feed_age_gap_seconds=age_gap,
            )
            self._log_transition(symbol, state, result, now)
            return result

        if p_stale != s_stale:
            live_key = SECONDARY if p_stale else PRIMARY
            live_tick = s_tick if p_stale else p_tick
            stale_age = p_age if p_stale else s_age
            # A stale counterpart cannot corroborate or contradict anything, so any
            # standing quarantine decision is no longer being evaluated.
            state.divergence_started_at = None
            state.clean_comparisons = 0

            # The survivor may itself be the quarantined feed. Failing over onto a feed
            # that was quarantined for disagreeing would silently promote a known-bad
            # source to sole price authority, so the price is emitted UNTRUSTED instead.
            surviving_is_quarantined = state.quarantined_vendor == live_key
            statuses = {
                PRIMARY: VendorStatus.STALE_TIMEOUT if p_stale else VendorStatus.HEALTHY,
                SECONDARY: VendorStatus.STALE_TIMEOUT if s_stale else VendorStatus.HEALTHY,
            }
            if surviving_is_quarantined:
                statuses[live_key] = state.quarantine_status
            result = ArbitratedTickResult(
                symbol=symbol,
                consensus_price=live_tick.price,
                is_arbitrated=True,
                primary_vendor_status=statuses[PRIMARY],
                secondary_vendor_status=statuses[SECONDARY],
                relative_divergence_pct=None,
                active_vendor=live_key,
                message=(
                    f"{self._other(live_key).capitalize()} feed stale ({stale_age:.3f}s > "
                    f"{self.max_stale_seconds}s). "
                    + (
                        f"Only surviving feed ({live_key}) is itself quarantined; price emitted UNTRUSTED."
                        if surviving_is_quarantined
                        else f"Failed over to {live_key}; price not cross-verified."
                    )
                ),
                decision=ArbitrationDecision.FAILOVER,
                is_trusted=not surviving_is_quarantined,
                quarantined_vendor=state.quarantined_vendor,
                feed_age_gap_seconds=age_gap,
            )
            self._log_transition(symbol, state, result, now)
            return result

        # Both feeds fresh.
        divergence_pct = abs(p_tick.price - s_tick.price) / ((p_tick.price + s_tick.price) / 2.0) * 100.0
        # The tolerance boundary is documented as inclusive, so a divergence that is
        # exactly the tolerance must not be rejected by representation error alone
        # (99.975 vs 100.025 evaluates to 0.05000000000000071% against a 0.05% limit).
        within_tolerance = divergence_pct <= self.max_divergence_pct or math.isclose(
            divergence_pct, self.max_divergence_pct, rel_tol=1e-9, abs_tol=0.0
        )
        comparable = age_gap <= self.max_comparison_age_seconds

        if state.quarantined_vendor is not None:
            return self._handle_quarantine(
                symbol, state, now, p_tick, s_tick, divergence_pct, age_gap, within_tolerance, comparable
            )

        if within_tolerance and comparable:
            state.divergence_started_at = None
            state.clean_comparisons += 1
            midpoint = (p_tick.price + s_tick.price) / 2.0
            result = ArbitratedTickResult(
                symbol=symbol,
                consensus_price=midpoint,
                is_arbitrated=True,
                primary_vendor_status=VendorStatus.HEALTHY,
                secondary_vendor_status=VendorStatus.HEALTHY,
                relative_divergence_pct=round(divergence_pct, 6),
                active_vendor=CONSENSUS_BOTH,
                message=(
                    f"Consensus validated (divergence {divergence_pct:.4f}% <= "
                    f"{self.max_divergence_pct}%, feeds {age_gap * 1000:.1f}ms apart)."
                ),
                decision=ArbitrationDecision.CONSENSUS,
                is_trusted=True,
                is_cross_verified=True,
                feed_age_gap_seconds=age_gap,
            )
            self._log_transition(symbol, state, result, now)
            return result

        if within_tolerance:
            # Agreeing, but on observations taken at different instants. Take the
            # fresher price rather than averaging two different moments together.
            # The divergence episode is deliberately NOT cleared here: only a
            # comparable, in-tolerance comparison is evidence that a divergence ended.
            state.clean_comparisons = 0
            fresher_key = PRIMARY if p_tick.timestamp >= s_tick.timestamp else SECONDARY
            fresher = p_tick if fresher_key == PRIMARY else s_tick
            result = ArbitratedTickResult(
                symbol=symbol,
                consensus_price=fresher.price,
                is_arbitrated=True,
                primary_vendor_status=VendorStatus.HEALTHY,
                secondary_vendor_status=VendorStatus.HEALTHY,
                relative_divergence_pct=round(divergence_pct, 6),
                active_vendor=fresher_key,
                message=(
                    f"Feeds agree ({divergence_pct:.4f}%) but observations are {age_gap * 1000:.1f}ms "
                    f"apart (> {self.max_comparison_age_seconds * 1000:.0f}ms). Using freshest feed "
                    f"({fresher_key}) instead of averaging non-simultaneous prices."
                ),
                decision=ArbitrationDecision.LATENCY_SKEW_UNVERIFIED,
                is_trusted=True,
                feed_age_gap_seconds=age_gap,
            )
            self._log_transition(symbol, state, result, now)
            return result

        return self._handle_divergence(symbol, state, now, p_tick, s_tick, divergence_pct, age_gap, comparable)

    def _handle_quarantine(
        self,
        symbol: str,
        state: _SymbolState,
        now: float,
        p_tick: VendorTick,
        s_tick: VendorTick,
        divergence_pct: float,
        age_gap: float,
        within_tolerance: bool,
        comparable: bool,
    ) -> ArbitratedTickResult:
        """Price from the surviving feed, and evaluate release of the quarantine."""
        quarantined = state.quarantined_vendor
        assert quarantined is not None
        surviving = self._other(quarantined)

        # A frozen feed has not recovered merely because the moving feed wandered back
        # across its stuck price: without this the quarantine flaps every time a random
        # walk crosses the frozen level. It must be seen to move again first.
        moving_again = True
        if state.quarantine_status == VendorStatus.FROZEN_PRICE:
            changed_at = state.vendors[quarantined].price_changed_at
            moving_again = (
                changed_at is not None
                and state.quarantined_at is not None
                and changed_at > state.quarantined_at
            )

        if within_tolerance and comparable and moving_again:
            state.clean_comparisons += 1
        else:
            state.clean_comparisons = 0

        if state.clean_comparisons >= self.recovery_consecutive_ticks:
            logger.warning(
                "[%s] Releasing quarantine on %s after %d consecutive clean comparisons.",
                symbol, quarantined, state.clean_comparisons,
            )
            state.quarantined_vendor = None
            state.quarantined_at = None
            state.divergence_started_at = None
            state.last_logged_decision = None
            return self._arbitrate(symbol, state, now)

        surviving_tick = p_tick if surviving == PRIMARY else s_tick
        statuses = {surviving: VendorStatus.HEALTHY, quarantined: state.quarantine_status}
        result = ArbitratedTickResult(
            symbol=symbol,
            consensus_price=surviving_tick.price,
            is_arbitrated=True,
            primary_vendor_status=statuses[PRIMARY],
            secondary_vendor_status=statuses[SECONDARY],
            relative_divergence_pct=round(divergence_pct, 6),
            active_vendor=surviving,
            message=(
                f"{quarantined.capitalize()} quarantined (divergence {divergence_pct:.4f}%). "
                f"Pricing from {surviving}; {state.clean_comparisons}/{self.recovery_consecutive_ticks} "
                f"clean comparisons toward release."
            ),
            decision=ArbitrationDecision.QUARANTINE_ACTIVE,
            is_trusted=True,
            quarantined_vendor=quarantined,
            feed_age_gap_seconds=age_gap,
        )
        self._log_transition(symbol, state, result, now)
        return result

    def _detect_frozen_vendor(self, state: _SymbolState, now: float) -> Optional[str]:
        """Return a vendor that has stopped moving while its counterpart moved.

        This is the one outlier attribution available from two feeds alone: a feed that
        is still delivering ticks but repeating one price, while the counterpart has
        moved, is demonstrably not tracking the market. A genuine market move is ruled
        out because a genuine move eventually shows on both feeds.
        """
        for vendor in VENDOR_KEYS:
            own = state.vendors[vendor].price_changed_at
            other = state.vendors[self._other(vendor)].price_changed_at
            if own is None or other is None:
                continue
            if (now - own) >= self.frozen_price_seconds and (now - other) < self.frozen_price_seconds:
                return vendor
        return None

    def _handle_divergence(
        self,
        symbol: str,
        state: _SymbolState,
        now: float,
        p_tick: VendorTick,
        s_tick: VendorTick,
        divergence_pct: float,
        age_gap: float,
        comparable: bool,
    ) -> ArbitratedTickResult:
        """Feeds disagree beyond tolerance and no vendor is currently quarantined."""
        state.clean_comparisons = 0

        # 1. Evidence first: a frozen feed is attributable without any policy choice.
        frozen = self._detect_frozen_vendor(state, now)
        if frozen is not None:
            surviving = self._other(frozen)
            surviving_tick = p_tick if surviving == PRIMARY else s_tick
            state.quarantined_vendor = frozen
            state.quarantine_status = VendorStatus.FROZEN_PRICE
            state.quarantined_at = now
            state.divergence_started_at = None
            statuses = {surviving: VendorStatus.HEALTHY, frozen: VendorStatus.FROZEN_PRICE}
            result = ArbitratedTickResult(
                symbol=symbol,
                consensus_price=surviving_tick.price,
                is_arbitrated=True,
                primary_vendor_status=statuses[PRIMARY],
                secondary_vendor_status=statuses[SECONDARY],
                relative_divergence_pct=round(divergence_pct, 6),
                active_vendor=surviving,
                message=(
                    f"{frozen.capitalize()} feed frozen: price unchanged for >= "
                    f"{self.frozen_price_seconds}s while {surviving} moved, divergence "
                    f"{divergence_pct:.4f}%. Quarantined {frozen}; pricing from {surviving}."
                ),
                decision=ArbitrationDecision.QUARANTINE_ACTIVE,
                is_trusted=True,
                quarantined_vendor=frozen,
                feed_age_gap_seconds=age_gap,
            )
            self._log_transition(symbol, state, result, now)
            return result

        # 2. Non-simultaneous observations: the disagreement is explainable by the price
        #    having moved between them, so no vendor may be blamed. The freshest price is
        #    the best estimate available, but nothing has verified it.
        if not comparable:
            # No evidence either way, so the confirmation window keeps running rather
            # than restarting: alternating vendor ticks are routinely further apart than
            # max_comparison_age_seconds, and restarting here would mean a persistent
            # divergence never reaches confirmation on anything but the most liquid names.
            fresher_key = PRIMARY if p_tick.timestamp >= s_tick.timestamp else SECONDARY
            fresher = p_tick if fresher_key == PRIMARY else s_tick
            result = ArbitratedTickResult(
                symbol=symbol,
                consensus_price=fresher.price,
                is_arbitrated=True,
                primary_vendor_status=VendorStatus.HEALTHY,
                secondary_vendor_status=VendorStatus.HEALTHY,
                relative_divergence_pct=round(divergence_pct, 6),
                active_vendor=fresher_key,
                message=(
                    f"Divergence {divergence_pct:.4f}% between observations {age_gap * 1000:.1f}ms "
                    f"apart (> {self.max_comparison_age_seconds * 1000:.0f}ms): not attributable to a "
                    f"vendor. Emitting freshest feed ({fresher_key}) as UNVERIFIED."
                ),
                decision=ArbitrationDecision.LATENCY_SKEW_UNVERIFIED,
                is_trusted=False,
                feed_age_gap_seconds=age_gap,
            )
            self._log_transition(symbol, state, result, now)
            return result

        # 3. Simultaneous and genuinely disagreeing. Hold before blaming anyone: a fast
        #    market produces exactly this signature for as long as one feed leads.
        if state.divergence_started_at is None:
            state.divergence_started_at = now
        episode_seconds = now - state.divergence_started_at

        if episode_seconds < self.divergence_confirmation_seconds:
            reference_tick = p_tick if self.reference_vendor == PRIMARY else s_tick
            statuses = {
                PRIMARY: VendorStatus.DIVERGENT_UNCONFIRMED,
                SECONDARY: VendorStatus.DIVERGENT_UNCONFIRMED,
            }
            result = ArbitratedTickResult(
                symbol=symbol,
                consensus_price=reference_tick.price,
                is_arbitrated=True,
                primary_vendor_status=statuses[PRIMARY],
                secondary_vendor_status=statuses[SECONDARY],
                relative_divergence_pct=round(divergence_pct, 6),
                active_vendor=self.reference_vendor,
                message=(
                    f"Divergence {divergence_pct:.4f}% > {self.max_divergence_pct}% on simultaneous "
                    f"observations, unattributed after {episode_seconds:.3f}s of "
                    f"{self.divergence_confirmation_seconds}s confirmation window. Emitting "
                    f"{self.reference_vendor} price as UNTRUSTED."
                ),
                decision=ArbitrationDecision.DIVERGENCE_UNRESOLVED,
                is_trusted=False,
                feed_age_gap_seconds=age_gap,
            )
            self._log_transition(symbol, state, result, now)
            return result

        # 4. Confirmation window elapsed with no distinguishing evidence. Falling back to
        #    the reference vendor is an operator POLICY: with two sources and no third
        #    reference, the outlier is not identifiable from prices.
        outlier = self._other(self.reference_vendor)
        reference_tick = p_tick if self.reference_vendor == PRIMARY else s_tick
        state.quarantined_vendor = outlier
        state.quarantine_status = VendorStatus.DIVERGENT_OUTLIER
        state.quarantined_at = now
        state.divergence_started_at = None
        statuses = {self.reference_vendor: VendorStatus.HEALTHY, outlier: VendorStatus.DIVERGENT_OUTLIER}
        result = ArbitratedTickResult(
            symbol=symbol,
            consensus_price=reference_tick.price,
            is_arbitrated=True,
            primary_vendor_status=statuses[PRIMARY],
            secondary_vendor_status=statuses[SECONDARY],
            relative_divergence_pct=round(divergence_pct, 6),
            active_vendor=self.reference_vendor,
            message=(
                f"Divergence {divergence_pct:.4f}% persisted {episode_seconds:.3f}s >= "
                f"{self.divergence_confirmation_seconds}s with no attributing evidence. POLICY "
                f"fallback to reference vendor {self.reference_vendor}; {outlier} quarantined. "
                f"This is a configured preference, not outlier detection."
            ),
            decision=ArbitrationDecision.QUARANTINE_ACTIVE,
            is_trusted=True,
            quarantined_vendor=outlier,
            feed_age_gap_seconds=age_gap,
        )
        self._log_transition(symbol, state, result, now)
        return result

    # ------------------------------------------------------------- observability

    def snapshot(self) -> List[Tuple[str, Optional[str], Optional[float], Optional[float]]]:
        """Return ``(symbol, quarantined_vendor, primary_ts, secondary_ts)`` per symbol.

        For health dashboards and post-incident forensics; does not mutate state.
        """
        with self._lock:
            rows: List[Tuple[str, Optional[str], Optional[float], Optional[float]]] = []
            for sym, state in sorted(self._symbols.items()):
                p = state.vendors[PRIMARY].last_tick
                s = state.vendors[SECONDARY].last_tick
                rows.append(
                    (sym, state.quarantined_vendor, p.timestamp if p else None, s.timestamp if s else None)
                )
            return rows
