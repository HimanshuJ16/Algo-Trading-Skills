import logging
import math
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Status values returned on TuningReport.status
STATUS_DIRECT_PASS = "DIRECT_PASS_NO_TUNING_REQUIRED"
STATUS_TUNING_APPLIED = "RATE_LIMIT_TUNING_APPLIED"
STATUS_TARGET_UNREACHABLE = "RATE_LIMIT_TARGET_UNREACHABLE"

# Absolute tolerance for message-rate comparisons (MPS), guarding float artifacts only.
_RATE_TOLERANCE_MPS = 1e-9


def _require_positive_finite(value: float, label: str) -> float:
    """Rejects NaN/Inf/non-positive input that would silently corrupt rate arithmetic."""
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be a finite number (got {value!r}).")
    if numeric <= 0.0:
        raise ValueError(f"{label} must be strictly positive (got {numeric}).")
    return numeric


def _require_non_negative_finite(value: float, label: str) -> float:
    """Rejects NaN/Inf/negative input. Zero is allowed (e.g. a quiet, zero-tick symbol)."""
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be a finite number (got {value!r}).")
    if numeric < 0.0:
        raise ValueError(f"{label} must be non-negative (got {numeric}).")
    return numeric


def _ceil_2dp(value: float) -> float:
    """Rounds up to 2 decimal places.

    Throttle delays are always rounded UP, never to-nearest: rounding a delay down
    raises the resulting message rate back above the target safety limit.
    """
    return math.ceil(value * 100.0 - _RATE_TOLERANCE_MPS) / 100.0


@dataclass
class TuningConfig:
    """Operator-supplied rate-limit budget and repricing bounds for one quoting session.

    `exchange_max_mps` must be sourced from the venue's own session configuration
    (see references/standards.md); it is not a universal constant. Where a venue
    publishes both a reject and a terminate threshold, set it to the lower (reject)
    threshold. `price_threshold_bps` is an INPUT consumed by the quoting engine --
    this module does not derive an optimal threshold.
    """
    symbol: str
    exchange_max_mps: float = 500.0      # Venue-published message ceiling for THIS session
    target_safety_buffer_pct: float = 80.0  # Share of the ceiling this engine may consume; (0, 100]
    min_reprice_delay_ms: float = 1.0    # Hardware minimum reprice delay
    max_reprice_delay_ms: float = 500.0  # Upper bound delay cap (staleness limit)
    price_threshold_bps: float = 2.0     # Minimum price movement required to trigger quote update

    def __post_init__(self) -> None:
        if not self.symbol or not str(self.symbol).strip():
            raise ValueError("Symbol must be a non-empty string.")
        self.symbol = str(self.symbol)
        self.exchange_max_mps = _require_positive_finite(
            self.exchange_max_mps, "Exchange max MPS limit")
        self.target_safety_buffer_pct = _require_positive_finite(
            self.target_safety_buffer_pct, "Target safety buffer pct")
        if self.target_safety_buffer_pct > 100.0:
            raise ValueError(
                "Target safety buffer pct must be <= 100.0; targeting a rate above the "
                f"exchange ceiling provides no headroom (got {self.target_safety_buffer_pct})."
            )
        self.min_reprice_delay_ms = _require_positive_finite(
            self.min_reprice_delay_ms, "Min reprice delay ms")
        self.max_reprice_delay_ms = _require_positive_finite(
            self.max_reprice_delay_ms, "Max reprice delay ms")
        if self.max_reprice_delay_ms < self.min_reprice_delay_ms:
            raise ValueError(
                f"Max reprice delay ({self.max_reprice_delay_ms}ms) must be >= "
                f"min reprice delay ({self.min_reprice_delay_ms}ms)."
            )
        self.price_threshold_bps = _require_non_negative_finite(
            self.price_threshold_bps, "Price threshold bps")


