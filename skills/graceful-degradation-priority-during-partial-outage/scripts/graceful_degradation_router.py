"""Priority load-shedding decision engine for partial trading-system outages.

This module is a *decision* engine, not a scheduler. Given a snapshot of system
health and a batch of pending trading tasks, it returns the disposition of every
task -- ``PROCESS``, ``DEFER`` or ``DROP`` -- plus an audit report. Enacting those
decisions (dispatching, re-queuing, discarding) belongs to the caller.

Three properties matter more than throughput here, and each is enforced rather
than merely documented:

* **P1 can never be shed.** A task whose priority string cannot be parsed raises
  instead of being silently reclassified, and any policy that would shed P1 is
  rejected at construction.
* **Shedding is monotone in priority.** A priority is only shed once every lower
  priority is already shed (Google SRE, *Handling Overload*: a backend rejects a
  given criticality only if it is already rejecting all lower criticalities).
* **Unreadable health telemetry escalates.** A metric the configuration depends
  on that is missing, or a health sample that is too old, is treated as
  ``CRITICAL_OUTAGE`` -- never as healthy.

Priority levels:

* ``P1_CRITICAL``: risk-limit checks, emergency stop / mass-cancel, heartbeats.
* ``P2_HIGH``: position exit orders, stop-loss executions, fill reconciliation.
* ``P3_MEDIUM``: new signal entry orders, child order slices.
* ``P4_LOW``: non-critical analytics, historical tick logging, GUI streaming.

Deferred P2 work is an *outstanding order or position that still needs managing*
(MiFID II RTS 6, Art. 14(2)(g)), which is why the report raises
``manual_intervention_required`` rather than reporting a clean shed.
"""
from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Mapping, Optional, Sequence, Tuple, Union

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class LoadSheddingConfigurationError(ValueError):
    """Raised when engine thresholds or a shedding policy are internally invalid."""


class UnknownTaskPriorityError(ValueError):
    """Raised when a task priority cannot be mapped to a known tier.

    Deliberately fatal: an unclassifiable task must never be silently shed
    (it could be a mass-cancel) nor silently processed (it could be analytics).
    """


class InvalidHealthMetricError(ValueError):
    """Raised when a health metric is present but not a usable measurement."""


class TaskPriority(str, Enum):
    """Task criticality tier. Lower ``rank`` is more critical."""

    P1_CRITICAL = "P1_CRITICAL"
    P2_HIGH = "P2_HIGH"
    P3_MEDIUM = "P3_MEDIUM"
    P4_LOW = "P4_LOW"

    @property
    def rank(self) -> int:
        return _PRIORITY_RANK[self]

    @classmethod
    def parse(cls, value: Union[str, "TaskPriority"]) -> "TaskPriority":
        """Strictly map a priority label to a tier, raising on anything unknown."""
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise UnknownTaskPriorityError(
                f"Task priority must be a string or TaskPriority, got {type(value).__name__}."
            )
        try:
            return cls(value.strip().upper())
        except ValueError as exc:
            raise UnknownTaskPriorityError(
                f"Unknown task priority {value!r}. Expected one of "
                f"{[p.value for p in cls]}. Unclassifiable tasks are never routed: "
                "a mis-tagged mass-cancel must not be shed as analytics."
            ) from exc


_PRIORITY_RANK: Dict[TaskPriority, int] = {
    TaskPriority.P1_CRITICAL: 1,
    TaskPriority.P2_HIGH: 2,
    TaskPriority.P3_MEDIUM: 3,
    TaskPriority.P4_LOW: 4,
}


class SystemMode(str, Enum):
    """Operating mode. Higher ``severity`` sheds more work."""

    NORMAL_HEALTHY = "NORMAL_HEALTHY"
    PARTIAL_DEGRADATION = "PARTIAL_DEGRADATION"
    CRITICAL_OUTAGE = "CRITICAL_OUTAGE"

    @property
    def severity(self) -> int:
        return _MODE_SEVERITY[self]


