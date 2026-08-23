"""Shared-infrastructure resource contention manager for co-located trading strategies.

This module is an *advisory* control plane. It ingests host/gateway telemetry,
classifies the contention state, and emits mitigation directives plus a status
label per registered strategy. It does NOT itself set CPU affinity, suspend a
process, cancel orders, or rate-limit a FIX session - the caller is responsible
for enforcing every directive it returns. See SKILL.md ("When NOT to Use").
"""

import logging
import math
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Priority classes, ordered most-critical first. The order is load-bearing: the
# defensive fallback for an unrecognised class treats it as the LAST entry.
PRIORITY_HIGH_HFT = "HIGH_HFT"
PRIORITY_MEDIUM_ARB = "MEDIUM_ARB"
PRIORITY_LOW_BATCH = "LOW_BATCH"
VALID_PRIORITY_LEVELS: Tuple[str, ...] = (
    PRIORITY_HIGH_HFT,
    PRIORITY_MEDIUM_ARB,
    PRIORITY_LOW_BATCH,
)

STATUS_RUNNING = "RUNNING"
STATUS_THROTTLED = "THROTTLED"
STATUS_PAUSED = "PAUSED"
VALID_STATUSES: Tuple[str, ...] = (STATUS_RUNNING, STATUS_THROTTLED, STATUS_PAUSED)

STATE_NORMAL = "NORMAL"
STATE_ELEVATED = "ELEVATED"
STATE_CRITICAL = "CRITICAL_CONTENTION"

RESOURCE_CPU = "CPU"
RESOURCE_RAM = "RAM"
RESOURCE_FIX_GATEWAY = "FIX_GATEWAY"


def _require_utilization_pct(value: float, label: str) -> float:
    """Validate a percentage telemetry reading, which must lie in [0, 100].

    NaN is the dangerous case: every ``>=`` comparison against NaN is False, so
    an unvalidated NaN would classify a saturated host as NORMAL and silently
    disable preemption. Readings above 100% are rejected because the usual cause
    is an un-normalised ``top``-style aggregate (e.g. 400% on a 4-core box),
    which would otherwise pin the manager to CRITICAL forever. FIX-gateway
    utilisation is derived rather than ingested and may exceed 100%.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a real number, got {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite, got {value!r}")
    if value < 0.0:
        raise ValueError(f"{label} must be non-negative, got {value!r}")
    if value > 100.0:
        raise ValueError(
            f"{label} must be a host-normalised percentage in [0, 100], got {value!r}. "
            "Divide multi-core aggregates by the core count before passing them in."
        )
    return float(value)


def _require_non_negative_rate(value: float, label: str) -> float:
    """Validate a msgs/sec rate or unitless factor: real, finite, >= 0."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a real number, got {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite, got {value!r}")
    if value < 0.0:
        raise ValueError(f"{label} must be non-negative, got {value!r}")
    return float(value)


@dataclass
class StrategyProcessInfo:
    """Telemetry and classification for one co-located strategy process."""

    strategy_id: str
    priority_level: str                 # one of VALID_PRIORITY_LEVELS
    pinned_cpu_core: int
    current_cpu_utilization_pct: float
    current_msg_rate_per_sec: float
    status: str                         # one of VALID_STATUSES
    # Steady-state message rate this strategy is provisioned for. Throttle caps
    # are expressed against this baseline; when it is None the manager falls
    # back to the observed rate and says so in the directive.
    baseline_msg_rate_per_sec: Optional[float] = None


@dataclass
class ContentionMitigationReport:
    """Outcome of one telemetry evaluation. Every directive is advisory."""

    contention_state: str               # STATE_NORMAL | STATE_ELEVATED | STATE_CRITICAL
    overall_cpu_pct: float
    overall_ram_pct: float
    total_fix_msg_rate_sec: float
    throttled_strategies: List[str]
    paused_strategies: List[str]
    mitigation_directives: List[str]
    # Added so the escalation decision is auditable: which resource was binding,
    # and at what level. Defaulted, to keep positional construction compatible.
    fix_gateway_utilization_pct: float = 0.0
    max_utilization_pct: float = 0.0
    binding_resource: str = RESOURCE_CPU
    resumed_strategies: List[str] = field(default_factory=list)
    throttle_caps_msg_per_sec: Dict[str, float] = field(default_factory=dict)