@dataclass
class MarketState:
    """Observed microstructure state driving the message-rate projection.

    `active_quoting_pairs` counts resting quote sides, assuming ONE outbound message
    per side per reprice cycle (cancel/replace-style amendment). Strategies or venues
    that send a separate cancel plus a separate new order emit two messages per side
    and must double this value.
    """
    ticks_per_sec: float                # Microstructure tick arrival frequency
    price_volatility_bps: float         # Short-horizon realised price volatility in basis points
    active_quoting_pairs: int = 2       # Number of active bid/ask order pairs (e.g. 2 = 1 bid + 1 ask)
    # Messages per second already consumed on the SAME venue session by other flow
    # (hedges, cancels, other strategies, administrative traffic). MiFID II RTS 6
    # Art. 15(2) requires all orders sent to a venue to count against the firm's
    # pre-trade message limit, so this is deducted before the delay is derived.
    baseline_session_mps: float = 0.0

    def __post_init__(self) -> None:
        self.ticks_per_sec = _require_non_negative_finite(
            self.ticks_per_sec, "Ticks per sec")
        self.price_volatility_bps = _require_non_negative_finite(
            self.price_volatility_bps, "Price volatility bps")
        if isinstance(self.active_quoting_pairs, bool) or int(self.active_quoting_pairs) != self.active_quoting_pairs:
            raise ValueError(
                f"Active quoting pairs must be a whole number (got {self.active_quoting_pairs!r}).")
        self.active_quoting_pairs = int(self.active_quoting_pairs)
        if self.active_quoting_pairs <= 0:
            raise ValueError("Active quoting pairs must be positive.")
        self.baseline_session_mps = _require_non_negative_finite(
            self.baseline_session_mps, "Baseline session MPS")


@dataclass
class TuningReport:
    """Structured audit output of one tuning pass. All rates are messages per second."""
    symbol: str
    # Quote-repricing rate with no throttle applied, EXCLUDING baseline_session_mps.
    unthrottled_message_rate_mps: float
    target_safety_limit_mps: float
    recommended_reprice_delay_ms: float
    # Total projected session rate: throttled quote traffic PLUS baseline_session_mps.
    projected_message_rate_mps: float
    # Ordinal staleness heuristic (delay_ms * volatility_bps), NOT a currency cost.
    adverse_selection_exposure_score: float
    is_tuning_required: bool
    status: str                         # One of the STATUS_* constants above
    audit_notes: str
    baseline_session_mps: float = 0.0
    # False => the target rate CANNOT be met under the configured delay ceiling.
    # Callers MUST NOT deploy these parameters while this is False.
    is_target_achievable: bool = True