_MODE_SEVERITY: Dict[SystemMode, int] = {
    SystemMode.NORMAL_HEALTHY: 0,
    SystemMode.PARTIAL_DEGRADATION: 1,
    SystemMode.CRITICAL_OUTAGE: 2,
}
_MODE_BY_SEVERITY: Dict[int, SystemMode] = {v: k for k, v in _MODE_SEVERITY.items()}


class TaskDisposition(str, Enum):
    """What the caller must do with a task.

    ``DEFER`` is not ``DROP``: the work still exists and still has to be done,
    by the system once it recovers or by a human if it does not.
    """

    PROCESS = "PROCESS"
    DEFER = "DEFER"
    DROP = "DROP"

    @property
    def shed_severity(self) -> int:
        return _DISPOSITION_SEVERITY[self]


_DISPOSITION_SEVERITY: Dict[TaskDisposition, int] = {
    TaskDisposition.PROCESS: 0,
    TaskDisposition.DEFER: 1,
    TaskDisposition.DROP: 2,
}


@dataclass
class TradingTask:
    """A unit of work awaiting a routing decision."""

    task_id: str
    task_type: str          # e.g. 'MASS_CANCEL', 'STOP_LOSS_EXIT', 'NEW_ENTRY_ORDER'
    priority: Union[str, TaskPriority]
    payload: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError("TradingTask.task_id must be a non-empty string.")
        self.priority = TaskPriority.parse(self.priority)


@dataclass(frozen=True)
class SystemHealthMetrics:
    """A health sample: an immutable point-in-time measurement.

    ``None`` means *this metric could not be read*, which is not the same as
    zero. A metric the engine's configuration depends on that reads ``None``
    escalates the system to ``CRITICAL_OUTAGE``.

    ``sample_age_seconds`` is the age of the sample at decision time; it is only
    checked when the engine is configured with ``max_health_sample_age_seconds``.

    Frozen deliberately: values are validated once at construction, so a sampler
    thread cannot write a ``NaN`` into a sample that has already been validated.
    Build a new instance per sample.
    """

    cpu_utilization_pct: Optional[float]
    network_packet_loss_pct: Optional[float]
    db_connection_latency_ms: Optional[float]
    sample_age_seconds: Optional[float] = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "cpu_utilization_pct",
            _validate_pct("cpu_utilization_pct", self.cpu_utilization_pct),
        )
        object.__setattr__(
            self, "network_packet_loss_pct",
            _validate_pct("network_packet_loss_pct", self.network_packet_loss_pct),
        )
        object.__setattr__(
            self, "db_connection_latency_ms",
            _validate_non_negative("db_connection_latency_ms", self.db_connection_latency_ms),
        )
        object.__setattr__(
            self, "sample_age_seconds",
            _validate_non_negative("sample_age_seconds", self.sample_age_seconds),
        )


