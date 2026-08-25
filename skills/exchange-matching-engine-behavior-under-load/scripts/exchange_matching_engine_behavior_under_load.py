"""
Closed-form queueing model of exchange matching-engine congestion.

Given an engine's mean per-message service time, its sustainable service rate and the
current message arrival rate, this module reports the steady-state sojourn time an
inbound message experiences and converts engine utilisation into a market-making
quoting directive.

Scope and limits (read before trusting a number out of this module):

* ``simulate_matching_engine_load`` is an *analytic* evaluation of a standard queueing
  result. It is not a stochastic simulation and draws no random numbers -- it is
  deterministic for a given input.
* The M/M/1 sojourn time ``W = (1/mu) / (1 - rho)`` holds only when ``baseline_latency_us``
  is the mean **service time** ``1/mu``, i.e. the reciprocal of
  ``engine_capacity_msgs_per_sec``. Load-independent latency (network transit, wire
  serialisation, gateway hops) is *added*, never multiplied by ``1/(1 - rho)``; pass it
  as ``fixed_latency_us``.
* Arrivals are assumed Poisson. Real order flow is strongly clustered/self-exciting, so
  a model fitted to a *mean* rate understates delay during a burst. Feed the peak-window
  arrival rate, not a session average.
* Real venues shed load rather than queueing it without bound: above a session throttle
  CME Globex, Eurex T7 and Nasdaq reject messages and ultimately disconnect the session.
  ``rho >= 1`` therefore denotes "engine saturated, expect rejects", not a latency
  estimate. See ``references/standards.md``.
"""
import logging
import math
from dataclasses import dataclass
from typing import Final

logger = logging.getLogger(__name__)

# --- Service-time distribution models -------------------------------------------------
# M/M/1: exponential service times (squared coefficient of variation cs^2 = 1).
# M/D/1: deterministic service times (cs^2 = 0). By the Pollaczek-Khinchine formula the
#        mean queueing delay is exactly HALF the M/M/1 value at the same rho. A sequenced,
#        single-threaded matching engine is closer to deterministic than exponential, so
#        M/M/1 is the conservative (pessimistic) of the two.
SERVICE_MODEL_MM1: Final[str] = "M/M/1"
SERVICE_MODEL_MD1: Final[str] = "M/D/1"
SUPPORTED_SERVICE_MODELS: Final[frozenset] = frozenset({SERVICE_MODEL_MM1, SERVICE_MODEL_MD1})

# rho is clamped here before evaluating 1/(1 - rho). At or above rho = 1 the queue has no
# steady state, so any finite number is a censored LOWER BOUND, flagged as such in the report.
SATURATION_RHO_CAP: Final[float] = 0.99

# Ratio between the supplied service time and 1/C beyond which the two inputs are
# considered mutually inconsistent and a warning is logged.
SERVICE_TIME_CONSISTENCY_TOLERANCE: Final[float] = 2.0

_MICROSECONDS_PER_SECOND: Final[float] = 1_000_000.0


def _require_finite(value: float, name: str) -> float:
    """Reject NaN/Inf before it can propagate into an 'engine healthy' verdict."""
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(
            f"{name} must be a finite number, got {value!r}. A non-finite telemetry "
            f"reading must fail loudly -- it must never be silently treated as low load."
        )
    return numeric


