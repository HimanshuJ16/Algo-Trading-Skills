"""
risk-metric-recalculation-frequency-tuning: tiered risk-metric recalculation
scheduler with hysteresis-guarded P&L-velocity acceleration.

Purpose
-------
Decide *when* each risk metric is recomputed, not *what* it computes. A risk
engine that reruns a 10,000-path Monte Carlo VaR on every tick starves the
thread that is supposed to be checking drawdown. This scheduler assigns each
metric a cadence tier, answers "which metrics are due right now", and shortens
every cadence when P&L starts moving fast enough that stale risk numbers are
dangerous.

Scope boundary (read this before wiring it into an order path)
--------------------------------------------------------------
**This is a monitoring/analytics cadence tuner. It is not a pre-trade control
and must never gate one.** 17 CFR 240.15c3-5(c)(1)(i) requires controls
reasonably designed to "[p]revent the entry of orders that exceed appropriate
pre-set credit or capital thresholds ... by rejecting orders if such orders
would exceed the applicable credit or capital thresholds". That check is a
property of *every order*, not of a cadence. Putting a per-order credit,
capital, position or fat-finger check behind a 30-second tier is a rule
violation, not an optimisation. Tier only the analytics that inform humans and
sizing logic: portfolio VaR, stress scenarios, aggregate Greeks.

The regulation's own words bound how slow a tier may be where it feeds
supervision:

- Commission Delegated Regulation (EU) 2017/589 (MiFID II RTS 6), Article
  16(5): "Real-time alerts shall be generated within five seconds after the
  relevant event." Any metric whose output can raise a disorderly-trading alert
  for an EU/UK-authorised firm therefore cannot sit on a 30 s or 300 s tier.
- RTS 6 Article 17(1)-(2): a firm "shall continuously operate the post-trade
  controls that it has in place", which "shall include the continuous
  assessment and monitoring of market and credit risk ... in terms of effective
  exposure". "Continuous" is not defined numerically; a documented, monitored
  cadence is the practical reading, and a cadence the scheduler demonstrably
  fails to meet (see ``overdue_metrics``) is not one.
- 17 CFR 240.15c3-5(c)(2)(iv) separately requires assurance that "appropriate
  surveillance personnel receive immediate post-trade execution reports".
  Execution reporting is not a tunable tier either.

The tier intervals below (2 s / 30 s / 300 s) are **engineering defaults, not
regulatory minima**. No regulator consulted for this skill prescribes a VaR or
Greeks recalculation interval. Calibrate them against your own measured metric
cost and your firm's documented risk appetite, and record what you chose.

Design notes
------------
- **Dueness is evaluation-driven, not wall-clock driven.** Nothing recomputes
  unless ``evaluate_due_metrics`` is called. If the market data feed stalls, the
  scheduler stalls with it and every risk number silently goes stale. Drive it
  from a heartbeat as well as from ticks. ``overdue_metrics`` reports, on the
  next call, which cadences were missed so the gap is auditable rather than
  invisible.
- **Velocity needs a measurement window.** ``|dPnL| / dt`` over a
  sub-millisecond tick gap amplifies a $1 P&L wobble into $10,000/s. The anchor
  is only advanced once ``min_velocity_sample_sec`` has elapsed; below that the
  previous estimate is carried forward rather than a fabricated one produced.
- **Acceleration has hysteresis.** A single quiet sample in the middle of a
  crash must not drop the engine back to a 300 s stress-test cadence. Exit
  requires velocity at or below ``threshold * acceleration_exit_ratio`` *and* a
  minimum dwell time in accelerated mode.
- **Entering accelerated mode forces an immediate recalculation.** Without it
  the tick that detects the spike does not actually recompute anything: a VaR
  last run 1 s ago is not due again for another 4 s under a 5 s accelerated
  interval, so the engine announces an emergency and then waits.
- **Load reduction is measured, not asserted.** ``calculation_load_reduction_pct``
  is a running figure derived from the invocations this instance actually
  scheduled versus the naive "recompute everything, every evaluation" baseline.
  It is *not* a CPU-cycle measurement: with the default
  ``relative_cost_units=1.0`` every metric counts equally, which materially
  understates the true saving because a stress test costs orders of magnitude
  more than a drawdown update. Supply measured ``relative_cost_units`` to make
  the figure cost-weighted, and never report it as a CPU benchmark.

Limitations (deliberate, documented)
------------------------------------
- P&L velocity is a proxy for "risk numbers are going stale fast". It does not
  detect risk that builds without P&L movement (a gamma or correlation build-up
  in a quiet market). Pair it with a position-change trigger if that matters.
- The scheduler carries no notion of how long a metric takes to compute. If a
  300 s stress test takes 90 s, an accelerated 30 s interval is unachievable;
  the scheduler will simply report it due on every call.
- Single P&L series only. A per-strategy or per-desk engine needs one instance
  per book, or an aggregation policy the caller supplies.
"""
import logging
import math
import threading
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

