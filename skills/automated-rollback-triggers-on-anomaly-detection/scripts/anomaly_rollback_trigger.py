"""
automated-rollback-triggers-on-anomaly-detection:
Monitors post-deployment telemetry and recommends an automated rollback if
technical or trading anomalies exceed safe thresholds.

The engine implements three institutional safeguards that a naive threshold
check omits and that are the difference between controlled recovery and a
self-inflicted outage:

  1. Consecutive-failure confirmation -- a rollback is only recommended after
     ``consecutive_failures_required`` metric snapshots in a row breach a
     threshold. This prevents false-positive rollbacks from a single transient
     spike (flapping) during normal market-open volatility.
  2. Rollback-loop prevention -- a cooldown window and a hard cap on the number
     of rollbacks per deployment stop the cascade of
     rollback -> redeploy -> still-flagged -> rollback. Once the cap is hit the
     engine suppresses further automatic action and escalates to a human.
  3. Real-signal guard -- when the caller flags the sample as
     ``market_open_volatility`` the engine detects and logs the anomaly but
     suppresses the rollback, because the anomaly may be a genuine market event
     rather than a deployment defect.

Detection always uses absolute threshold comparison (the THRESHOLD strategy).
See ``references/standards.md`` for the PREVIOUS and CANARY_BASELINE
comparison strategies an operator can adopt for tighter false-positive control.

Input-validity contract: every threshold and every observed value must be a
finite number. ``NaN`` and ``Inf`` are rejected at construction rather than
compared, because IEEE 754 makes every ordered comparison against ``NaN``
false -- a ``NaN`` latency sample would be reported ``HEALTHY``, and a ``NaN``
or ``Inf`` latency threshold would silently disable latency detection for the
whole deployment. Telemetry backends do produce these values (Prometheus
transfers ``NaN``/``Inf`` as quoted JSON strings, and a reject ratio over a
window with no orders is 0/0), so the poller must treat an invalid or missing
sample as an evaluation failure to escalate -- never as a healthy sample.
"""
import dataclasses
import enum
import logging
import math
import time
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)


def _require_finite(name: str, value: float) -> None:
    """Raise ``ValueError`` unless ``value`` is a finite number.

    Guards the engine against ``NaN``/``Inf`` silently defeating every
    threshold comparison. See the input-validity contract in the module
    docstring.
    """
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}")


class AnomalySeverity(enum.Enum):
    """Severity of a single metric breach, used to prioritise response."""
    WARNING = "warning"
    CRITICAL = "critical"


class RollbackDecision(enum.Enum):
    """
    Outcome of an evaluation.

    - ``HEALTHY``    : no threshold breached.
    - ``CONFIRMING`` : anomaly detected but the consecutive-failure count has
                       not yet reached the configured confirmation threshold.
    - ``ROLLBACK``   : anomaly confirmed within policy; revert to the previous
                       known-good version.
    - ``SUPPRESSED`` : anomaly detected but action withheld (market-open
                       volatility guard, rollback cooldown, or the per-deployment
                       rollback cap has been reached -- escalate to a human).
    """
    HEALTHY = "healthy"
    CONFIRMING = "confirming"
    ROLLBACK = "rollback"
    SUPPRESSED = "suppressed"