class SharedInfrastructureContentionManager:
    """Advisory contention manager for strategies sharing a host and a FIX gateway.

    Escalation is driven by the single most-loaded shared resource (``max`` of
    CPU %, RAM %, and FIX-gateway rate as a % of the negotiated limit), not an
    average - averaging hides a saturated gateway behind an idle CPU.

    De-escalation is deliberately asymmetric. Preemption engages the instant
    utilisation reaches ``critical_threshold_pct``, but suppressed strategies are
    released only after ``resume_clear_samples`` consecutive samples strictly
    below ``resume_threshold_pct``. Without that hysteresis a host oscillating
    around the critical threshold would pause and resume batch work on every
    sample, and each resume would immediately re-saturate the resource that
    triggered the pause.

    Thread safety: registration and evaluation are guarded by a re-entrant lock,
    so a telemetry thread may evaluate while a supervisor thread registers. The
    ``StrategyProcessInfo`` records themselves are shared mutable state - do not
    mutate their fields from outside the manager.
    """

    def __init__(
        self,
        max_fix_gateway_rate_sec: float = 1000.0,
        elevated_threshold_pct: float = 75.0,
        critical_threshold_pct: float = 85.0,
        resume_threshold_pct: Optional[float] = None,
        resume_clear_samples: int = 3,
        medium_priority_throttle_factor: float = 0.5,
    ):
        """
        Args:
            max_fix_gateway_rate_sec: Message-rate ceiling of the shared gateway
                session, msgs/sec. A venue- or broker-negotiated value, not a
                FIX-protocol constant (see ``references/standards.md``). Must be > 0.
            elevated_threshold_pct: Watch level. No preemption, but new
                background work should be held back.
            critical_threshold_pct: Preemption level. Must be >=
                ``elevated_threshold_pct``.
            resume_threshold_pct: Utilisation a sample must fall strictly below
                to count as "clear". Defaults to ``elevated_threshold_pct`` and
                must not exceed it.
            resume_clear_samples: Consecutive clear samples required before
                suppressed strategies are released. Must be >= 1. Multiply by the
                telemetry interval to get the real resume dwell time.
            medium_priority_throttle_factor: Fraction of baseline message rate
                left to MEDIUM_ARB strategies under CRITICAL. In [0, 1].

        These thresholds are operational defaults, not regulatory figures. No
        regulator publishes a numeric host-utilisation trigger; calibrate them
        from your own capacity and stress tests.
        """
        self.max_fix_gateway_rate_sec = _require_non_negative_rate(
            max_fix_gateway_rate_sec, "max_fix_gateway_rate_sec"
        )
        if self.max_fix_gateway_rate_sec == 0.0:
            raise ValueError("max_fix_gateway_rate_sec must be > 0 (it is a divisor)")

        self.elevated_threshold_pct = _require_utilization_pct(
            elevated_threshold_pct, "elevated_threshold_pct"
        )
        self.critical_threshold_pct = _require_utilization_pct(
            critical_threshold_pct, "critical_threshold_pct"
        )
        if self.critical_threshold_pct < self.elevated_threshold_pct:
            raise ValueError(
                "critical_threshold_pct must be >= elevated_threshold_pct "
                f"({critical_threshold_pct} < {elevated_threshold_pct})"
            )

        resolved_resume = (
            self.elevated_threshold_pct
            if resume_threshold_pct is None
            else _require_utilization_pct(resume_threshold_pct, "resume_threshold_pct")
        )
        if resolved_resume > self.elevated_threshold_pct:
            raise ValueError(
                "resume_threshold_pct must be <= elevated_threshold_pct "
                f"({resolved_resume} > {self.elevated_threshold_pct}); a resume level "
                "above the watch level would defeat the hysteresis."
            )
        self.resume_threshold_pct = resolved_resume

        if isinstance(resume_clear_samples, bool) or not isinstance(resume_clear_samples, int):
            raise TypeError(
                f"resume_clear_samples must be an int, got {resume_clear_samples!r}"
            )
        if resume_clear_samples < 1:
            raise ValueError(f"resume_clear_samples must be >= 1, got {resume_clear_samples}")
        self.resume_clear_samples = resume_clear_samples

        factor = _require_non_negative_rate(
            medium_priority_throttle_factor, "medium_priority_throttle_factor"
        )
        if factor > 1.0:
            raise ValueError(f"medium_priority_throttle_factor must be in [0, 1], got {factor}")
        self.medium_priority_throttle_factor = factor

        self.processes: Dict[str, StrategyProcessInfo] = {}
        self._consecutive_clear_samples = 0
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #
    def register_process(self, process: StrategyProcessInfo) -> None:
        """Register (or replace) a co-located strategy process.

        Raises on an unrecognised priority class. A silently ignored typo such as
        ``"HIGH-HFT"`` would leave that process outside every preemption branch:
        neither throttled when the host saturates nor protected as high priority.
        """
        if not isinstance(process, StrategyProcessInfo):
            raise TypeError(
                f"process must be a StrategyProcessInfo, got {type(process).__name__}"
            )
        if not isinstance(process.strategy_id, str) or not process.strategy_id:
            raise ValueError("strategy_id must be a non-empty string")
        if process.priority_level not in VALID_PRIORITY_LEVELS:
            raise ValueError(
                f"priority_level {process.priority_level!r} for {process.strategy_id!r} is not "
                f"one of {VALID_PRIORITY_LEVELS}"
            )
        if process.status not in VALID_STATUSES:
            raise ValueError(
                f"status {process.status!r} for {process.strategy_id!r} is not one of "
                f"{VALID_STATUSES}"
            )
        _require_utilization_pct(
            process.current_cpu_utilization_pct,
            f"{process.strategy_id}.current_cpu_utilization_pct",
        )
        _require_non_negative_rate(
            process.current_msg_rate_per_sec,
            f"{process.strategy_id}.current_msg_rate_per_sec",
        )
        if process.baseline_msg_rate_per_sec is not None:
            _require_non_negative_rate(
                process.baseline_msg_rate_per_sec,
                f"{process.strategy_id}.baseline_msg_rate_per_sec",
            )

        with self._lock:
            if process.strategy_id in self.processes:
                logger.warning(
                    "Re-registering strategy_id %s; the previous registration (priority %s) "
                    "and its suppression state are discarded.",
                    process.strategy_id,
                    self.processes[process.strategy_id].priority_level,
                )
            self.processes[process.strategy_id] = process

    # ------------------------------------------------------------------ #
    # Evaluation
    # ------------------------------------------------------------------ #
    def evaluate_contention_state(
        self,
        overall_cpu_pct: float,
        overall_ram_pct: float,
        current_fix_msg_rate_sec: float,
    ) -> ContentionMitigationReport:
        """Audit shared-resource utilisation and issue preemption/throttling directives.

        Side effect: updates ``status`` on every registered process. The returned
        directives are advisory - nothing here suspends an OS process, cancels a
        resting order, or reconfigures a gateway.

        Args:
            overall_cpu_pct: Host CPU utilisation, normalised to [0, 100].
            overall_ram_pct: Host memory utilisation, normalised to [0, 100].
            current_fix_msg_rate_sec: Aggregate outbound rate on the shared FIX
                session, msgs/sec. May exceed the negotiated limit, which yields
                a gateway utilisation above 100%.

        Raises:
            TypeError: a reading is not a real number.
            ValueError: a reading is non-finite, negative, or un-normalised.
        """
        overall_cpu_pct = _require_utilization_pct(overall_cpu_pct, "overall_cpu_pct")
        overall_ram_pct = _require_utilization_pct(overall_ram_pct, "overall_ram_pct")
        current_fix_msg_rate_sec = _require_non_negative_rate(
            current_fix_msg_rate_sec, "current_fix_msg_rate_sec"
        )

        fix_utilization_pct = (
            current_fix_msg_rate_sec / self.max_fix_gateway_rate_sec
        ) * 100.0

        readings = (
            (RESOURCE_CPU, overall_cpu_pct),
            (RESOURCE_RAM, overall_ram_pct),
            (RESOURCE_FIX_GATEWAY, fix_utilization_pct),
        )
        binding_resource, max_utilization = max(readings, key=lambda item: item[1])

        throttled: List[str] = []
        paused: List[str] = []
        resumed: List[str] = []
        directives: List[str] = []
        throttle_caps: Dict[str, float] = {}

        with self._lock:
            if max_utilization < self.resume_threshold_pct:
                self._consecutive_clear_samples += 1
            else:
                self._consecutive_clear_samples = 0

            if max_utilization >= self.critical_threshold_pct:
                state = STATE_CRITICAL
                logger.critical(
                    "CRITICAL RESOURCE CONTENTION: %s at %.1f%% >= %.1f%%. "
                    "Preempting low-priority tasks.",
                    binding_resource,
                    max_utilization,
                    self.critical_threshold_pct,
                )
                self._apply_preemption(throttled, paused, directives, throttle_caps)
            elif max_utilization >= self.elevated_threshold_pct:
                state = STATE_ELEVATED
                logger.warning(
                    "ELEVATED RESOURCE UTILIZATION: %s at %.1f%%. Holding existing "
                    "suppression; no new background work.",
                    binding_resource,
                    max_utilization,
                )
                directives.append(
                    "Elevated system usage. Restricting new background batch job spawns."
                )
                self._hold_suppression(throttled, paused, directives)
            else:
                state = STATE_NORMAL
                if self._consecutive_clear_samples >= self.resume_clear_samples:
                    self._release_suppression(resumed, directives)
                    directives.append(
                        "Resource usage normal. All processes running at 100% quota."
                    )
                else:
                    self._hold_suppression(throttled, paused, directives)
                    if throttled or paused:
                        directives.append(
                            "Utilisation normal but resume hysteresis not satisfied "
                            f"({self._consecutive_clear_samples}/{self.resume_clear_samples} "
                            f"clear samples below {self.resume_threshold_pct:.1f}%); "
                            "suppression held."
                        )
                    else:
                        directives.append(
                            "Resource usage normal. All processes running at 100% quota."
                        )

        return ContentionMitigationReport(
            contention_state=state,
            overall_cpu_pct=round(overall_cpu_pct, 2),
            overall_ram_pct=round(overall_ram_pct, 2),
            total_fix_msg_rate_sec=round(current_fix_msg_rate_sec, 2),
            throttled_strategies=throttled,
            paused_strategies=paused,
            mitigation_directives=directives,
            fix_gateway_utilization_pct=round(fix_utilization_pct, 2),
            max_utilization_pct=round(max_utilization, 2),
            binding_resource=binding_resource,
            resumed_strategies=resumed,
            throttle_caps_msg_per_sec=throttle_caps,
        )

    # ------------------------------------------------------------------ #
    # Internals - all called with self._lock held
    # ------------------------------------------------------------------ #
    def _effective_priority(self, proc: StrategyProcessInfo) -> str:
        """Fail closed on an unrecognised priority class.

        ``register_process`` validates the class, but ``StrategyProcessInfo`` is a
        mutable dataclass a caller can edit after registration. Treating an
        unknown class as the least-privileged one keeps a corrupted record from
        escaping preemption entirely.
        """
        if proc.priority_level in VALID_PRIORITY_LEVELS:
            return proc.priority_level
        logger.error(
            "Strategy %s has unrecognised priority_level %r; treating it as %s.",
            proc.strategy_id,
            proc.priority_level,
            VALID_PRIORITY_LEVELS[-1],
        )
        return VALID_PRIORITY_LEVELS[-1]

    def _apply_preemption(
        self,
        throttled: List[str],
        paused: List[str],
        directives: List[str],
        throttle_caps: Dict[str, float],
    ) -> None:
        for sid, proc in self.processes.items():
            priority = self._effective_priority(proc)
            if priority == PRIORITY_LOW_BATCH:
                proc.status = STATUS_PAUSED
                paused.append(sid)
                directives.append(
                    f"PAUSE process {sid} (LOW_BATCH) to relieve CPU/memory contention. "
                    "Cancel or hand off any working orders it owns before suspending it."
                )
            elif priority == PRIORITY_MEDIUM_ARB:
                proc.status = STATUS_THROTTLED
                throttled.append(sid)
                reference_rate = (
                    proc.baseline_msg_rate_per_sec
                    if proc.baseline_msg_rate_per_sec is not None
                    else proc.current_msg_rate_per_sec
                )
                cap = reference_rate * self.medium_priority_throttle_factor
                throttle_caps[sid] = round(cap, 4)
                basis = (
                    "baseline" if proc.baseline_msg_rate_per_sec is not None else "observed"
                )
                directives.append(
                    f"THROTTLE process {sid} (MEDIUM_ARB) to "
                    f"{self.medium_priority_throttle_factor:.0%} of its {basis} rate "
                    f"= {cap:.2f} msgs/sec."
                )
            else:  # PRIORITY_HIGH_HFT
                proc.status = STATUS_RUNNING
                directives.append(
                    f"PROTECT process {sid} (HIGH_HFT) on pinned CPU core "
                    f"{proc.pinned_cpu_core}; verify the core is also isolated, not "
                    "merely affinitised."
                )

    def _hold_suppression(
        self, throttled: List[str], paused: List[str], directives: List[str]
    ) -> None:
        """Leave existing PAUSED/THROTTLED states in place and re-report them."""
        for sid, proc in self.processes.items():
            if proc.status == STATUS_PAUSED:
                paused.append(sid)
                directives.append(f"HOLD process {sid} paused pending resume hysteresis.")
            elif proc.status == STATUS_THROTTLED:
                throttled.append(sid)
                directives.append(f"HOLD process {sid} throttled pending resume hysteresis.")

    def _release_suppression(self, resumed: List[str], directives: List[str]) -> None:
        for sid, proc in self.processes.items():
            if proc.status != STATUS_RUNNING:
                resumed.append(sid)
                directives.append(
                    f"RESUME process {sid} from {proc.status} after "
                    f"{self._consecutive_clear_samples} clear samples."
                )
                proc.status = STATUS_RUNNING