#: Tier reserved for metrics that must run on every evaluation (drawdown,
#: position caps). Tier-1 intervals are ignored and must be 0.0.
TIER_PER_EVALUATION = 1

#: Default absolute P&L rate at or above which cadences are accelerated. A house
#: default sized for a book where $500/s is unusual, not a published threshold.
DEFAULT_PNL_VELOCITY_THRESHOLD_USD_PER_SEC = 500.0

#: Minimum elapsed time before the velocity anchor is advanced. Shorter windows
#: turn tick jitter into six-figure velocities.
DEFAULT_MIN_VELOCITY_SAMPLE_SEC = 0.25

#: Accelerated mode is left only once velocity falls to this fraction of the
#: entry threshold -- the hysteresis band that prevents cadence flapping.
DEFAULT_ACCELERATION_EXIT_RATIO = 0.5

#: Minimum time to remain in accelerated mode once entered, regardless of how
#: quickly velocity subsides.
DEFAULT_ACCELERATION_MIN_DWELL_SEC = 30.0

#: A metric is reported overdue when the gap since its last calculation exceeds
#: this multiple of the interval that was in force.
DEFAULT_STALENESS_MULTIPLE = 2.0


@dataclass
class RiskMetricScheduleConfig:
    """
    Cadence configuration for one risk metric.

    ``last_calculated_timestamp`` is ``None`` until the metric has been
    scheduled once. ``None`` means "never calculated, therefore due" and is
    deliberately not ``0.0``: with a ``0.0`` sentinel the first evaluation's
    outcome depends on the caller's clock epoch (everything is due under
    ``time.time()``, almost nothing is due under a ``time.monotonic()`` clock
    that starts near zero).

    ``relative_cost_units`` weights this metric in the reported load reduction.
    Leave it at 1.0 (every metric counts equally) or set it to a *measured*
    relative cost. Do not invent the numbers.
    """

    metric_name: str
    tier: int
    base_interval_sec: float
    accelerated_interval_sec: float
    relative_cost_units: float = 1.0
    last_calculated_timestamp: Optional[float] = None

    def __post_init__(self) -> None:
        if not self.metric_name or not self.metric_name.strip():
            raise ValueError("metric_name must be a non-empty string")
        if not isinstance(self.tier, int) or isinstance(self.tier, bool) or self.tier < 1:
            raise ValueError(
                f"{self.metric_name}: tier must be an integer >= 1, got {self.tier!r}")
        for field_name in ("base_interval_sec", "accelerated_interval_sec"):
            value = getattr(self, field_name)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0.0:
                raise ValueError(
                    f"{self.metric_name}: {field_name} must be a finite, "
                    f"non-negative number, got {value!r}")
        if self.accelerated_interval_sec > self.base_interval_sec:
            raise ValueError(
                f"{self.metric_name}: accelerated_interval_sec "
                f"({self.accelerated_interval_sec}) must not exceed "
                f"base_interval_sec ({self.base_interval_sec}) -- accelerating a "
                f"cadence means calculating more often, not less")
        if not math.isfinite(self.relative_cost_units) or self.relative_cost_units <= 0.0:
            raise ValueError(
                f"{self.metric_name}: relative_cost_units must be finite and "
                f"> 0, got {self.relative_cost_units!r}")
        if self.tier == TIER_PER_EVALUATION and (
            self.base_interval_sec != 0.0 or self.accelerated_interval_sec != 0.0
        ):
            raise ValueError(
                f"{self.metric_name}: tier {TIER_PER_EVALUATION} runs on every "
                f"evaluation, so both intervals must be 0.0; a non-zero interval "
                f"on a tier-1 metric is a configuration contradiction")
        if self.last_calculated_timestamp is not None and not math.isfinite(
            self.last_calculated_timestamp
        ):
            raise ValueError(
                f"{self.metric_name}: last_calculated_timestamp must be finite "
                f"or None")