@dataclasses.dataclass(frozen=True)
class RollbackThresholdConfig:
    """Per-metric safety thresholds plus the rollback-confirmation policy.

    Defaults follow institutional norms: confirm on 2 consecutive breaches,
    a 5-minute cooldown, and a single automatic rollback per deployment
    (further failures escalate to a human).
    """
    max_latency_ms: float
    max_http_5xx_error_rate: float
    max_order_reject_rate: float
    # Number of consecutive breaching snapshots required before a rollback is
    # recommended. >= 1. Set to 1 to rollback on the first breach (not advised
    # for volatile instruments).
    consecutive_failures_required: int = 2
    # Seconds that must elapse after a recommended rollback before another is
    # allowed. Prevents rollback loops.
    rollback_cooldown_seconds: float = 300.0
    # Hard cap on automatic rollbacks for one deployment. Once reached the
    # engine suppresses further rollbacks and escalates to a human. 0 means
    # detection-only / manual-rollback mode.
    max_rollbacks_per_deployment: int = 1

    def __post_init__(self) -> None:
        _require_finite("max_latency_ms", self.max_latency_ms)
        _require_finite("max_http_5xx_error_rate", self.max_http_5xx_error_rate)
        _require_finite("max_order_reject_rate", self.max_order_reject_rate)
        _require_finite("rollback_cooldown_seconds", self.rollback_cooldown_seconds)
        if self.max_latency_ms < 0:
            raise ValueError("max_latency_ms must be >= 0")
        if not (0.0 <= self.max_http_5xx_error_rate <= 1.0):
            raise ValueError("max_http_5xx_error_rate must be in [0.0, 1.0]")
        if not (0.0 <= self.max_order_reject_rate <= 1.0):
            raise ValueError("max_order_reject_rate must be in [0.0, 1.0]")
        if self.consecutive_failures_required < 1:
            raise ValueError("consecutive_failures_required must be >= 1")
        if self.rollback_cooldown_seconds < 0:
            raise ValueError("rollback_cooldown_seconds must be >= 0")
        if self.max_rollbacks_per_deployment < 0:
            raise ValueError("max_rollbacks_per_deployment must be >= 0")


@dataclasses.dataclass(frozen=True)
class DeploymentHealthMetrics:
    """One post-deployment telemetry snapshot.

    ``market_open_volatility`` should be set True when the caller knows the
    sample overlaps a market-open / fast-market window in which elevated
    reject rates and latency are expected and may represent a real signal
    rather than a deployment defect.
    """
    latency_ms: float
    http_5xx_error_rate: float
    order_reject_rate: float
    market_open_volatility: bool = False

    def __post_init__(self) -> None:
        _require_finite("latency_ms", self.latency_ms)
        _require_finite("http_5xx_error_rate", self.http_5xx_error_rate)
        _require_finite("order_reject_rate", self.order_reject_rate)
        if self.latency_ms < 0:
            raise ValueError("latency_ms must be >= 0")
        if not (0.0 <= self.http_5xx_error_rate <= 1.0):
            raise ValueError("http_5xx_error_rate must be in [0.0, 1.0]")
        if not (0.0 <= self.order_reject_rate <= 1.0):
            raise ValueError("order_reject_rate must be in [0.0, 1.0]")


@dataclasses.dataclass(frozen=True)
class Anomaly:
    """A single metric breach detected during evaluation."""
    metric_name: str
    observed_value: float
    threshold: float
    severity: AnomalySeverity
    detail: str


@dataclasses.dataclass
class RollbackEvaluationResult:
    """Outcome of ``AutomatedRollbackEngine.evaluate_metrics``.

    ``should_rollback`` is kept as a plain boolean for CI/CD controllers that
    only need the action bit; ``decision`` carries the richer policy state.
    ``anomalies`` is the structured record; ``anomalies_detected`` retains the
    human-readable message list for log parity with the original API.

    ``consecutive_failures`` is the streak as it stood when the decision was
    taken (on ``ROLLBACK``, the confirmed streak before the internal reset).
    It is frozen, not advanced, on samples suppressed by the market-open guard
    or by an active cooldown. ``remaining_cooldown_s`` is the cooldown in force
    *after* this evaluation, so a ``ROLLBACK`` result reports the full cooldown
    it just started.
    """
    should_rollback: bool
    decision: RollbackDecision
    anomalies: List[Anomaly]
    anomalies_detected: List[str]
    consecutive_failures: int
    remaining_cooldown_s: float
    rollbacks_issued: int


