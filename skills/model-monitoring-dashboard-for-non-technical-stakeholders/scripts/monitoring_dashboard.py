"""
model-monitoring-dashboard-for-non-technical-stakeholders: traffic-light model
health aggregator and plain-language risk action recommendation generator.

The engine translates ML model telemetry (out-of-sample accuracy, model age,
feature-drift PSI, inference latency) into a GREEN / AMBER / RED status per
component, an overall status equal to the *worst* component, and one of four
plain-language recommended actions.

Two properties matter more than anything else here, because this report is read
by people who cannot audit the numbers behind it:

1. The dashboard never reports GREEN for something it did not measure. An
   unreported or ungoverned metric grades AMBER and says so, and its ``value``
   is ``None`` rather than ``0.0``.
2. The dashboard never accepts impossible telemetry. A negative model age, a
   negative PSI, an accuracy outside [0, 100] or any non-finite value raises
   ``DashboardInputError`` instead of falling through to a healthy branch.

The recommended action is advisory. This module does not cancel orders, flatten
positions or halt anything - see `kill-switch-and-drawdown-circuit-breakers` for
the enforcement mechanism and `risk-limit-breach-escalation-matrix` for routing.
"""
from dataclasses import dataclass, field
import logging
import math
import numbers
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Plain-language recommendations. Stable strings: downstream dashboards, runbooks
# and escalation matrices match on them.
ACTION_NONE = "NO_ACTION_REQUIRED"
ACTION_RESTORE_TELEMETRY = "RESTORE_MODEL_TELEMETRY"
ACTION_RETRAIN = "SCHEDULE_RETRAIN_AND_REVIEW"
ACTION_HALT = "HALT_TRADING_IMMEDIATELY"


class DashboardConfigError(ValueError):
    """Raised when the configured traffic-light thresholds are not coherent."""


class DashboardInputError(ValueError):
    """Raised when supplied telemetry is impossible, non-finite or mistyped.

    Deliberately an exception rather than a RED status: a RED means "the model
    is unhealthy", whereas this means "the monitoring input is broken". Escalate
    it as you would a RED - do not swallow it, or the control is silently gone.
    """


class HealthStatus(Enum):
    GREEN = "GREEN"
    AMBER = "AMBER"
    RED = "RED"


# Severity ordering for the "overall = worst component" aggregation rule.
_SEVERITY: Dict[HealthStatus, int] = {
    HealthStatus.GREEN: 0,
    HealthStatus.AMBER: 1,
    HealthStatus.RED: 2,
}


