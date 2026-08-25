"""
graduated-response-to-data-quality-degradation: graduated de-risking engine driven by
market-data quality telemetry.

Converts per-symbol feed-quality metrics (staleness, sequence gaps, price-spike
anomalies, crossed books, spread blow-out) into a bounded quality score and a
four-tier trading mandate:

    Q >= 90  -> TIER_0_NORMAL                 full size
    70 <= Q  -> TIER_1_MINOR_DEGRADATION      new entries at 50% size
    40 <= Q  -> TIER_2_MODERATE_DEGRADATION   no new entries, exits still allowed
    Q <  40  -> TIER_3_SEVERE_OUTAGE          cancel resting orders and flatten

Design rules this module follows, and why:

- **Fail closed on corrupt telemetry.** A metric that is NaN, infinite or negative is
  itself evidence that the feed handler or the collector is broken. Such a metric is
  scored 0.0 and forced to Tier 3 rather than skipped. A naive implementation compares
  ``NaN > 1.0`` (always False), so a NaN staleness produces a perfect score and
  ``ALLOW_FULL_TRADING`` -- the worst possible answer to the worst possible input.
- **Configuration errors raise; runtime telemetry degrades.** Bad constructor
  parameters raise at construction. Bad per-tick metrics degrade the mandate, because
  raising inside the tick loop of a live feed handler tends to be swallowed.
- **Classification uses the exact score; only the reported score is rounded.**
  Rounding to 2dp *before* the comparison lets a true score of 89.996 round up to
  90.00 and win ``ALLOW_FULL_TRADING``. The reported score is floored to 2dp so a
  displayed number can never overstate quality relative to the tier assigned.
- **Sizing factor applies to new entries only.** ``position_sizing_factor`` is 0.0 at
  both Tier 2 and Tier 3, so it cannot distinguish "block entries, keep exiting" from
  "flatten now". A caller that multiplies *every* order quantity by it would suppress
  the exits Tier 2 explicitly permits. The actionable outputs are the four booleans
  ``allow_new_entries`` / ``allow_risk_reducing_exits`` / ``cancel_resting_orders`` /
  ``flatten_positions``; the float is an entry-size multiplier and nothing else.
  ``cancel_resting_entry_orders`` is deliberately named: a resting stop-loss is a
  risk-*reducing* order and cancelling it removes protection at the exact moment the
  feed is degraded. Only orders that would open or increase exposure are in scope.
- **Boolean flags must be real booleans.** ``None`` is the value a collector emits for
  "not measured", and truthiness would silently read it as "clean". A non-``bool`` flag
  is treated as corrupt telemetry, not as ``False``.
- **Escalate fast, recover slow.** With ``recovery_hold_seconds`` set, a worse tier
  applies on the tick that produces it, while an improvement only applies once the
  better quality has persisted for the hold period. Without it the engine is
  memoryless and one transient bad tick can trigger an emergency flatten that is
  released on the next tick -- the flapping this skill exists to avoid.

Limitations (documented, deliberate):

- **Thresholds and penalties are engineering defaults, not standards.** No regulator,
  exchange or standards body publishes a market-data quality score, a tier boundary or
  a penalty weight. See ``references/standards.md``. Calibrate every number against
  your own feed's measured behaviour before trading on it.
- **The engine cannot detect its own absence.** If the collector stops calling
  ``audit_and_de_risk`` altogether, no report is produced and no tier changes. A
  heartbeat on the auditor itself is the caller's responsibility.
- **Defaulted metrics are optimistic.** ``missing_sequence_count=0``,
  ``price_spike_anomaly_detected=False``, ``crossed_book_detected=False`` and
  ``bid_ask_spread_multiplier=1.0`` all mean "measured, and clean". A collector that
  does not compute a given check leaves that penalty permanently at zero.
  ``stale_time_seconds`` has deliberately no default for this reason: liveness must
  always be measured.
- **Crossed only, not locked.** ``crossed_book_detected`` covers bid > ask. A *locked*
  book (bid == ask) is not in the metric set and is not scored.
- **Single process.** State is in-memory and guarded by a lock. Two processes each
  running an engine keep two independent recovery timers, not one shared timer.
- **Flattening uses the feed you just distrusted.** Tier 3 is a mandate, not an
  execution policy. See ``references/workflows.md``.
"""
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Tier lower bounds on the quality score, as percentages. Engineering defaults.
DEFAULT_TIER_0_MIN_SCORE = 90.0
DEFAULT_TIER_1_MIN_SCORE = 70.0
DEFAULT_TIER_2_MIN_SCORE = 40.0