class MessageRateLatencyTunerEngine:
    """
    Quantitative rate-limit vs adverse selection latency tuning engine,
    dynamically balancing quote repricing delays against exchange messages-per-second (MPS) ceilings.

    Model and assumptions (full derivation in references/workflows.md):
      * Steady-state model: the message rate is treated as an average over the venue's
        measurement window. It does not model sub-window bursts.
      * One outbound message per quoting side per reprice cycle.
      * Guarantee: whenever `is_target_achievable` is True, the reported
        `projected_message_rate_mps` is <= `target_safety_limit_mps`.
    """

    def __init__(self) -> None:
        pass

    def tune_quote_reprice_parameters(
        self,
        config: TuningConfig,
        state: MarketState
    ) -> TuningReport:
        """
        Calculates unthrottled message velocity, determines optimal quote reprice delay, and audits adverse selection.

        Inputs are validated when `TuningConfig` / `MarketState` are constructed, so a
        malformed budget raises `ValueError` there rather than producing a report that
        silently approves an unsafe message rate.
        """
        symbol = config.symbol
        pairs = state.active_quoting_pairs
        baseline_mps = state.baseline_session_mps

        # 1. Target Safety Limit (MPS)
        target_mps = round(config.exchange_max_mps * (config.target_safety_buffer_pct / 100.0), 2)

        # 2. Unthrottled Message Velocity (MPS)
        # Every tick triggers reprice messages for all active quoting pairs
        unthrottled_mps = round(state.ticks_per_sec * pairs, 2)

        # Budget left for quote repricing once other flow on the same session is counted
        # (MiFID II RTS 6 Art. 15(2): all orders sent to the venue count toward the limit).
        available_mps = target_mps - baseline_mps

        is_tuning_req = (unthrottled_mps + baseline_mps) > (target_mps + _RATE_TOLERANCE_MPS)

        # 3. Optimal Reprice Delay Calculation (ms)
        # Required delay to restrict quote traffic to available_mps:
        # Delay (sec) = ActiveQuotingPairs / available_mps
        # Delay (ms) = (ActiveQuotingPairs / available_mps) * 1000.0
        # Rounded UP so the throttle is never looser than the budget permits.
        if not is_tuning_req:
            optimal_delay_ms = config.min_reprice_delay_ms
        elif available_mps <= _RATE_TOLERANCE_MPS:
            # Other flow alone already consumes the whole safety budget: no repricing
            # delay can bring the session under target. Report the slowest quote allowed.
            optimal_delay_ms = config.max_reprice_delay_ms
        else:
            required_delay_ms = _ceil_2dp((pairs / available_mps) * 1000.0)
            optimal_delay_ms = min(
                max(config.min_reprice_delay_ms, required_delay_ms), config.max_reprice_delay_ms
            )

        # 4. Projected MPS with Tuned Reprice Delay
        # The throttle caps quote traffic at pairs / delay_sec, but cannot manufacture
        # ticks, so the realised rate is the lesser of the cap and the arrival rate.
        throttle_cap_mps = pairs / (optimal_delay_ms / 1000.0)
        exact_projected_mps = min(unthrottled_mps, throttle_cap_mps) + baseline_mps
        projected_mps = round(exact_projected_mps, 2)

        # 5. Adverse Selection Exposure Score (Delay_ms * Volatility_bps)
        # Ordinal staleness heuristic only -- see SKILL.md "Common Pitfalls".
        exposure_score = round(optimal_delay_ms * state.price_volatility_bps, 2)

        is_target_achievable = exact_projected_mps <= (target_mps + _RATE_TOLERANCE_MPS)

        if not is_tuning_req:
            status = STATUS_DIRECT_PASS
            notes = (
                f"DIRECT PASS [{symbol}]: Unthrottled rate {unthrottled_mps:.0f} MPS "
                f"(+ {baseline_mps:.0f} MPS baseline session flow) is within "
                f"target safety limit ({target_mps:.0f} MPS). Retained min delay = "
                f"{config.min_reprice_delay_ms:.1f}ms."
            )
            logger.info(notes)
        elif is_target_achievable:
            status = STATUS_TUNING_APPLIED
            notes = (
                f"RATE LIMIT TUNING APPLIED [{symbol}]: Unthrottled rate {unthrottled_mps:.0f} MPS "
                f"(+ {baseline_mps:.0f} MPS baseline session flow) exceeds target safety limit "
                f"({target_mps:.0f} MPS). Tuned reprice delay from "
                f"{config.min_reprice_delay_ms:.1f}ms to {optimal_delay_ms:.1f}ms "
                f"(Projected Rate = {projected_mps:.0f} MPS, Adverse Exposure Score = {exposure_score:.1f})."
            )
            logger.info(notes)
        else:
            status = STATUS_TARGET_UNREACHABLE
            overage_mps = round(exact_projected_mps - target_mps, 2)
            notes = (
                f"RATE LIMIT TARGET UNREACHABLE [{symbol}]: Even at the maximum permitted reprice "
                f"delay ({optimal_delay_ms:.1f}ms), projected rate {projected_mps:.0f} MPS exceeds "
                f"the target safety limit ({target_mps:.0f} MPS) by {overage_mps:.0f} MPS "
                f"(exchange ceiling {config.exchange_max_mps:.0f} MPS, baseline session flow "
                f"{baseline_mps:.0f} MPS). DO NOT DEPLOY: reduce active quoting pairs "
                f"(currently {pairs}), widen the price threshold "
                f"(currently {config.price_threshold_bps:.1f} bps), or split flow across sessions."
            )
            logger.error(notes)

        return TuningReport(
            symbol=symbol,
            unthrottled_message_rate_mps=unthrottled_mps,
            target_safety_limit_mps=target_mps,
            recommended_reprice_delay_ms=optimal_delay_ms,
            projected_message_rate_mps=projected_mps,
            adverse_selection_exposure_score=exposure_score,
            is_tuning_required=is_tuning_req,
            status=status,
            audit_notes=notes,
            baseline_session_mps=baseline_mps,
            is_target_achievable=is_target_achievable
        )