@dataclass(frozen=True)
class DashboardThresholds:
    """Traffic-light band edges.

    Accuracy, model-age and latency defaults are **operator-chosen conventions
    with no external authority** - calibrate them against the strategy's own
    payoff profile and retraining cadence (see `references/standards.md`).

    The PSI bands follow the Lewis (1994) rule of thumb as stated by Yurdakul &
    Naranjo (2020): PSI < 0.10 little change, 0.10 <= PSI < 0.25 moderate
    change, 0.25 <= PSI significant change. The band edges belong to the
    *worse* band, matching that convention: exactly 0.10 is AMBER and exactly
    0.25 is RED.

    Accuracy, staleness and latency edges use the opposite (inclusive-GREEN)
    convention, because no external source fixes them: exactly
    ``accuracy_green_min_pct`` is GREEN, exactly ``staleness_green_max_days``
    is GREEN, exactly ``latency_green_max_ms`` is GREEN.

    Latency bounds have no defensible default (a sub-millisecond tick-to-trade
    budget and a five-second end-of-day rebalance budget are both normal), so
    they default to ``None``. Leaving them unset does not silently disable the
    check: the latency component then grades AMBER as unconfigured.
    """

    accuracy_green_min_pct: float = 55.0
    accuracy_amber_min_pct: float = 50.0
    staleness_green_max_days: int = 14
    staleness_amber_max_days: int = 30
    drift_psi_green_max: float = 0.10
    drift_psi_amber_max: float = 0.25
    latency_green_max_ms: Optional[float] = None
    latency_amber_max_ms: Optional[float] = None

    def __post_init__(self) -> None:
        for name in ("accuracy_green_min_pct", "accuracy_amber_min_pct",
                     "drift_psi_green_max", "drift_psi_amber_max"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, numbers.Real) \
                    or not math.isfinite(float(value)):
                raise DashboardConfigError(f"{name} must be a finite number, got {value!r}")

        for name in ("staleness_green_max_days", "staleness_amber_max_days"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, numbers.Integral):
                raise DashboardConfigError(
                    f"{name} must be a whole number of days, got {value!r}")

        if not 0.0 <= self.accuracy_amber_min_pct <= self.accuracy_green_min_pct <= 100.0:
            raise DashboardConfigError(
                "accuracy bands must satisfy 0 <= accuracy_amber_min_pct <= "
                f"accuracy_green_min_pct <= 100, got {self.accuracy_amber_min_pct} "
                f"and {self.accuracy_green_min_pct}")

        if not 0 <= self.staleness_green_max_days <= self.staleness_amber_max_days:
            raise DashboardConfigError(
                "staleness bands must satisfy 0 <= staleness_green_max_days <= "
                f"staleness_amber_max_days, got {self.staleness_green_max_days} "
                f"and {self.staleness_amber_max_days}")

        if not 0.0 < self.drift_psi_green_max <= self.drift_psi_amber_max:
            raise DashboardConfigError(
                "PSI bands must satisfy 0 < drift_psi_green_max <= "
                f"drift_psi_amber_max, got {self.drift_psi_green_max} and "
                f"{self.drift_psi_amber_max}")

        green_ms, amber_ms = self.latency_green_max_ms, self.latency_amber_max_ms
        if (green_ms is None) != (amber_ms is None):
            raise DashboardConfigError(
                "latency_green_max_ms and latency_amber_max_ms must be set "
                "together or left unset together")
        if green_ms is not None:
            for name, value in (("latency_green_max_ms", green_ms),
                                ("latency_amber_max_ms", amber_ms)):
                if isinstance(value, bool) or not isinstance(value, numbers.Real) \
                        or not math.isfinite(float(value)):
                    raise DashboardConfigError(f"{name} must be a finite number, got {value!r}")
            if not 0.0 < float(green_ms) <= float(amber_ms):
                raise DashboardConfigError(
                    "latency bands must satisfy 0 < latency_green_max_ms <= "
                    f"latency_amber_max_ms, got {green_ms} and {amber_ms}")

    @property
    def latency_budget_configured(self) -> bool:
        """True when both latency band edges are set."""
        return self.latency_green_max_ms is not None


@dataclass
class ModelHealthComponent:
    """One row of the stakeholder-facing dashboard.

    ``measured`` is False when the metric was not reported this cycle or has no
    configured budget. ``value`` is then ``None`` - never ``0.0``, which would
    put a number that was never computed in front of a risk reviewer.
    """

    name: str
    value: Optional[float]
    unit: str
    status: HealthStatus
    summary: str
    measured: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "status": self.status.value,
            "summary": self.summary,
            "measured": self.measured,
        }


@dataclass
class DashboardReport:
    model_name: str
    overall_status: HealthStatus
    components: List[ModelHealthComponent]
    recommended_action: str
    summary_message: str
    driving_components: List[str] = field(default_factory=list)
    latency_monitored: bool = True

    def to_dict(self) -> Dict[str, Any]:
        """JSON-serialisable view, for dashboard rendering and audit retention.

        Model monitoring evidence is one input to the RTS 6 Article 9 annual
        self-assessment and validation report; retain these snapshots rather
        than only the rendered colour.
        """
        return {
            "model_name": self.model_name,
            "overall_status": self.overall_status.value,
            "recommended_action": self.recommended_action,
            "summary_message": self.summary_message,
            "driving_components": list(self.driving_components),
            "latency_monitored": self.latency_monitored,
            "components": [c.to_dict() for c in self.components],
        }