class AutomatedRollbackEngine:
    """
    Evaluates real-time health metrics post-deployment and recommends an
    automated rollback to the previous known-good version when technical or
    trading anomalies are confirmed within the configured safety policy.

    The engine is stateful across calls within a single deployment: it tracks
    the consecutive-failure streak, the last rollback time, and the rollback
    count. Call ``reset()`` at the start of each new deployment.

    It is not thread-safe. Drive it from a single poller loop; concurrent
    ``evaluate_metrics`` calls would interleave the streak updates and the
    rollback count that the safety policy depends on.
    """

    def __init__(
        self,
        config: RollbackThresholdConfig,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._clock = clock
        self._consecutive_failures: int = 0
        self._last_rollback_time_s: float = 0.0
        self._rollbacks_issued: int = 0

    def reset(self) -> None:
        """Clear all per-deployment state. Call when a new deployment begins."""
        self._consecutive_failures = 0
        self._last_rollback_time_s = 0.0
        self._rollbacks_issued = 0
        logger.info("Rollback engine state reset for a new deployment.")

    def _remaining_cooldown(self) -> float:
        if self._rollbacks_issued == 0:
            return 0.0
        elapsed = self._clock() - self._last_rollback_time_s
        remaining = self.config.rollback_cooldown_seconds - elapsed
        return max(0.0, remaining)

    def _detect_anomalies(self, metrics: DeploymentHealthMetrics) -> List[Anomaly]:
        anomalies: List[Anomaly] = []

        if metrics.latency_ms > self.config.max_latency_ms:
            anomalies.append(Anomaly(
                metric_name="latency_ms",
                observed_value=metrics.latency_ms,
                threshold=self.config.max_latency_ms,
                severity=AnomalySeverity.WARNING,
                detail=(
                    f"Latency Spike: {metrics.latency_ms:.1f}ms exceeds "
                    f"threshold of {self.config.max_latency_ms:.1f}ms."
                ),
            ))

        if metrics.http_5xx_error_rate > self.config.max_http_5xx_error_rate:
            anomalies.append(Anomaly(
                metric_name="http_5xx_error_rate",
                observed_value=metrics.http_5xx_error_rate,
                threshold=self.config.max_http_5xx_error_rate,
                severity=AnomalySeverity.CRITICAL,
                detail=(
                    f"API Error Spike: 5xx rate {metrics.http_5xx_error_rate:.2%} "
                    f"exceeds threshold of {self.config.max_http_5xx_error_rate:.2%}."
                ),
            ))

        if metrics.order_reject_rate > self.config.max_order_reject_rate:
            anomalies.append(Anomaly(
                metric_name="order_reject_rate",
                observed_value=metrics.order_reject_rate,
                threshold=self.config.max_order_reject_rate,
                severity=AnomalySeverity.CRITICAL,
                detail=(
                    f"Trading Anomaly: Order reject rate "
                    f"{metrics.order_reject_rate:.2%} exceeds threshold of "
                    f"{self.config.max_order_reject_rate:.2%}."
                ),
            ))

        return anomalies

    def evaluate_metrics(
        self, metrics: DeploymentHealthMetrics
    ) -> RollbackEvaluationResult:
        """Evaluate one telemetry snapshot and return the rollback decision.

        This method raises nothing: field validation runs in
        ``DeploymentHealthMetrics.__post_init__`` when the snapshot is
        constructed, so a snapshot that reaches here already carries finite,
        in-range values. Every outcome is expressed as a ``RollbackDecision``.

        The engine can only judge the samples it is given. A poller that
        substitutes the previous sample, or a zero, for a metric the backend
        did not return will hold this gate at ``HEALTHY`` for the whole
        burn-in window while a defective version keeps trading -- construct
        the snapshot only from a fresh, complete scrape and escalate
        otherwise.
        """
        anomalies = self._detect_anomalies(metrics)
        messages = [a.detail for a in anomalies]
        cooldown_remaining = self._remaining_cooldown()
        cap_reached = (
            self._rollbacks_issued >= self.config.max_rollbacks_per_deployment
        )
        # Streak value reported to the caller. Set explicitly only by the
        # ROLLBACK branch, which resets the live counter after confirming.
        streak_at_decision: Optional[int] = None

        if not anomalies:
            self._consecutive_failures = 0
            decision = RollbackDecision.HEALTHY
            should_rollback = False
            logger.info(
                "Deployment health metrics are within acceptable parameters.",
                extra={"decision": decision.value},
            )

        elif metrics.market_open_volatility:
            # Market-open / fast-market guard: detect and log, but neither
            # advance nor reset the streak. The anomaly may be a real market
            # event rather than a deployment defect.
            decision = RollbackDecision.SUPPRESSED
            should_rollback = False
            logger.warning(
                "Anomalies detected during market-open volatility window; "
                "rollback suppressed (may be a real signal, not a defect).",
                extra={"anomaly_count": len(anomalies), "decision": decision.value},
            )

        elif cooldown_remaining > 0 and not cap_reached:
            # Cooldown guard: samples taken while traffic is being rerouted to
            # the previous version do not evidence a defect in the version
            # under test, so they do NOT advance the streak. The next rollback
            # must earn a fresh confirmation streak after the cooldown expires
            # -- otherwise the cooldown would merely postpone a rollback loop
            # by one cooldown period instead of preventing it.
            #
            # A firm already at its rollback cap is not waiting for anything,
            # so the cooldown does not hold back its escalation below.
            decision = RollbackDecision.SUPPRESSED
            should_rollback = False
            logger.warning(
                f"Anomaly detected but rollback cooldown active "
                f"({cooldown_remaining:.1f}s remaining); action suppressed and "
                f"confirmation streak held.",
                extra={
                    "decision": decision.value,
                    "remaining_cooldown_s": cooldown_remaining,
                    "consecutive_failures": self._consecutive_failures,
                },
            )

        else:
            # Confirmation runs before the cap is consulted, in every mode. The
            # cap decides whether a *confirmed* anomaly becomes a rollback or a
            # page -- it must not turn a single transient spike into either.
            self._consecutive_failures += 1
            confirmed = (
                self._consecutive_failures
                >= self.config.consecutive_failures_required
            )

            if not confirmed:
                decision = RollbackDecision.CONFIRMING
                should_rollback = False
                logger.warning(
                    "Anomaly detected; awaiting consecutive confirmation "
                    f"({self._consecutive_failures}/"
                    f"{self.config.consecutive_failures_required}).",
                    extra={
                        "decision": decision.value,
                        "consecutive_failures": self._consecutive_failures,
                    },
                )
            elif cap_reached:
                # Confirmed, but no rollback budget left (or none configured,
                # in detection-only mode): do not loop, escalate to a human.
                # The streak keeps advancing here as an escalation signal --
                # it says how many consecutive samples have breached.
                decision = RollbackDecision.SUPPRESSED
                should_rollback = False
                logger.critical(
                    "Anomaly confirmed but automatic rollback cap "
                    f"({self.config.max_rollbacks_per_deployment}) reached; "
                    "ESCALATING TO HUMAN. Further rollbacks suppressed.",
                    extra={
                        "decision": decision.value,
                        "rollbacks_issued": self._rollbacks_issued,
                        "consecutive_failures": self._consecutive_failures,
                    },
                )
            else:
                decision = RollbackDecision.ROLLBACK
                should_rollback = True
                streak_at_decision = self._consecutive_failures
                self._rollbacks_issued += 1
                self._last_rollback_time_s = self._clock()
                # Reset the streak so the next rollback cycle requires fresh
                # confirmation rather than riding the prior streak.
                self._consecutive_failures = 0
                # Report the cooldown this rollback just started, not the
                # (zero) value observed before it.
                cooldown_remaining = self._remaining_cooldown()
                logger.critical(
                    "ANOMALY CONFIRMED IN POST-DEPLOYMENT METRICS. "
                    "TRIGGERING ROLLBACK.",
                    extra={
                        "decision": decision.value,
                        "consecutive_failures": streak_at_decision,
                        "rollbacks_issued": self._rollbacks_issued,
                    },
                )
                for anomaly in anomalies:
                    logger.error(
                        "- %s (severity=%s)", anomaly.detail, anomaly.severity.value,
                    )

        if streak_at_decision is None:
            streak_at_decision = self._consecutive_failures

        return RollbackEvaluationResult(
            should_rollback=should_rollback,
            decision=decision,
            anomalies=anomalies,
            anomalies_detected=messages,
            consecutive_failures=streak_at_decision,
            remaining_cooldown_s=cooldown_remaining,
            rollbacks_issued=self._rollbacks_issued,
        )