@dataclass
class TunerExecutionReport:
    """
    Outcome of a single scheduling decision.

    ``calculation_load_reduction_pct``, ``evaluations_observed``,
    ``cost_units_executed`` and ``cost_units_naive`` are **cumulative since the
    tuner was constructed**, not per-call. A long-lived instance therefore
    reports a session average that lags a recent change in tick rate or cadence.
    """

    metrics_due_for_calc: List[str]
    is_accelerated_mode: bool
    pnl_velocity_usd_per_sec: float
    calculation_load_reduction_pct: float
    status_message: str
    overdue_metrics: List[str] = field(default_factory=list)
    evaluations_observed: int = 0
    cost_units_executed: float = 0.0
    cost_units_naive: float = 0.0


def default_schedule() -> List[RiskMetricScheduleConfig]:
    """
    House default four-tier schedule.

    These intervals are engineering defaults chosen for a single-book equity /
    options engine. They are not derived from any regulator, exchange or
    published study -- see the module docstring before treating them as
    authoritative.
    """
    return [
        RiskMetricScheduleConfig(
            "TICK_DRAWDOWN", tier=1, base_interval_sec=0.0, accelerated_interval_sec=0.0),
        RiskMetricScheduleConfig(
            "GREEKS_DELTA", tier=2, base_interval_sec=2.0, accelerated_interval_sec=0.5),
        RiskMetricScheduleConfig(
            "VAR_1DAY", tier=3, base_interval_sec=30.0, accelerated_interval_sec=5.0),
        RiskMetricScheduleConfig(
            "STRESS_TEST", tier=4, base_interval_sec=300.0, accelerated_interval_sec=30.0),
    ]