@dataclass(frozen=True)
class EngineLoadMetrics:
    """
    One observation of matching-engine load.

    All latencies are **microseconds**; all rates are **messages per second**.

    Args:
        venue_id: Venue / market-segment identifier used in audit output.
        baseline_latency_us: Mean per-message **service time** of the engine, i.e.
            ``1e6 / engine_capacity_msgs_per_sec``. This is *not* a round-trip or
            wire-to-wire latency -- see ``fixed_latency_us``.
        engine_capacity_msgs_per_sec: Sustainable service rate ``mu`` of the engine
            (or of the market-segment partition being modelled).
        arrival_rate_msgs_per_sec: Observed arrival rate ``lambda``. Use the peak-window
            rate during bursts, not a session average.
        fixed_latency_us: Load-**independent** latency (network transit, serialisation,
            gateway hops). Added to the queueing result, never scaled by ``1/(1 - rho)``.

    Raises:
        ValueError: on a non-finite value, a non-positive capacity or service time, a
            negative arrival rate or fixed latency, or an empty ``venue_id``.
    """

    venue_id: str
    baseline_latency_us: float
    engine_capacity_msgs_per_sec: float
    arrival_rate_msgs_per_sec: float
    fixed_latency_us: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.venue_id, str) or not self.venue_id.strip():
            raise ValueError("venue_id must be a non-empty string.")

        baseline = _require_finite(self.baseline_latency_us, "baseline_latency_us")
        capacity = _require_finite(self.engine_capacity_msgs_per_sec, "engine_capacity_msgs_per_sec")
        arrival = _require_finite(self.arrival_rate_msgs_per_sec, "arrival_rate_msgs_per_sec")
        fixed = _require_finite(self.fixed_latency_us, "fixed_latency_us")

        if capacity <= 0.0:
            raise ValueError(f"engine_capacity_msgs_per_sec must be > 0, got {capacity}.")
        if baseline <= 0.0:
            raise ValueError(
                f"baseline_latency_us (mean service time) must be > 0, got {baseline}."
            )
        if arrival < 0.0:
            raise ValueError(f"arrival_rate_msgs_per_sec must be >= 0, got {arrival}.")
        if fixed < 0.0:
            raise ValueError(f"fixed_latency_us must be >= 0, got {fixed}.")

    @property
    def implied_service_time_us(self) -> float:
        """Service time implied by the stated capacity: ``1e6 / mu`` microseconds."""
        return _MICROSECONDS_PER_SECOND / self.engine_capacity_msgs_per_sec


@dataclass(frozen=True)
class MatchingEngineLoadAuditReport:
    """
    Immutable audit record of one load evaluation.

    Latency fields are microseconds. ``queuing_delay_penalty_us`` is the mean queueing
    delay ``Wq`` alone -- it excludes both the service time and ``fixed_latency_us``.
    """

    venue_id: str
    arrival_rate_msgs_per_sec: float
    engine_capacity_msgs_per_sec: float
    utilization_factor_rho: float        # lambda / C, NOT clamped; may exceed 1.0
    baseline_latency_us: float           # mean service time 1/mu
    effective_latency_us: float          # fixed + service + queueing delay
    queuing_delay_penalty_us: float      # Wq only
    latency_multiplier: float            # effective / unloaded (fixed + service)
    adverse_selection_risk_level: str    # 'LOW' | 'MODERATE' | 'HIGH_SNIPING_RISK'
    strategy_adaptation_directive: str   # 'NORMAL_OPERATIONS' | 'WIDEN_PASSIVE_SPREADS' | 'PAUSE_PASSIVE_QUOTING'
    audit_notes: str
    service_model: str = SERVICE_MODEL_MM1
    fixed_latency_us: float = 0.0
    is_saturated: bool = False           # rho >= 1.0: no steady state, venue will reject
    # True whenever the SATURATION_RHO_CAP clamp bound, i.e. rho > cap. This is a superset
    # of is_saturated: at 0.99 < rho < 1 the queue is still stable but the reported latency
    # is the value at the cap, far below the true figure.
    effective_latency_is_lower_bound: bool = False
    implied_service_time_us: float = 0.0
    service_time_consistency_ratio: float = 1.0  # baseline_latency_us / implied_service_time_us