def _validate_pct(name: str, value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    value = _validate_finite(name, value)
    if not 0.0 <= value <= 100.0:
        raise InvalidHealthMetricError(f"{name} must be within [0, 100], got {value}.")
    return value


def _validate_non_negative(name: str, value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    value = _validate_finite(name, value)
    if value < 0.0:
        raise InvalidHealthMetricError(f"{name} must be >= 0, got {value}.")
    return value


def _validate_finite(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidHealthMetricError(
            f"{name} must be a real number or None, got {type(value).__name__}."
        )
    value = float(value)
    if not math.isfinite(value):
        # NaN compares False against every threshold, so an unguarded NaN reads
        # as "healthy" and disables load shedding entirely.
        raise InvalidHealthMetricError(
            f"{name} must be finite, got {value!r}. Pass None for an unreadable "
            "metric so the engine can fail safe instead of reading it as healthy."
        )
    return value


@dataclass
class GracefulDegradationAuditReport:
    """Routing decision for one batch, in a form that can be logged verbatim."""

    system_mode: SystemMode
    total_tasks_received: int
    processed_tasks_count: int
    shed_tasks_count: int                       # deferred + dropped
    processed_task_ids: List[str]               # strict priority order
    shed_task_ids: List[str]                    # deferred first, then dropped
    audit_notes: str
    deferred_task_ids: List[str] = field(default_factory=list)
    dropped_task_ids: List[str] = field(default_factory=list)
    deferred_tasks_count: int = 0
    dropped_tasks_count: int = 0
    dispositions: Dict[str, TaskDisposition] = field(default_factory=dict)
    instantaneous_mode: Optional[SystemMode] = None   # before recovery damping
    previous_mode: Optional[SystemMode] = None
    mode_changed: bool = False
    manual_intervention_required: bool = False
    telemetry_age_verified: bool = False
    classification_reasons: List[str] = field(default_factory=list)


#: Default policy. Read down a column: a priority is only shed once every lower
#: priority is already shed, and P1 is never shed in any mode.
DEFAULT_SHEDDING_POLICY: Dict[SystemMode, Dict[TaskPriority, TaskDisposition]] = {
    SystemMode.NORMAL_HEALTHY: {
        TaskPriority.P1_CRITICAL: TaskDisposition.PROCESS,
        TaskPriority.P2_HIGH: TaskDisposition.PROCESS,
        TaskPriority.P3_MEDIUM: TaskDisposition.PROCESS,
        TaskPriority.P4_LOW: TaskDisposition.PROCESS,
    },
    SystemMode.PARTIAL_DEGRADATION: {
        TaskPriority.P1_CRITICAL: TaskDisposition.PROCESS,
        TaskPriority.P2_HIGH: TaskDisposition.PROCESS,
        TaskPriority.P3_MEDIUM: TaskDisposition.DEFER,   # new entries wait
        TaskPriority.P4_LOW: TaskDisposition.DROP,       # analytics are lost, by design
    },
    SystemMode.CRITICAL_OUTAGE: {
        TaskPriority.P1_CRITICAL: TaskDisposition.PROCESS,
        TaskPriority.P2_HIGH: TaskDisposition.DEFER,     # exits still need managing
        TaskPriority.P3_MEDIUM: TaskDisposition.DROP,    # stale alpha, do not replay
        TaskPriority.P4_LOW: TaskDisposition.DROP,
    },
}


class GracefulDegradationRouterEngine:
    """Routes trading tasks under partial outage using a strict priority hierarchy.

    Mode escalation is immediate; recovery is damped. After a degradation the
    engine steps down one severity level only after ``recovery_confirmation_samples``
    consecutive healthier samples, because a system that has just dipped back
    under its overload threshold is not yet recovered (Google SRE, *Addressing
    Cascading Failures*). Consequently ``process_and_filter_tasks`` is
    **stateful**: the mode it applies depends on the samples seen before it.
    ``determine_system_mode`` stays pure and can be queried freely.

    Thresholds are compared with ``>=``: a sample sitting exactly on a threshold
    degrades. The instance is safe to share across threads in one process; two
    processes each hold their own mode state.
    """

    def __init__(
        self,
        partial_degradation_cpu_pct: float = 75.0,
        partial_degradation_packet_loss_pct: float = 1.0,
        critical_outage_packet_loss_pct: float = 10.0,
        critical_outage_cpu_pct: float = 90.0,
        partial_degradation_db_latency_ms: Optional[float] = None,
        critical_outage_db_latency_ms: Optional[float] = None,
        max_health_sample_age_seconds: Optional[float] = None,
        recovery_confirmation_samples: int = 3,
        policy: Optional[Mapping[SystemMode, Mapping[TaskPriority, TaskDisposition]]] = None,
    ) -> None:
        """Configure thresholds.

        The DB-latency and sample-age checks default to ``None`` (disabled).
        No regulator or standards body publishes a database-latency figure at
        which a trading system must enter capital preservation, so the engine
        ships without an invented one: set them from your own measured
        percentiles before relying on DB-driven degradation.
        """
        self.partial_degradation_cpu_pct = _validate_threshold_pct(
            "partial_degradation_cpu_pct", partial_degradation_cpu_pct
        )
        self.critical_outage_cpu_pct = _validate_threshold_pct(
            "critical_outage_cpu_pct", critical_outage_cpu_pct
        )
        self.partial_degradation_packet_loss_pct = _validate_threshold_pct(
            "partial_degradation_packet_loss_pct", partial_degradation_packet_loss_pct
        )
        self.critical_outage_packet_loss_pct = _validate_threshold_pct(
            "critical_outage_packet_loss_pct", critical_outage_packet_loss_pct
        )
        self.partial_degradation_db_latency_ms = _validate_optional_threshold(
            "partial_degradation_db_latency_ms", partial_degradation_db_latency_ms
        )
        self.critical_outage_db_latency_ms = _validate_optional_threshold(
            "critical_outage_db_latency_ms", critical_outage_db_latency_ms
        )
        self.max_health_sample_age_seconds = _validate_optional_threshold(
            "max_health_sample_age_seconds", max_health_sample_age_seconds
        )

        _require_ordered("cpu", self.partial_degradation_cpu_pct, self.critical_outage_cpu_pct)
        _require_ordered(
            "packet loss",
            self.partial_degradation_packet_loss_pct,
            self.critical_outage_packet_loss_pct,
        )
        _require_ordered(
            "db latency",
            self.partial_degradation_db_latency_ms,
            self.critical_outage_db_latency_ms,
        )

        if not isinstance(recovery_confirmation_samples, int) or isinstance(
            recovery_confirmation_samples, bool
        ):
            raise LoadSheddingConfigurationError(
                "recovery_confirmation_samples must be an int."
            )
        if recovery_confirmation_samples < 1:
            raise LoadSheddingConfigurationError(
                "recovery_confirmation_samples must be >= 1; 0 would restore full "
                "load the instant one sample dips below the threshold."
            )
        self.recovery_confirmation_samples = recovery_confirmation_samples

        self.policy = _validate_policy(policy if policy is not None else DEFAULT_SHEDDING_POLICY)

        self._lock = threading.RLock()
        self._current_mode = SystemMode.NORMAL_HEALTHY
        self._healthier_sample_streak = 0

    # ---------------------------------------------------------------- state

    @property
    def current_mode(self) -> SystemMode:
        """Mode currently in force, including recovery damping."""
        with self._lock:
            return self._current_mode

    def reset_mode_state(self, mode: SystemMode = SystemMode.NORMAL_HEALTHY) -> None:
        """Force the latched mode (e.g. after an operator-confirmed recovery)."""
        with self._lock:
            self._current_mode = SystemMode(mode)
            self._healthier_sample_streak = 0

    # ------------------------------------------------------- classification

    def determine_system_mode(self, health: SystemHealthMetrics) -> SystemMode:
        """Classify a single health sample. Pure: does not touch engine state."""
        return self._classify(health)[0]

    def _classify(self, health: SystemHealthMetrics) -> Tuple[SystemMode, List[str]]:
        if not isinstance(health, SystemHealthMetrics):
            raise TypeError("health must be a SystemHealthMetrics instance.")

        reasons: List[str] = []
        severity = _MODE_SEVERITY[SystemMode.NORMAL_HEALTHY]

        def escalate(mode: SystemMode, reason: str) -> None:
            nonlocal severity
            severity = max(severity, _MODE_SEVERITY[mode])
            reasons.append(reason)

        # Freshness first: a stale sample makes every value below meaningless.
        if self.max_health_sample_age_seconds is not None:
            if health.sample_age_seconds is None:
                escalate(
                    SystemMode.CRITICAL_OUTAGE,
                    "health sample age unknown while a freshness limit is configured",
                )
            elif health.sample_age_seconds >= self.max_health_sample_age_seconds:
                escalate(
                    SystemMode.CRITICAL_OUTAGE,
                    f"health sample is {health.sample_age_seconds:.1f}s old "
                    f"(limit {self.max_health_sample_age_seconds:.1f}s)",
                )

        if health.cpu_utilization_pct is None:
            escalate(SystemMode.CRITICAL_OUTAGE, "cpu_utilization_pct unreadable")
        elif health.cpu_utilization_pct >= self.critical_outage_cpu_pct:
            escalate(
                SystemMode.CRITICAL_OUTAGE,
                f"CPU {health.cpu_utilization_pct:.1f}% >= {self.critical_outage_cpu_pct:.1f}%",
            )
        elif health.cpu_utilization_pct >= self.partial_degradation_cpu_pct:
            escalate(
                SystemMode.PARTIAL_DEGRADATION,
                f"CPU {health.cpu_utilization_pct:.1f}% >= {self.partial_degradation_cpu_pct:.1f}%",
            )

        if health.network_packet_loss_pct is None:
            escalate(SystemMode.CRITICAL_OUTAGE, "network_packet_loss_pct unreadable")
        elif health.network_packet_loss_pct >= self.critical_outage_packet_loss_pct:
            escalate(
                SystemMode.CRITICAL_OUTAGE,
                f"packet loss {health.network_packet_loss_pct:.1f}% >= "
                f"{self.critical_outage_packet_loss_pct:.1f}%",
            )
        elif health.network_packet_loss_pct >= self.partial_degradation_packet_loss_pct:
            escalate(
                SystemMode.PARTIAL_DEGRADATION,
                f"packet loss {health.network_packet_loss_pct:.1f}% >= "
                f"{self.partial_degradation_packet_loss_pct:.1f}%",
            )

        db_thresholds_configured = (
            self.partial_degradation_db_latency_ms is not None
            or self.critical_outage_db_latency_ms is not None
        )
        if db_thresholds_configured:
            if health.db_connection_latency_ms is None:
                escalate(
                    SystemMode.CRITICAL_OUTAGE,
                    "db_connection_latency_ms unreadable while a DB threshold is configured",
                )
            elif (
                self.critical_outage_db_latency_ms is not None
                and health.db_connection_latency_ms >= self.critical_outage_db_latency_ms
            ):
                escalate(
                    SystemMode.CRITICAL_OUTAGE,
                    f"DB latency {health.db_connection_latency_ms:.1f}ms >= "
                    f"{self.critical_outage_db_latency_ms:.1f}ms",
                )
            elif (
                self.partial_degradation_db_latency_ms is not None
                and health.db_connection_latency_ms >= self.partial_degradation_db_latency_ms
            ):
                escalate(
                    SystemMode.PARTIAL_DEGRADATION,
                    f"DB latency {health.db_connection_latency_ms:.1f}ms >= "
                    f"{self.partial_degradation_db_latency_ms:.1f}ms",
                )

        if not reasons:
            reasons.append("all monitored metrics within healthy thresholds")
        return _MODE_BY_SEVERITY[severity], reasons

    def _telemetry_incomplete(self, health: SystemHealthMetrics) -> bool:
        """True when a metric the configuration depends on could not be read."""
        if health.cpu_utilization_pct is None or health.network_packet_loss_pct is None:
            return True
        db_configured = (
            self.partial_degradation_db_latency_ms is not None
            or self.critical_outage_db_latency_ms is not None
        )
        if db_configured and health.db_connection_latency_ms is None:
            return True
        if self.max_health_sample_age_seconds is not None and health.sample_age_seconds is None:
            return True
        return False

    def _apply_recovery_damping(self, instantaneous: SystemMode) -> SystemMode:
        """Escalate immediately; step down one level per confirmed recovery run."""
        current = self._current_mode
        if instantaneous.severity >= current.severity:
            self._healthier_sample_streak = 0
            self._current_mode = instantaneous
            return self._current_mode

        self._healthier_sample_streak += 1
        if self._healthier_sample_streak >= self.recovery_confirmation_samples:
            self._healthier_sample_streak = 0
            next_severity = max(instantaneous.severity, current.severity - 1)
            self._current_mode = _MODE_BY_SEVERITY[next_severity]
        return self._current_mode

    # ------------------------------------------------------------- routing

    def process_and_filter_tasks(
        self,
        health: SystemHealthMetrics,
        tasks: Sequence[TradingTask],
    ) -> GracefulDegradationAuditReport:
        """Route one batch of tasks under the mode implied by ``health``.

        Returns the disposition of every task. ``processed_task_ids`` is ordered
        by priority (P1 first, input order preserved within a tier) so a caller
        dispatching in list order dispatches risk work first.

        Raises ``UnknownTaskPriorityError`` if any task carries an unparseable
        priority: the batch is rejected whole rather than routed with a task
        whose criticality is unknown.
        """
        if isinstance(tasks, (str, bytes)) or not isinstance(tasks, Sequence):
            raise TypeError("tasks must be a sequence of TradingTask.")

        ordered: List[Tuple[int, int, TradingTask]] = []
        seen_ids: Dict[str, int] = {}
        for index, task in enumerate(tasks):
            if not isinstance(task, TradingTask):
                raise TypeError(
                    f"tasks[{index}] must be a TradingTask, got {type(task).__name__}."
                )
            priority = TaskPriority.parse(task.priority)
            seen_ids[task.task_id] = seen_ids.get(task.task_id, 0) + 1
            ordered.append((priority.rank, index, task))
        ordered.sort(key=lambda item: (item[0], item[1]))

        duplicates = sorted(tid for tid, count in seen_ids.items() if count > 1)
        if duplicates:
            logger.warning(
                "Duplicate task_id(s) in batch: %s. Dispositions collapse per id; "
                "de-duplicate upstream to avoid double dispatch.",
                duplicates,
            )

        instantaneous, reasons = self._classify(health)
        with self._lock:
            previous_mode = self._current_mode
            mode = self._apply_recovery_damping(instantaneous)

        processed: List[str] = []
        deferred: List[str] = []
        dropped: List[str] = []
        dispositions: Dict[str, TaskDisposition] = {}
        manual_intervention = False

        mode_policy = self.policy[mode]
        for _rank, _index, task in ordered:
            priority = TaskPriority.parse(task.priority)
            disposition = mode_policy[priority]
            dispositions[task.task_id] = disposition
            if disposition is TaskDisposition.PROCESS:
                processed.append(task.task_id)
            elif disposition is TaskDisposition.DEFER:
                deferred.append(task.task_id)
                manual_intervention = manual_intervention or priority.rank <= 2
            else:
                dropped.append(task.task_id)
                manual_intervention = manual_intervention or priority.rank <= 2

        telemetry_age_verified = (
            self.max_health_sample_age_seconds is not None
            and health.sample_age_seconds is not None
            and health.sample_age_seconds < self.max_health_sample_age_seconds
        )
        manual_intervention = manual_intervention or self._telemetry_incomplete(health)

        shed = deferred + dropped
        mode_changed = mode is not previous_mode
        notes = self._build_notes(
            mode=mode,
            instantaneous=instantaneous,
            previous_mode=previous_mode,
            reasons=reasons,
            processed=processed,
            deferred=deferred,
            dropped=dropped,
            manual_intervention=manual_intervention,
        )

        if mode is SystemMode.CRITICAL_OUTAGE:
            logger.critical("%s", notes)
        elif mode is SystemMode.PARTIAL_DEGRADATION:
            logger.warning("%s", notes)
        elif mode_changed:
            logger.info("%s", notes)
        else:
            logger.debug("%s", notes)

        if mode_changed:
            logger.warning(
                "SYSTEM MODE TRANSITION: %s -> %s (instantaneous classification %s).",
                previous_mode.value,
                mode.value,
                instantaneous.value,
            )

        return GracefulDegradationAuditReport(
            system_mode=mode,
            total_tasks_received=len(ordered),
            processed_tasks_count=len(processed),
            shed_tasks_count=len(shed),
            processed_task_ids=processed,
            shed_task_ids=shed,
            audit_notes=notes,
            deferred_task_ids=deferred,
            dropped_task_ids=dropped,
            deferred_tasks_count=len(deferred),
            dropped_tasks_count=len(dropped),
            dispositions=dispositions,
            instantaneous_mode=instantaneous,
            previous_mode=previous_mode,
            mode_changed=mode_changed,
            manual_intervention_required=manual_intervention,
            telemetry_age_verified=telemetry_age_verified,
            classification_reasons=reasons,
        )

    def _build_notes(
        self,
        mode: SystemMode,
        instantaneous: SystemMode,
        previous_mode: SystemMode,
        reasons: Sequence[str],
        processed: Sequence[str],
        deferred: Sequence[str],
        dropped: Sequence[str],
        manual_intervention: bool,
    ) -> str:
        reason_text = "; ".join(reasons)
        if mode is SystemMode.CRITICAL_OUTAGE:
            headline = "CRITICAL OUTAGE - CAPITAL PRESERVATION MODE"
        elif mode is SystemMode.PARTIAL_DEGRADATION:
            headline = "PARTIAL DEGRADATION - LOAD SHEDDING ACTIVE"
        else:
            headline = "NORMAL HEALTHY"
        damping = (
            ""
            if mode is instantaneous
            else (
                f" Recovery damping in force: sample classified {instantaneous.value}, "
                f"holding {mode.value} for {self.recovery_confirmation_samples} "
                "consecutive healthier samples per step."
            )
        )
        escalation = (
            " MANUAL INTERVENTION REQUIRED: P1/P2 work was shed or health telemetry "
            "was unreadable - outstanding orders and positions need an alternative "
            "arrangement."
            if manual_intervention
            else ""
        )
        return (
            f"{headline} (previous mode {previous_mode.value}). Triggers: {reason_text}. "
            f"Processed {len(processed)}, deferred {len(deferred)}, dropped {len(dropped)}."
            f"{damping}{escalation}"
        )


def _validate_threshold_pct(name: str, value: float) -> float:
    value = _validate_threshold_number(name, value)
    if not 0.0 <= value <= 100.0:
        raise LoadSheddingConfigurationError(f"{name} must be within [0, 100], got {value}.")
    return value


def _validate_threshold_number(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LoadSheddingConfigurationError(
            f"{name} must be a real number, got {type(value).__name__}."
        )
    value = float(value)
    if not math.isfinite(value):
        raise LoadSheddingConfigurationError(f"{name} must be finite, got {value!r}.")
    return value


def _validate_optional_threshold(name: str, value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    value = _validate_threshold_number(name, value)
    if value < 0.0:
        raise LoadSheddingConfigurationError(f"{name} must be >= 0, got {value}.")
    return value


def _require_ordered(label: str, partial: Optional[float], critical: Optional[float]) -> None:
    if partial is None or critical is None:
        return
    if critical < partial:
        raise LoadSheddingConfigurationError(
            f"critical {label} threshold ({critical}) must be >= the partial "
            f"degradation threshold ({partial}); otherwise the partial band is "
            "unreachable and degradation jumps straight to capital preservation."
        )


def _validate_policy(
    policy: Mapping[SystemMode, Mapping[TaskPriority, TaskDisposition]],
) -> Dict[SystemMode, Dict[TaskPriority, TaskDisposition]]:
    """Reject any policy that could shed P1 or shed out of priority order."""
    validated: Dict[SystemMode, Dict[TaskPriority, TaskDisposition]] = {}
    for mode in SystemMode:
        if mode not in policy:
            raise LoadSheddingConfigurationError(f"Policy is missing mode {mode.value}.")
        mode_policy = policy[mode]
        row: Dict[TaskPriority, TaskDisposition] = {}
        for priority in TaskPriority:
            if priority not in mode_policy:
                raise LoadSheddingConfigurationError(
                    f"Policy for {mode.value} is missing priority {priority.value}."
                )
            disposition = mode_policy[priority]
            if not isinstance(disposition, TaskDisposition):
                raise LoadSheddingConfigurationError(
                    f"Policy for {mode.value}/{priority.value} must be a TaskDisposition."
                )
            row[priority] = disposition

        if row[TaskPriority.P1_CRITICAL] is not TaskDisposition.PROCESS:
            raise LoadSheddingConfigurationError(
                f"Policy for {mode.value} sheds P1_CRITICAL. P1 carries risk-limit "
                "checks and mass-cancels and must be processed in every mode."
            )

        previous = TaskDisposition.PROCESS
        for priority in sorted(TaskPriority, key=lambda p: p.rank):
            disposition = row[priority]
            if disposition.shed_severity < previous.shed_severity:
                raise LoadSheddingConfigurationError(
                    f"Policy for {mode.value} sheds {priority.value} less than a "
                    "higher-priority tier. Shedding must be monotone in priority: "
                    "a tier is only shed once every lower tier is already shed."
                )
            previous = disposition
        validated[mode] = row
    return validated