#: Staleness (seconds) tolerated before the staleness penalty starts accruing.
DEFAULT_STALE_GRACE_SECONDS = 1.0

#: Spread blow-out (multiple of normal spread) tolerated before penalty accrual.
DEFAULT_SPREAD_GRACE_MULTIPLE = 2.0

#: Penalty weights, in score points. Engineering defaults.
DEFAULT_STALE_PENALTY_PER_SECOND = 10.0
DEFAULT_MISSING_SEQUENCE_PENALTY_EACH = 2.0
DEFAULT_PRICE_SPIKE_PENALTY = 25.0
DEFAULT_CROSSED_BOOK_PENALTY = 50.0
DEFAULT_SPREAD_PENALTY_PER_MULTIPLE = 15.0

#: New-entry size multiplier applied at Tier 1.
DEFAULT_TIER_1_SIZE_FACTOR = 0.50

_TIER_NAMES = {
    0: "TIER_0_NORMAL",
    1: "TIER_1_MINOR_DEGRADATION",
    2: "TIER_2_MODERATE_DEGRADATION",
    3: "TIER_3_SEVERE_OUTAGE",
}

_TIER_ACTIONS = {
    0: "ALLOW_FULL_TRADING",
    1: "REDUCE_SIZE_50_PCT",
    2: "BLOCK_NEW_ENTRIES",
    3: "EMERGENCY_HALT_AND_FLATTEN",
}


@dataclass
class DataQualityMetrics:
    """One quality observation for one symbol, produced by the feed-quality collector.

    ``stale_time_seconds`` has no default: it is the liveness measurement, and an
    object constructed without it would otherwise describe a perfect feed.
    """

    symbol: str
    stale_time_seconds: float           # Age of the most recent accepted tick, seconds.
    missing_sequence_count: int = 0     # Sequence numbers detected missing in the window.
    price_spike_anomaly_detected: bool = False
    crossed_book_detected: bool = False      # Bid strictly above ask on the same instrument.
    bid_ask_spread_multiplier: float = 1.0   # Current spread / normal spread. 1.0 = normal.


@dataclass
class DataQualityDeRiskReport:
    """The mandate emitted for one observation.

    The four booleans are the actionable outputs. ``position_sizing_factor`` is a
    multiplier for **new entry** quantity only and must never be applied to a
    risk-reducing exit: it is 0.0 at both Tier 2 and Tier 3, and Tier 2 permits exits.
    """

    symbol: str
    data_quality_score_pct: float       # Effective score, floored to 2dp, in [0, 100].
    de_risking_tier: int                # 0 Normal, 1 Minor, 2 Moderate, 3 Severe.
    tier_name: str
    action_mandate: str
    position_sizing_factor: float       # New-entry multiplier: 1.0 / 0.50 / 0.0 / 0.0.
    audit_notes: str
    allow_new_entries: bool = False
    allow_risk_reducing_exits: bool = True
    cancel_resting_orders: bool = False
    flatten_positions: bool = False
    penalty_breakdown: Dict[str, float] = field(default_factory=dict)
    triggered_conditions: List[str] = field(default_factory=list)
    instantaneous_tier: int = 0          # Tier implied by this observation alone.
    tier_held_by_recovery: bool = False  # True when a de-escalation is being withheld.
    metrics_valid: bool = True           # False when a metric was NaN/inf/out of domain.