def _require_finite_number(value: Any, label: str) -> float:
    """Coerce to float, rejecting bools, non-numerics, NaN and infinities.

    Accepts anything registered as ``numbers.Real`` so a ``numpy.float64`` or a
    pandas scalar pulled straight from a telemetry frame works, without this
    module taking a dependency on either. ``bool`` is an ``Integral`` and needs
    its own guard.
    """
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise DashboardInputError(f"{label} must be a real number, got {value!r}")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise DashboardInputError(
            f"{label} must be finite, got {value!r}. A NaN compares False against "
            "every threshold, so an unguarded ladder would report a healthy band.")
    return numeric


class NonTechnicalMonitoringDashboard:
    """Aggregate ML model telemetry into a GREEN / AMBER / RED stakeholder view.

    The dashboard exists to serve a reader who cannot interrogate a PSI figure:
    under MiFID II RTS 6 Article 16(2), real-time monitoring of algorithmic
    trading must be performed by a risk management or independent risk control
    function as well as by the trader, and under Article 2 compliance staff must
    have at least a general understanding of how the algorithms operate. Neither
    reader is served by a raw drift statistic.

    Args:
        thresholds: band edges. Defaults are conventions, not requirements.
        monitor_latency: whether inference latency is in scope for this
            dashboard. Left True with no configured latency budget, the latency
            component grades AMBER as unconfigured - a visible gap rather than a
            silent pass. Set False to declare latency explicitly out of scope
            (for example a daily-rebalance model), which records
            ``latency_monitored=False`` on every report.
    """

    def __init__(
        self,
        thresholds: Optional[DashboardThresholds] = None,
        monitor_latency: bool = True,
    ) -> None:
        if thresholds is not None and not isinstance(thresholds, DashboardThresholds):
            raise DashboardConfigError(
                f"thresholds must be a DashboardThresholds, got {type(thresholds).__name__}")
        if not isinstance(monitor_latency, bool):
            raise DashboardConfigError(
                f"monitor_latency must be a bool, got {monitor_latency!r}")
        self.thresholds: DashboardThresholds = thresholds or DashboardThresholds()
        self.monitor_latency: bool = monitor_latency

    def evaluate_health(
        self,
        model_name: str,
        accuracy_pct: Optional[float],
        staleness_days: Optional[int],
        feature_drift_psi: Optional[float],
        latency_ms: Optional[float] = None,
    ) -> DashboardReport:
        """Grade one model snapshot.

        Args:
            model_name: non-empty identifier shown on the dashboard.
            accuracy_pct: out-of-sample directional accuracy in **percentage
                points** (58.5, not 0.585), in [0, 100]. ``None`` if not
                reported this cycle.
            staleness_days: whole days since the model was last retrained, >= 0.
                ``None`` if not reported. A negative value is rejected: it means
                a clock skew or a bad `last_retrained_at`, not a fresh model.
            feature_drift_psi: population stability index across the monitored
                feature set, >= 0. Use the per-feature **maximum**, never the
                mean - averaging dilutes one broken feature below any threshold
                (see `concept-drift-vs-staleness-differentiation`). ``None`` if
                not reported.
            latency_ms: the inference latency statistic being governed, in
                milliseconds - use the p99, not the mean (see
                `model-inference-latency-budget-for-live-trading`). ``None`` if
                not reported. Ignored when ``monitor_latency`` is False.

        Returns:
            A ``DashboardReport`` whose ``overall_status`` is the worst
            component status and whose ``recommended_action`` is one of
            ``NO_ACTION_REQUIRED``, ``RESTORE_MODEL_TELEMETRY``,
            ``SCHEDULE_RETRAIN_AND_REVIEW`` or ``HALT_TRADING_IMMEDIATELY``.

        Raises:
            DashboardInputError: on an empty model name, or impossible,
                non-finite or mistyped telemetry.
        """
        if not isinstance(model_name, str) or not model_name.strip():
            raise DashboardInputError(
                f"model_name must be a non-empty string, got {model_name!r}")

        components: List[ModelHealthComponent] = [
            self._evaluate_accuracy(accuracy_pct),
            self._evaluate_staleness(staleness_days),
            self._evaluate_drift(feature_drift_psi),
        ]
        if self.monitor_latency:
            components.append(self._evaluate_latency(latency_ms))
        elif latency_ms is not None:
            logger.warning(
                "latency_ms=%s supplied for model '%s' but monitor_latency is False; "
                "the value is not graded and does not affect the dashboard status.",
                latency_ms, model_name)

        overall = max((c.status for c in components), key=lambda s: _SEVERITY[s])
        driving = [
            c.name for c in components
            if c.status is overall and overall is not HealthStatus.GREEN
        ]

        action, msg = self._recommend(model_name, overall, components, driving)
        if overall is HealthStatus.RED:
            logger.error(msg)
        elif overall is HealthStatus.AMBER:
            logger.warning(msg)
        else:
            logger.info(msg)

        return DashboardReport(
            model_name=model_name,
            overall_status=overall,
            components=components,
            recommended_action=action,
            summary_message=msg,
            driving_components=driving,
            latency_monitored=self.monitor_latency,
        )

    # -- component evaluators -------------------------------------------------

    @staticmethod
    def _unmeasured(name: str, unit: str, reason: str) -> ModelHealthComponent:
        return ModelHealthComponent(
            name=name, value=None, unit=unit, status=HealthStatus.AMBER,
            summary=reason, measured=False)

    def _evaluate_accuracy(self, accuracy_pct: Optional[float]) -> ModelHealthComponent:
        name, unit = "Prediction Accuracy", "%"
        if accuracy_pct is None:
            return self._unmeasured(
                name, unit, "Accuracy was not reported this cycle - unverified.")

        value = _require_finite_number(accuracy_pct, "accuracy_pct")
        if not 0.0 <= value <= 100.0:
            raise DashboardInputError(
                f"accuracy_pct must be in [0, 100] percentage points, got {value}")
        if 0.0 < value < 1.0:
            logger.warning(
                "accuracy_pct=%s looks like a fraction; this API expects percentage "
                "points (58.5, not 0.585). Grading it as %s%%.", value, value)

        thresholds = self.thresholds
        if value >= thresholds.accuracy_green_min_pct:
            status = HealthStatus.GREEN
            summary = f"Accuracy ({value:.1f}%) is at or above the configured target."
        elif value >= thresholds.accuracy_amber_min_pct:
            status = HealthStatus.AMBER
            summary = f"Accuracy ({value:.1f}%) is decaying towards the configured floor."
        else:
            status = HealthStatus.RED
            summary = (f"Accuracy ({value:.1f}%) is below the configured floor of "
                       f"{thresholds.accuracy_amber_min_pct:.1f}%.")
        return ModelHealthComponent(name, value, unit, status, summary)

    def _evaluate_staleness(self, staleness_days: Optional[int]) -> ModelHealthComponent:
        name, unit = "Model Age", "days"
        if staleness_days is None:
            return self._unmeasured(
                name, unit, "Model age was not reported this cycle - unverified.")

        # numbers.Integral, not int, so a numpy/pandas integer scalar is accepted.
        if isinstance(staleness_days, bool) or not isinstance(staleness_days, numbers.Integral):
            raise DashboardInputError(
                f"staleness_days must be a whole number of days, got {staleness_days!r}")
        staleness_days = int(staleness_days)
        if staleness_days < 0:
            raise DashboardInputError(
                f"staleness_days must be >= 0, got {staleness_days}. A negative age is a "
                "clock skew or a bad last-retrained timestamp, not a fresh model.")

        thresholds = self.thresholds
        if staleness_days <= thresholds.staleness_green_max_days:
            status = HealthStatus.GREEN
            summary = f"Model is fresh ({staleness_days} days since last retrain)."
        elif staleness_days <= thresholds.staleness_amber_max_days:
            status = HealthStatus.AMBER
            summary = f"Model is aging ({staleness_days} days since last retrain)."
        else:
            status = HealthStatus.RED
            summary = (f"Model is stale ({staleness_days} days since last retrain, "
                       f"limit {thresholds.staleness_amber_max_days}).")
        return ModelHealthComponent(name, float(staleness_days), unit, status, summary)

    def _evaluate_drift(self, feature_drift_psi: Optional[float]) -> ModelHealthComponent:
        name, unit = "Feature Drift PSI", "PSI"
        if feature_drift_psi is None:
            return self._unmeasured(
                name, unit, "Feature drift was not reported this cycle - unverified.")

        value = _require_finite_number(feature_drift_psi, "feature_drift_psi")
        if value < 0.0:
            raise DashboardInputError(
                f"feature_drift_psi must be >= 0, got {value}. PSI is non-negative by "
                "construction, so a negative value means a broken drift computation.")

        thresholds = self.thresholds
        if value < thresholds.drift_psi_green_max:
            status = HealthStatus.GREEN
            summary = f"Feature drift (PSI={value:.2f}) is low."
        elif value < thresholds.drift_psi_amber_max:
            status = HealthStatus.AMBER
            summary = f"Moderate feature drift (PSI={value:.2f}) detected."
        else:
            status = HealthStatus.RED
            summary = (f"Severe feature drift (PSI={value:.2f}) detected, at or above "
                       f"{thresholds.drift_psi_amber_max:.2f}.")
        return ModelHealthComponent(name, value, unit, status, summary)

    def _evaluate_latency(self, latency_ms: Optional[float]) -> ModelHealthComponent:
        name, unit = "Inference Latency", "ms"
        thresholds = self.thresholds
        if not thresholds.latency_budget_configured:
            return self._unmeasured(
                name, unit,
                "No inference-latency budget configured - latency is not being "
                "monitored. Set latency_green_max_ms/latency_amber_max_ms, or pass "
                "monitor_latency=False to declare it out of scope.")
        if latency_ms is None:
            return self._unmeasured(
                name, unit, "Inference latency was not reported this cycle - unverified.")

        value = _require_finite_number(latency_ms, "latency_ms")
        if value < 0.0:
            raise DashboardInputError(f"latency_ms must be >= 0, got {value}")

        green_ms = float(thresholds.latency_green_max_ms)
        amber_ms = float(thresholds.latency_amber_max_ms)
        if value <= green_ms:
            status = HealthStatus.GREEN
            summary = f"Inference latency ({value:.1f}ms) is within budget."
        elif value <= amber_ms:
            status = HealthStatus.AMBER
            summary = (f"Inference latency ({value:.1f}ms) is approaching the "
                       f"{amber_ms:.1f}ms limit.")
        else:
            status = HealthStatus.RED
            summary = (f"Inference latency ({value:.1f}ms) has breached the "
                       f"{amber_ms:.1f}ms limit.")
        return ModelHealthComponent(name, value, unit, status, summary)

    # -- recommendation -------------------------------------------------------

    @staticmethod
    def _recommend(
        model_name: str,
        overall: HealthStatus,
        components: List[ModelHealthComponent],
        driving: List[str],
    ) -> Tuple[str, str]:
        """Pick the action and compose the headline.

        The headline names the components that drove the status. A colour with
        no named cause is not actionable by a non-technical reader, and forces
        them back to the raw telemetry this dashboard exists to replace.
        """
        named = ", ".join(driving)
        if overall is HealthStatus.RED:
            return ACTION_HALT, (
                f"CRITICAL DASHBOARD ALERT: Model '{model_name}' STATUS IS RED. "
                f"Breaching: {named}. Recommended action: {ACTION_HALT} (advisory - "
                "execute via the kill switch; this dashboard halts nothing).")

        if overall is HealthStatus.AMBER:
            amber = [c for c in components if c.status is HealthStatus.AMBER]
            # Recommending a retrain because a metric is missing sends the operator
            # to fix the model when the actual fault is in the telemetry pipeline.
            if all(not c.measured for c in amber):
                return ACTION_RESTORE_TELEMETRY, (
                    f"DASHBOARD WARNING: Model '{model_name}' STATUS IS AMBER because "
                    f"health metrics were not measured: {named}. Recommended action: "
                    f"{ACTION_RESTORE_TELEMETRY}. Model quality is unverified, "
                    "not proven good.")
            return ACTION_RETRAIN, (
                f"DASHBOARD WARNING: Model '{model_name}' STATUS IS AMBER. "
                f"Attention: {named}. Recommended action: {ACTION_RETRAIN}.")

        return ACTION_NONE, f"DASHBOARD OK: Model '{model_name}' STATUS IS GREEN."