class ExchangeMatchingEngineLoadSimulator:
    """
    Converts matching-engine utilisation into a latency estimate and a quoting directive.

    Directive thresholds are evaluated on the **exact** (unrounded, unclamped) utilisation
    ``rho = lambda / C``, with non-strict lower bounds::

        rho <  moderate_congestion_threshold  -> NORMAL_OPERATIONS     (risk LOW)
        rho >= moderate_congestion_threshold  -> WIDEN_PASSIVE_SPREADS (risk MODERATE)
        rho >= high_congestion_threshold      -> PAUSE_PASSIVE_QUOTING (risk HIGH_SNIPING_RISK)

    A rho landing exactly on a threshold takes the more conservative branch.
    """

    def __init__(
        self,
        high_congestion_threshold: float = 0.85,
        moderate_congestion_threshold: float = 0.50,
        service_model: str = SERVICE_MODEL_MM1,
    ) -> None:
        """
        Raises:
            ValueError: if the thresholds are non-finite, outside ``(0, 1]``, or not
                strictly ordered ``moderate < high``; or if ``service_model`` is unknown.
                An inverted pair would silently emit the wrong directive, so it is
                rejected rather than reordered.
        """
        high = _require_finite(high_congestion_threshold, "high_congestion_threshold")
        moderate = _require_finite(moderate_congestion_threshold, "moderate_congestion_threshold")

        if not 0.0 < moderate < high <= 1.0:
            raise ValueError(
                "Thresholds must satisfy 0 < moderate_congestion_threshold < "
                f"high_congestion_threshold <= 1.0, got moderate={moderate}, high={high}."
            )
        if service_model not in SUPPORTED_SERVICE_MODELS:
            raise ValueError(
                f"service_model must be one of {sorted(SUPPORTED_SERVICE_MODELS)}, "
                f"got {service_model!r}."
            )

        self.high_congestion_threshold = high
        self.moderate_congestion_threshold = moderate
        self.service_model = service_model

    def _mean_queueing_delay_us(self, service_time_us: float, rho: float) -> float:
        """
        Mean queueing delay ``Wq`` (excluding service), from the Pollaczek-Khinchine result.

        M/M/1 (cs^2 = 1): ``Wq = (1/mu) * rho / (1 - rho)``
        M/D/1 (cs^2 = 0): ``Wq = (1/mu) * rho / (2 * (1 - rho))`` -- exactly half of M/M/1.
        """
        wq_mm1 = service_time_us * rho / (1.0 - rho)
        if self.service_model == SERVICE_MODEL_MD1:
            return wq_mm1 * 0.5
        return wq_mm1

    def simulate_matching_engine_load(
        self, metrics: EngineLoadMetrics
    ) -> MatchingEngineLoadAuditReport:
        """
        Evaluate steady-state engine latency and emit a quoting directive.

        This is a closed-form evaluation, not a Monte Carlo simulation.

        Args:
            metrics: A validated :class:`EngineLoadMetrics` observation.

        Returns:
            A :class:`MatchingEngineLoadAuditReport`. When ``is_saturated`` is true the
            latency figures are a censored lower bound, not an estimate.

        Raises:
            TypeError: if ``metrics`` is not an :class:`EngineLoadMetrics`.
        """
        if not isinstance(metrics, EngineLoadMetrics):
            raise TypeError(
                f"metrics must be an EngineLoadMetrics instance, got {type(metrics).__name__}."
            )

        service_time_us = metrics.baseline_latency_us
        implied_service_time_us = metrics.implied_service_time_us
        consistency_ratio = service_time_us / implied_service_time_us

        # The M/M/1 sojourn time is only (1/mu)/(1 - rho) when the supplied service time IS
        # 1/mu. A materially different value means a wire-to-wire or round-trip latency has
        # been passed where a service time belongs, which inflates the result by 1/(1 - rho).
        if not (
            1.0 / SERVICE_TIME_CONSISTENCY_TOLERANCE
            <= consistency_ratio
            <= SERVICE_TIME_CONSISTENCY_TOLERANCE
        ):
            logger.warning(
                "SERVICE TIME INCONSISTENT WITH CAPACITY [%s]: baseline_latency_us=%.3fus is "
                "%.2fx the service time implied by capacity (%.3fus = 1e6/%.0f). The queueing "
                "model scales the service time by 1/(1-rho); load-independent latency belongs "
                "in fixed_latency_us instead.",
                metrics.venue_id, service_time_us, consistency_ratio,
                implied_service_time_us, metrics.engine_capacity_msgs_per_sec,
            )

        # Exact utilisation -- directives are decided on this, never on a rounded value.
        rho_exact = metrics.arrival_rate_msgs_per_sec / metrics.engine_capacity_msgs_per_sec
        is_saturated = rho_exact >= 1.0
        rho_for_model = min(rho_exact, SATURATION_RHO_CAP)
        # The clamp binds slightly before saturation: between the cap and 1.0 the queue is
        # still stable but its true delay far exceeds the value computed at the cap.
        is_lower_bound = rho_exact > SATURATION_RHO_CAP

        queuing_penalty = self._mean_queueing_delay_us(service_time_us, rho_for_model)
        unloaded_latency = metrics.fixed_latency_us + service_time_us
        effective_latency = unloaded_latency + queuing_penalty
        multiplier = effective_latency / unloaded_latency

        # Finite inputs can still overflow to inf through the 1/(1 - rho) amplification.
        # An infinite "latency" must not be handed back as if it were a reading.
        if not all(map(math.isfinite, (queuing_penalty, effective_latency, multiplier))):
            raise ValueError(
                f"Latency computation overflowed to a non-finite value for "
                f"{metrics.venue_id} (service_time_us={service_time_us}, rho={rho_exact}). "
                f"Check the input magnitudes."
            )

        rho_rounded = round(rho_exact, 4)
        effective_rounded = round(effective_latency, 2)
        penalty_rounded = round(queuing_penalty, 2)
        multiplier_rounded = round(multiplier, 2)

        if is_saturated:
            bound_note = (
                f" ENGINE SATURATED (rho >= 1.0): the queue has no steady state, so "
                f"{effective_rounded:.1f}us is a LOWER BOUND computed at the "
                f"rho={SATURATION_RHO_CAP} cap, not an estimate. Expect message rejects "
                f"and session disconnection."
            )
        elif is_lower_bound:
            bound_note = (
                f" NOTE: rho exceeds the rho={SATURATION_RHO_CAP} modelling cap, so "
                f"{effective_rounded:.1f}us is a LOWER BOUND, not an estimate."
            )
        else:
            bound_note = ""

        if rho_exact >= self.high_congestion_threshold:
            risk_level = "HIGH_SNIPING_RISK"
            directive = "PAUSE_PASSIVE_QUOTING"
            notes = (
                f"HIGH MATCHING ENGINE CONGESTION [{metrics.venue_id}]: Utilization rho={rho_rounded:.2f} "
                f">= {self.high_congestion_threshold:.2f}. {self.service_model} latency {multiplier_rounded}x to "
                f"{effective_rounded:.1f}us (+{penalty_rounded:.1f}us queueing). "
                f"Directing PAUSE_PASSIVE_QUOTING to prevent stale quote sniping.{bound_note}"
            )
            logger.critical(notes)
        elif rho_exact >= self.moderate_congestion_threshold:
            risk_level = "MODERATE"
            directive = "WIDEN_PASSIVE_SPREADS"
            notes = (
                f"MODERATE MATCHING ENGINE CONGESTION [{metrics.venue_id}]: Utilization rho={rho_rounded:.2f}. "
                f"{self.service_model} latency {multiplier_rounded}x to {effective_rounded:.1f}us "
                f"(+{penalty_rounded:.1f}us queueing). Directing WIDEN_PASSIVE_SPREADS to "
                f"absorb queue drift.{bound_note}"
            )
            logger.warning(notes)
        else:
            risk_level = "LOW"
            directive = "NORMAL_OPERATIONS"
            notes = (
                f"MATCHING ENGINE HEALTHY [{metrics.venue_id}]: Utilization rho={rho_rounded:.2f}. "
                f"{self.service_model} latency = {effective_rounded:.1f}us.{bound_note}"
            )
            logger.info(notes)

        return MatchingEngineLoadAuditReport(
            venue_id=metrics.venue_id,
            arrival_rate_msgs_per_sec=metrics.arrival_rate_msgs_per_sec,
            engine_capacity_msgs_per_sec=metrics.engine_capacity_msgs_per_sec,
            utilization_factor_rho=rho_rounded,
            baseline_latency_us=service_time_us,
            effective_latency_us=effective_rounded,
            queuing_delay_penalty_us=penalty_rounded,
            latency_multiplier=multiplier_rounded,
            adverse_selection_risk_level=risk_level,
            strategy_adaptation_directive=directive,
            audit_notes=notes,
            service_model=self.service_model,
            fixed_latency_us=metrics.fixed_latency_us,
            is_saturated=is_saturated,
            effective_latency_is_lower_bound=is_lower_bound,
            implied_service_time_us=round(implied_service_time_us, 4),
            service_time_consistency_ratio=round(consistency_ratio, 4),
        )