def _is_bad_number(value: object) -> bool:
    """True when ``value`` is not a real, finite number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return True
    return not math.isfinite(float(value))


def _floor_2dp(value: float) -> float:
    """Floor to two decimals so a displayed score never overstates the tier assigned."""
    return math.floor(value * 100.0) / 100.0


@dataclass
class _SymbolState:
    """Held tier and de-escalation timer for one symbol."""

    held_tier: int
    improving_since: Optional[float] = None


class DataQualityDeRiskerEngine:
    """Graduated de-risking engine driven by market-data quality telemetry.

    Thread-safe within one process. Every threshold and penalty is a constructor
    parameter because none of them is set by an external standard.

    Args:
        tier_0_min_score: Lower bound of Tier 0 (a score >= this bound is Tier 0).
        tier_1_min_score: Lower bound of Tier 1. Must be strictly below ``tier_0_min_score``.
        tier_2_min_score: Lower bound of Tier 2. Must be strictly below ``tier_1_min_score``.
        stale_grace_seconds: Staleness tolerated before the staleness penalty accrues.
        stale_penalty_per_second: Score points removed per second beyond the grace.
        missing_sequence_penalty_each: Score points removed per missing sequence number.
        price_spike_penalty: Score points removed when a spike anomaly is flagged.
        crossed_book_penalty: Score points removed when the book is crossed.
        spread_grace_multiple: Spread multiple tolerated before the spread penalty accrues.
        spread_penalty_per_multiple: Score points removed per multiple beyond the grace.
        tier_1_size_factor: New-entry size multiplier at Tier 1, in (0, 1].
        recovery_hold_seconds: How long improved quality must persist before the engine
            de-escalates. ``0.0`` disables the hold and makes the engine memoryless.
        clock: Monotonic time source, injected for testability. A wall clock lets an
            NTP step shorten or extend a recovery hold.

    Raises:
        ValueError: on any invalid configuration parameter.
    """

    def __init__(
        self,
        tier_0_min_score: float = DEFAULT_TIER_0_MIN_SCORE,
        tier_1_min_score: float = DEFAULT_TIER_1_MIN_SCORE,
        tier_2_min_score: float = DEFAULT_TIER_2_MIN_SCORE,
        stale_grace_seconds: float = DEFAULT_STALE_GRACE_SECONDS,
        stale_penalty_per_second: float = DEFAULT_STALE_PENALTY_PER_SECOND,
        missing_sequence_penalty_each: float = DEFAULT_MISSING_SEQUENCE_PENALTY_EACH,
        price_spike_penalty: float = DEFAULT_PRICE_SPIKE_PENALTY,
        crossed_book_penalty: float = DEFAULT_CROSSED_BOOK_PENALTY,
        spread_grace_multiple: float = DEFAULT_SPREAD_GRACE_MULTIPLE,
        spread_penalty_per_multiple: float = DEFAULT_SPREAD_PENALTY_PER_MULTIPLE,
        tier_1_size_factor: float = DEFAULT_TIER_1_SIZE_FACTOR,
        recovery_hold_seconds: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        bound_names = ("tier_0_min_score", "tier_1_min_score", "tier_2_min_score")
        bounds = (tier_0_min_score, tier_1_min_score, tier_2_min_score)
        for name, value in zip(bound_names, bounds):
            if _is_bad_number(value) or not 0.0 < float(value) <= 100.0:
                raise ValueError(f"{name} must be a finite number in (0, 100], got {value!r}")
        if not tier_0_min_score > tier_1_min_score > tier_2_min_score:
            raise ValueError(
                "tier bounds must be strictly descending: "
                f"tier_0_min_score ({tier_0_min_score}) > tier_1_min_score "
                f"({tier_1_min_score}) > tier_2_min_score ({tier_2_min_score})"
            )

        penalties = {
            "stale_penalty_per_second": stale_penalty_per_second,
            "missing_sequence_penalty_each": missing_sequence_penalty_each,
            "price_spike_penalty": price_spike_penalty,
            "crossed_book_penalty": crossed_book_penalty,
            "spread_penalty_per_multiple": spread_penalty_per_multiple,
            "stale_grace_seconds": stale_grace_seconds,
            "spread_grace_multiple": spread_grace_multiple,
        }
        for name, value in penalties.items():
            if _is_bad_number(value) or float(value) < 0.0:
                raise ValueError(f"{name} must be a finite number >= 0, got {value!r}")

        if _is_bad_number(tier_1_size_factor) or not 0.0 < float(tier_1_size_factor) <= 1.0:
            raise ValueError(
                f"tier_1_size_factor must be a finite number in (0, 1], "
                f"got {tier_1_size_factor!r}"
            )
        if _is_bad_number(recovery_hold_seconds) or float(recovery_hold_seconds) < 0.0:
            raise ValueError(
                f"recovery_hold_seconds must be a finite number >= 0, "
                f"got {recovery_hold_seconds!r}"
            )
        if not callable(clock):
            raise ValueError("clock must be a callable returning a monotonic float")

        self.tier_0_min_score = float(tier_0_min_score)
        self.tier_1_min_score = float(tier_1_min_score)
        self.tier_2_min_score = float(tier_2_min_score)
        self.stale_grace_seconds = float(stale_grace_seconds)
        self.stale_penalty_per_second = float(stale_penalty_per_second)
        self.missing_sequence_penalty_each = float(missing_sequence_penalty_each)
        self.price_spike_penalty = float(price_spike_penalty)
        self.crossed_book_penalty = float(crossed_book_penalty)
        self.spread_grace_multiple = float(spread_grace_multiple)
        self.spread_penalty_per_multiple = float(spread_penalty_per_multiple)
        self.tier_1_size_factor = float(tier_1_size_factor)
        self.recovery_hold_seconds = float(recovery_hold_seconds)

        self._clock = clock
        self._lock = threading.RLock()
        self._state: Dict[str, _SymbolState] = {}

    # ---------------------------------------------------------------- scoring

    def _score(
        self, metrics: DataQualityMetrics
    ) -> Tuple[float, Dict[str, float], List[str], bool]:
        """Score one observation.

        Returns:
            ``(exact_score, penalty_breakdown, triggered_conditions, metrics_valid)``.
            ``exact_score`` is clamped to [0, 100] but not rounded, because rounding
            before the tier comparison can promote a sub-threshold score across a
            boundary in the direction of more trading.
        """
        breakdown: Dict[str, float] = {}
        triggered: List[str] = []
        invalid: List[str] = []

        stale = metrics.stale_time_seconds
        if _is_bad_number(stale) or float(stale) < 0.0:
            invalid.append("stale_time_seconds")
        elif float(stale) > self.stale_grace_seconds:
            breakdown["stale_data"] = (
                float(stale) - self.stale_grace_seconds
            ) * self.stale_penalty_per_second
            triggered.append(f"STALE_FEED:{float(stale):.3f}s")

        missing = metrics.missing_sequence_count
        if isinstance(missing, bool) or not isinstance(missing, int) or missing < 0:
            invalid.append("missing_sequence_count")
        elif missing > 0:
            breakdown["missing_sequences"] = missing * self.missing_sequence_penalty_each
            triggered.append(f"SEQUENCE_GAP:{missing}")

        if not isinstance(metrics.price_spike_anomaly_detected, bool):
            invalid.append("price_spike_anomaly_detected")
        elif metrics.price_spike_anomaly_detected:
            breakdown["price_spike"] = self.price_spike_penalty
            triggered.append("PRICE_SPIKE_ANOMALY")

        if not isinstance(metrics.crossed_book_detected, bool):
            invalid.append("crossed_book_detected")
        elif metrics.crossed_book_detected:
            breakdown["crossed_book"] = self.crossed_book_penalty
            triggered.append("CROSSED_BOOK")

        spread = metrics.bid_ask_spread_multiplier
        if _is_bad_number(spread) or float(spread) <= 0.0:
            invalid.append("bid_ask_spread_multiplier")
        elif float(spread) > self.spread_grace_multiple:
            breakdown["wide_spread"] = (
                float(spread) - self.spread_grace_multiple
            ) * self.spread_penalty_per_multiple
            triggered.append(f"SPREAD_BLOWOUT:{float(spread):.2f}x")

        if invalid:
            # A metric that is NaN, infinite, negative or of the wrong type means the
            # collector or the feed handler is broken. Score it as a total outage
            # rather than skipping the penalty, which would score it as clean.
            for name in invalid:
                triggered.append(f"INVALID_METRIC:{name}")
            return 0.0, breakdown, triggered, False

        exact = 100.0 - sum(breakdown.values())
        return max(0.0, min(100.0, exact)), breakdown, triggered, True

    def _classify(self, exact_score: float) -> int:
        """Map an exact (unrounded) score to a tier index."""
        if exact_score >= self.tier_0_min_score:
            return 0
        if exact_score >= self.tier_1_min_score:
            return 1
        if exact_score >= self.tier_2_min_score:
            return 2
        return 3

    def _apply_recovery_hold(self, symbol: str, instantaneous_tier: int) -> Tuple[int, bool]:
        """Escalate immediately, de-escalate only after sustained improvement.

        Returns ``(effective_tier, held)`` where ``held`` is True when the reported
        tier is worse than the instantaneous tier because the hold has not elapsed.
        """
        if self.recovery_hold_seconds <= 0.0:
            return instantaneous_tier, False

        now = self._clock()
        with self._lock:
            state = self._state.get(symbol)
            if state is None:
                self._state[symbol] = _SymbolState(held_tier=instantaneous_tier)
                return instantaneous_tier, False

            if instantaneous_tier >= state.held_tier:
                # Same or worse: apply now and cancel any pending recovery.
                state.held_tier = instantaneous_tier
                state.improving_since = None
                return instantaneous_tier, False

            if state.improving_since is None:
                state.improving_since = now
            elif now - state.improving_since >= self.recovery_hold_seconds:
                state.held_tier = instantaneous_tier
                state.improving_since = None
                return instantaneous_tier, False
            return state.held_tier, True

    # ----------------------------------------------------------------- public

    def audit_and_de_risk(self, metrics: DataQualityMetrics) -> DataQualityDeRiskReport:
        """Score one observation and return the graduated de-risking mandate.

        Args:
            metrics: One quality observation for one symbol.

        Returns:
            The mandate. Read the booleans, not only ``position_sizing_factor``.

        Raises:
            TypeError: if ``metrics`` is not a :class:`DataQualityMetrics`.
            ValueError: if ``metrics.symbol`` is empty or not a string. Corrupt
                *numeric* telemetry does not raise; it forces Tier 3.
        """
        if not isinstance(metrics, DataQualityMetrics):
            raise TypeError(
                f"metrics must be a DataQualityMetrics, got {type(metrics).__name__}"
            )
        if not isinstance(metrics.symbol, str) or not metrics.symbol.strip():
            raise ValueError("metrics.symbol must be a non-empty string")

        symbol = metrics.symbol
        exact_score, breakdown, triggered, metrics_valid = self._score(metrics)
        instantaneous_tier = self._classify(exact_score)
        tier, held = self._apply_recovery_hold(symbol, instantaneous_tier)

        reported_score = _floor_2dp(exact_score)
        factor = {0: 1.0, 1: self.tier_1_size_factor, 2: 0.0, 3: 0.0}[tier]
        reasons = ", ".join(triggered) if triggered else "no defects detected"
        hold_note = (
            f" Recovery hold active: instantaneous tier {instantaneous_tier} withheld "
            f"for {self.recovery_hold_seconds:.1f}s of sustained improvement."
            if held
            else ""
        )

        if tier == 0:
            notes = (
                f"DATA QUALITY NORMAL [{symbol}]: Score = {reported_score:.2f}%. "
                f"Full trading permitted. Reasons: {reasons}.{hold_note}"
            )
            logger.info(notes)
        elif tier == 1:
            notes = (
                f"DATA QUALITY MINOR DEGRADATION [{symbol}]: Score = {reported_score:.2f}%. "
                f"New entries at {self.tier_1_size_factor:.0%} size; exits unrestricted. "
                f"Reasons: {reasons}.{hold_note}"
            )
            logger.warning(notes)
        elif tier == 2:
            notes = (
                f"DATA QUALITY MODERATE DEGRADATION [{symbol}]: Score = {reported_score:.2f}%. "
                f"New entries BLOCKED; risk-reducing exits still permitted. "
                f"Reasons: {reasons}.{hold_note}"
            )
            logger.warning(notes)
        else:
            notes = (
                f"DATA QUALITY SEVERE OUTAGE [{symbol}]: Score = {reported_score:.2f}%. "
                f"EMERGENCY HALT: cancel resting orders and flatten. "
                f"Reasons: {reasons}.{hold_note}"
            )
            logger.critical(notes)

        return DataQualityDeRiskReport(
            symbol=symbol,
            data_quality_score_pct=reported_score,
            de_risking_tier=tier,
            tier_name=_TIER_NAMES[tier],
            action_mandate=_TIER_ACTIONS[tier],
            position_sizing_factor=factor,
            audit_notes=notes,
            allow_new_entries=tier <= 1,
            allow_risk_reducing_exits=True,
            cancel_resting_orders=tier >= 2,
            flatten_positions=tier == 3,
            penalty_breakdown=dict(breakdown),
            triggered_conditions=list(triggered),
            instantaneous_tier=instantaneous_tier,
            tier_held_by_recovery=held,
            metrics_valid=metrics_valid,
        )

    def reset(self, symbol: Optional[str] = None) -> None:
        """Clear recovery-hold state, for use after an operator has remediated a feed.

        Args:
            symbol: The symbol to clear, or ``None`` to clear every symbol.
        """
        with self._lock:
            if symbol is None:
                self._state.clear()
                logger.warning("Data quality recovery-hold state cleared for all symbols.")
            else:
                self._state.pop(symbol, None)
                logger.warning("Data quality recovery-hold state cleared for %s.", symbol)

    def held_tier(self, symbol: str) -> Optional[int]:
        """Return the tier currently held for ``symbol``, or ``None`` if untracked."""
        with self._lock:
            state = self._state.get(symbol)
            return None if state is None else state.held_tier