class RiskMetricFrequencyTuner:
    """
    Schedules tiered recalculation cadences across risk metrics and accelerates
    them, with hysteresis, while P&L is moving fast.

    Thread safety: all mutating state is guarded by a re-entrant lock, so a
    single instance may be driven from the market-data thread and a heartbeat
    thread concurrently. The returned report is a fresh object per call.
    """

    def __init__(
        self,
        pnl_velocity_threshold_usd_per_sec: float = DEFAULT_PNL_VELOCITY_THRESHOLD_USD_PER_SEC,
        configs: Optional[Sequence[RiskMetricScheduleConfig]] = None,
        min_velocity_sample_sec: float = DEFAULT_MIN_VELOCITY_SAMPLE_SEC,
        acceleration_exit_ratio: float = DEFAULT_ACCELERATION_EXIT_RATIO,
        acceleration_min_dwell_sec: float = DEFAULT_ACCELERATION_MIN_DWELL_SEC,
        staleness_multiple: float = DEFAULT_STALENESS_MULTIPLE,
    ) -> None:
        if not math.isfinite(pnl_velocity_threshold_usd_per_sec) or (
            pnl_velocity_threshold_usd_per_sec <= 0.0
        ):
            raise ValueError(
                "pnl_velocity_threshold_usd_per_sec must be finite and > 0, got "
                f"{pnl_velocity_threshold_usd_per_sec!r}")
        if not math.isfinite(min_velocity_sample_sec) or min_velocity_sample_sec <= 0.0:
            raise ValueError(
                f"min_velocity_sample_sec must be finite and > 0, got "
                f"{min_velocity_sample_sec!r}")
        if not math.isfinite(acceleration_exit_ratio) or not (
            0.0 < acceleration_exit_ratio <= 1.0
        ):
            raise ValueError(
                "acceleration_exit_ratio must lie in (0.0, 1.0]; a ratio of 1.0 "
                "means no hysteresis band, above 1.0 is incoherent. Got "
                f"{acceleration_exit_ratio!r}")
        if not math.isfinite(acceleration_min_dwell_sec) or acceleration_min_dwell_sec < 0.0:
            raise ValueError(
                "acceleration_min_dwell_sec must be finite and >= 0, got "
                f"{acceleration_min_dwell_sec!r}")
        if not math.isfinite(staleness_multiple) or staleness_multiple < 1.0:
            raise ValueError(
                "staleness_multiple must be finite and >= 1.0 (a metric cannot "
                f"be overdue before its interval elapses). Got {staleness_multiple!r}")

        self.velocity_threshold = pnl_velocity_threshold_usd_per_sec
        self.min_velocity_sample_sec = min_velocity_sample_sec
        self.acceleration_exit_ratio = acceleration_exit_ratio
        self.acceleration_min_dwell_sec = acceleration_min_dwell_sec
        self.staleness_multiple = staleness_multiple

        source = list(configs) if configs is not None else default_schedule()
        if not source:
            raise ValueError("configs must contain at least one metric schedule")
        self._configs: Dict[str, RiskMetricScheduleConfig] = {}
        for cfg in source:
            if not isinstance(cfg, RiskMetricScheduleConfig):
                raise TypeError(
                    f"configs must contain RiskMetricScheduleConfig objects, got "
                    f"{type(cfg).__name__}")
            if cfg.metric_name in self._configs:
                raise ValueError(f"duplicate metric_name in configs: {cfg.metric_name!r}")
            # Copy so the caller's objects are never mutated by scheduling.
            self._configs[cfg.metric_name] = replace(cfg)

        self._naive_cost_per_evaluation = sum(
            c.relative_cost_units for c in self._configs.values())

        self._lock = threading.RLock()
        self._last_pnl: Optional[float] = None
        self._last_pnl_time: Optional[float] = None
        self._last_seen_time: Optional[float] = None
        self._velocity_usd_per_sec: float = 0.0
        self._is_accelerated: bool = False
        self._accelerated_since: Optional[float] = None
        self._evaluations: int = 0
        self._cost_units_executed: float = 0.0
        self._cost_units_naive: float = 0.0

    @property
    def is_accelerated_mode(self) -> bool:
        """Current cadence mode. Read-only view of internal state."""
        with self._lock:
            return self._is_accelerated

    def schedule_snapshot(self) -> List[RiskMetricScheduleConfig]:
        """Copies of the live configs, for inspection and audit logging."""
        with self._lock:
            return [replace(c) for c in self._configs.values()]

    def evaluate_due_metrics(
        self,
        current_pnl_usd: float,
        current_timestamp_sec: float,
    ) -> TunerExecutionReport:
        """
        Decide which risk metrics are due at ``current_timestamp_sec``.

        ``current_timestamp_sec`` must come from a single, non-decreasing clock
        (``time.monotonic()`` is the right choice for a live engine; a replay
        harness may pass event timestamps). Out-of-order timestamps raise rather
        than being clamped: under the old ``max(0.001, dt)`` clamp a timestamp
        that went backwards produced a velocity a thousand times the P&L change
        and a spurious acceleration.

        Non-finite P&L or timestamps raise. A ``NaN`` P&L compared against the
        threshold is ``False``, which would silently *disable* acceleration at
        exactly the moment a corrupted feed makes risk numbers least reliable.

        Raises:
            ValueError: on non-finite inputs or a decreasing timestamp.
        """
        if not isinstance(current_pnl_usd, (int, float)) or not math.isfinite(current_pnl_usd):
            raise ValueError(
                f"current_pnl_usd must be a finite number, got {current_pnl_usd!r}")
        if not isinstance(current_timestamp_sec, (int, float)) or not math.isfinite(
            current_timestamp_sec
        ):
            raise ValueError(
                f"current_timestamp_sec must be a finite number, got "
                f"{current_timestamp_sec!r}")

        with self._lock:
            if self._last_seen_time is not None and current_timestamp_sec < self._last_seen_time:
                raise ValueError(
                    f"current_timestamp_sec went backwards "
                    f"({current_timestamp_sec} < {self._last_seen_time}); the tuner "
                    f"requires a single non-decreasing clock")

            velocity = self._update_velocity(current_pnl_usd, current_timestamp_sec)
            was_accelerated = self._is_accelerated
            is_accelerated = self._update_mode(velocity, current_timestamp_sec)
            entering = is_accelerated and not was_accelerated

            overdue = self._find_overdue(current_timestamp_sec, was_accelerated)

            due_metrics: List[str] = []
            executed_cost = 0.0
            for name, cfg in self._configs.items():
                if self._is_due(cfg, current_timestamp_sec, is_accelerated, entering):
                    due_metrics.append(name)
                    executed_cost += cfg.relative_cost_units
                    cfg.last_calculated_timestamp = current_timestamp_sec

            self._evaluations += 1
            self._cost_units_executed += executed_cost
            self._cost_units_naive += self._naive_cost_per_evaluation
            reduction_pct = 100.0 * (
                1.0 - (self._cost_units_executed / self._cost_units_naive))

            self._last_seen_time = current_timestamp_sec
            message = self._build_message(
                due_metrics, is_accelerated, entering, velocity, reduction_pct, overdue)

            return TunerExecutionReport(
                metrics_due_for_calc=due_metrics,
                is_accelerated_mode=is_accelerated,
                pnl_velocity_usd_per_sec=round(velocity, 2),
                calculation_load_reduction_pct=round(reduction_pct, 2),
                status_message=message,
                overdue_metrics=overdue,
                evaluations_observed=self._evaluations,
                cost_units_executed=self._cost_units_executed,
                cost_units_naive=self._cost_units_naive,
            )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _update_velocity(self, pnl: float, timestamp: float) -> float:
        """
        Advance the velocity anchor only once a usable measurement window has
        elapsed; otherwise carry the previous estimate forward.

        Holding the anchor (rather than resetting it) means the P&L change
        accumulated during sub-window calls is not discarded -- it is measured
        across the full span once the window is met.
        """
        if self._last_pnl_time is None:
            self._last_pnl = pnl
            self._last_pnl_time = timestamp
            self._velocity_usd_per_sec = 0.0
            return 0.0

        dt = timestamp - self._last_pnl_time
        if dt < self.min_velocity_sample_sec:
            return self._velocity_usd_per_sec

        self._velocity_usd_per_sec = abs(pnl - self._last_pnl) / dt
        self._last_pnl = pnl
        self._last_pnl_time = timestamp
        return self._velocity_usd_per_sec

    def _update_mode(self, velocity: float, timestamp: float) -> bool:
        """Apply the hysteresis band and dwell time around the mode switch."""
        if not self._is_accelerated:
            if velocity >= self.velocity_threshold:
                self._is_accelerated = True
                self._accelerated_since = timestamp
                return True
            return False

        # `is None`, not a truthiness test: an acceleration entered at
        # timestamp 0.0 on a monotonic clock is falsy, which would zero the
        # dwell on every call and pin the engine in accelerated mode.
        since = self._accelerated_since if self._accelerated_since is not None else timestamp
        dwell_elapsed = timestamp - since
        exit_level = self.velocity_threshold * self.acceleration_exit_ratio
        if velocity <= exit_level and dwell_elapsed >= self.acceleration_min_dwell_sec:
            self._is_accelerated = False
            self._accelerated_since = None
            return False
        return True

    def _interval_for(self, cfg: RiskMetricScheduleConfig, accelerated: bool) -> float:
        return cfg.accelerated_interval_sec if accelerated else cfg.base_interval_sec

    def _is_due(
        self,
        cfg: RiskMetricScheduleConfig,
        timestamp: float,
        accelerated: bool,
        entering: bool,
    ) -> bool:
        if cfg.tier == TIER_PER_EVALUATION:
            return True
        if cfg.last_calculated_timestamp is None:
            # Never calculated: due regardless of the caller's clock epoch.
            return True
        if entering:
            # The tick that detects the spike must actually recompute. Waiting
            # out the remainder of the previous interval defeats acceleration.
            return True
        elapsed = timestamp - cfg.last_calculated_timestamp
        return elapsed >= self._interval_for(cfg, accelerated)

    def _find_overdue(self, timestamp: float, mode_in_force: bool) -> List[str]:
        """
        Metrics whose cadence was missed, judged against the interval that was
        in force *before* this call -- so a mode change is not misreported as a
        missed cadence.
        """
        overdue: List[str] = []
        for name, cfg in self._configs.items():
            if cfg.tier == TIER_PER_EVALUATION or cfg.last_calculated_timestamp is None:
                continue
            interval = self._interval_for(cfg, mode_in_force)
            if interval <= 0.0:
                continue
            if timestamp - cfg.last_calculated_timestamp > self.staleness_multiple * interval:
                overdue.append(name)
        return overdue

    def _build_message(
        self,
        due_metrics: List[str],
        is_accelerated: bool,
        entering: bool,
        velocity: float,
        reduction_pct: float,
        overdue: List[str],
    ) -> str:
        if is_accelerated:
            prefix = "VOLATILITY ACCELERATION ENGAGED" if entering else "VOLATILITY ACCELERATION ACTIVE"
            message = (
                f"{prefix}: P&L velocity ${velocity:.2f}/s vs threshold "
                f"${self.velocity_threshold:.2f}/s. {len(due_metrics)} metric(s) due "
                f"on accelerated cadence.")
            logger.warning(message)
        else:
            message = (
                f"Risk cadence normal: {len(due_metrics)} metric(s) due. "
                f"P&L velocity ${velocity:.2f}/s. Scheduled calculation load is "
                f"{reduction_pct:.2f}% below the recompute-everything baseline "
                f"(invocation-weighted unless relative costs were supplied).")
            logger.info(message)

        if overdue:
            overdue_msg = (
                f"RISK METRICS STALE: {', '.join(overdue)} exceeded "
                f"{self.staleness_multiple}x their scheduled interval. The tuner is "
                f"evaluation-driven -- a stalled feed or a missing heartbeat stops "
                f"recalculation entirely.")
            logger.warning(overdue_msg)
            message = f"{message} {overdue_msg}"
        return message
