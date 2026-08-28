"""
runbook-automation-for-common-incident-types: deterministic execution of
pre-approved remediation playbooks for live algorithmic-trading incidents.

Purpose
-------
Turn "what do we do when the feed drops / the broker API dies / the drawdown
limit trips" from an improvisation at 03:00 into an ordered, auditable sequence
of pre-approved actions. Google's SRE Book puts the value plainly: "thinking
through and recording the best practices ahead of time in a 'playbook' produces
roughly a 3x improvement in MTTR as compared to the strategy of 'winging it'"
(Beyer et al., *Site Reliability Engineering*, Introduction).

The engine does not decide *whether* an incident is real. It receives an alert
that the monitoring stack has already classified, looks up the playbook for that
incident class, and runs it.

This engine executes nothing on its own
---------------------------------------
Every ``RemediationAction`` is inert until a handler is bound to it with
``register_handler``. An action with no handler is reported as
``NO_HANDLER_REGISTERED`` and escalates to a human -- it is **never** reported
as ``SUCCESS``. The previous version of this module hard-coded
``step_status = "SUCCESS"`` under a ``# Simulate execution`` comment, so a
caller who wired it to a real kill switch received a ``RESOLVED`` report while
the position ran on untouched. A remediation report that claims an action which
never happened is worse than no report.

Why an unmapped incident type does not fall back to a playbook
--------------------------------------------------------------
The previous ``playbooks.get(incident_type, [CANCEL_OPEN_ORDERS])`` default
meant any incident class nobody had written a playbook for silently triggered a
mass cancel. Executing a market-affecting action on an unrecognised diagnosis is
the specific failure the SEC recorded in the Knight Capital order: "In one of its
attempts to address the problem, Knight uninstalled the new RLP code from the
seven servers where it had been deployed correctly. This action worsened the
problem" (SEC Admin. Proc. 34-70694, 16 Oct 2013). An unmapped incident type now
escalates with zero steps executed.

Why a failed step does not abandon the kill switch
--------------------------------------------------
``halt_on_failure`` defaults to ``False``. On a ``DRAWDOWN_BREACH`` the sequence
is cancel-then-kill; if the cancel fails, halting there leaves the algorithm
live and the limit breached. MiFID II RTS 6 (Commission Delegated Regulation
(EU) 2017/589) Art. 12(1) requires an investment firm to be able "to cancel
immediately, as an emergency measure, any or all of its unexecuted orders", and
Art. 14(3) requires that the trading system "can be shut down ... without
creating disorderly trading conditions". Both point the same way: the protective
step is attempted even when the step before it failed. Set
``halt_on_failure=True`` only where continuing would itself be unsafe.

Dry run is a mode, not an outcome
---------------------------------
A dry run returns ``IncidentStatus.DRY_RUN_COMPLETE``, never ``RESOLVED``. It
also still checks handler wiring, so a dry run is a genuine pre-flight test of
the runbook rather than a rehearsal of a happy path. Note that a dry run against
production is not the separated test environment RTS 6 Art. 7 requires for
pre-deployment testing; it is an operational readiness check on top of it.

Timeouts bound the wait, not the handler
----------------------------------------
``step_timeout_seconds`` bounds how long the engine waits for a handler, using a
daemon thread. Python cannot forcibly kill a thread, so a handler blocked in a
socket read keeps running after it is reported ``TIMED_OUT``. Handlers must set
their own transport-level timeouts; this is a backstop against a stalled
capital-protection sequence, not a substitute.

What this engine does NOT do
----------------------------
It does not detect incidents, page anyone (see
``on-call-rotation-and-escalation-for-trading-systems``), implement the kill
switch itself (see ``execution-algorithm-kill-switch-integration``), choose a
failover venue (see ``smart-order-router-failover-on-venue-outage``), or persist
anything. ``get_audit_history`` is in-process memory; DORA Art. 17(2) requires
financial entities to "record all ICT-related incidents", and an in-memory list
that dies with the process does not satisfy that. Persist every report.
"""
import copy
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "RunbookInputError",
    "RunbookConfigurationError",
    "IncidentType",
    "RemediationAction",
    "IncidentStatus",
    "StepStatus",
    "IncidentAlert",
    "PlaybookStep",
    "RemediationStep",
    "IncidentRunbookReport",
    "RunbookIncidentAutomationEngine",
    "RemediationHandler",
    "VALID_SEVERITIES",
    "DEFAULT_STEP_TIMEOUT_SECONDS",
    "DEFAULT_MAX_AUDIT_HISTORY",
]


class RunbookInputError(ValueError):
    """An incident alert could not be accepted as written.

    Raised at the ingestion boundary. Page a human; do not fall back to a
    playbook. An alert the engine cannot parse is an alert it cannot classify,
    and an unclassified incident must not select a remediation sequence.
    """


class RunbookConfigurationError(ValueError):
    """The engine was wired incorrectly (bad handler, malformed playbook)."""


class IncidentType(str, Enum):
    """Incident classes with a pre-approved playbook in this module.

    Adding a member without also registering a playbook for it is safe: the
    lookup escalates rather than falling back to a destructive default.
    """

    FEED_DISCONNECT = "FEED_DISCONNECT"
    LATENCY_SPIKE = "LATENCY_SPIKE"
    BROKER_API_OUTAGE = "BROKER_API_OUTAGE"
    DRAWDOWN_BREACH = "DRAWDOWN_BREACH"
    ORDER_THROTTLE = "ORDER_THROTTLE"


class RemediationAction(str, Enum):
    """Actions a playbook may invoke. Each is inert until a handler is bound."""

    RECONNECT_SOCKET = "RECONNECT_SOCKET"
    FAILOVER_VENUE = "FAILOVER_VENUE"
    CANCEL_OPEN_ORDERS = "CANCEL_OPEN_ORDERS"
    TRIGGER_KILL_SWITCH = "TRIGGER_KILL_SWITCH"
    THROTTLE_ORDER_RATE = "THROTTLE_ORDER_RATE"


class IncidentStatus(str, Enum):
    """Terminal outcome of one runbook execution.

    ``TRIGGERED`` and ``EXECUTING`` were removed in 2.0.0: ``execute_runbook``
    is synchronous and never emitted them, so branching on them was dead code
    that looked live.
    """

    RESOLVED = "RESOLVED"
    ESCALATED = "ESCALATED"
    DRY_RUN_COMPLETE = "DRY_RUN_COMPLETE"


class StepStatus(str, Enum):
    """Outcome of a single remediation step."""

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    SKIPPED_DRY_RUN = "SKIPPED_DRY_RUN"
    SKIPPED_AFTER_HALT = "SKIPPED_AFTER_HALT"
    SKIPPED_ALREADY_REMEDIATED = "SKIPPED_ALREADY_REMEDIATED"
    NO_HANDLER_REGISTERED = "NO_HANDLER_REGISTERED"


#: A handler receives the alert and performs the action. It signals failure by
#: raising, or by returning exactly ``False``. Any other return value --
#: including ``None`` -- is success, so a handler that forgets to return is not
#: mistaken for a failed kill switch.
RemediationHandler = Callable[["IncidentAlert"], Any]

#: Severity labels this engine records. Severity is audit metadata here; the
#: playbook is selected by ``incident_type``, never by severity.
VALID_SEVERITIES: Tuple[str, ...] = ("CRITICAL", "HIGH", "MEDIUM")

#: Engineering default, not a regulatory one. No regulator prescribes a
#: remediation-execution deadline; RTS 6 Art. 16 prescribes five seconds for
#: *alert generation*, which is a different clock. Chosen so one unresponsive
#: handler cannot stall a capital-protection sequence indefinitely. Tune it to
#: your handlers' own transport timeouts and record why.
DEFAULT_STEP_TIMEOUT_SECONDS = 30.0

#: In-memory audit ring size. Reports are dropped oldest-first past this bound,
#: which is only safe once a durable copy exists (DORA Art. 17(2)).
DEFAULT_MAX_AUDIT_HISTORY = 10_000

_STATUSES_COUNTING_AS_FAILURE = frozenset(
    {StepStatus.FAILED, StepStatus.TIMED_OUT, StepStatus.NO_HANDLER_REGISTERED}
)


class _StepTimeout(Exception):
    """Internal: the engine stopped waiting for a handler."""


def _require_finite(value: Any, label: str) -> float:
    """Coerce ``value`` to a finite float or raise ``RunbookInputError``.

    Alert payloads arrive as JSON, where a metric is as likely to be the string
    ``"-150000.0"`` as a number, and a monitoring system with a gap in its
    series can emit ``NaN``. A NaN threshold compares False against everything,
    so it silently disables any downstream comparison and prints as ``nan`` in
    the incident record.
    """
    try:
        coerced = float(value)
    except (TypeError, ValueError):
        raise RunbookInputError(f"{label} is not a number: {value!r}.") from None
    if not math.isfinite(coerced):
        raise RunbookInputError(f"{label} is not finite: {value!r}.")
    return coerced


def _normalise_timestamp(value: Any) -> str:
    """Return ``value`` as a UTC ISO-8601 string, or raise.

    Accepts a trailing ``Z`` (``datetime.fromisoformat`` rejects it before
    Python 3.11) and requires an explicit offset. An incident record stamped in
    an unstated local time cannot be ordered against a venue's own log during a
    post-mortem, which is the one job the timestamp has.
    """
    if isinstance(value, datetime):
        parsed = value
    else:
        if not isinstance(value, str) or not value.strip():
            raise RunbookInputError(f"timestamp_iso is not an ISO-8601 string: {value!r}.")
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            raise RunbookInputError(
                f"timestamp_iso is not a parseable ISO-8601 timestamp: {value!r}."
            ) from None
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise RunbookInputError(
            f"timestamp_iso must carry an explicit UTC offset, got naive {value!r}. "
            "A local-time incident record cannot be reconciled with venue logs."
        )
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class IncidentAlert:
    """A classified incident handed over by the monitoring stack.

    Validated on construction. ``incident_type`` accepts either an
    ``IncidentType`` or its string name; an unrecognised label raises rather
    than defaulting, because the engine must not pick a remediation sequence for
    an incident class it does not know.
    """

    incident_id: str
    incident_type: IncidentType
    severity: str
    source_service: str
    metric_value: float
    threshold_value: float
    timestamp_iso: str
    #: True when ``severity`` was not one of ``VALID_SEVERITIES`` and was raised
    #: to ``CRITICAL``. Guessing severity downward routes the worst incident
    #: class to the quietest channel; guessing upward costs one extra look.
    severity_was_coerced: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.incident_id, str) or not self.incident_id.strip():
            raise RunbookInputError("incident_id must be a non-empty string.")
        self.incident_id = self.incident_id.strip()

        if isinstance(self.incident_type, IncidentType):
            pass
        elif isinstance(self.incident_type, str):
            label = self.incident_type.strip().upper()
            try:
                self.incident_type = IncidentType(label)
            except ValueError:
                raise RunbookInputError(
                    f"Unrecognised incident_type {self.incident_type!r}. "
                    f"Known types: {[t.value for t in IncidentType]}. "
                    "Escalate to a human rather than selecting a playbook."
                ) from None
        else:
            raise RunbookInputError(
                "incident_type must be an IncidentType or its name, got "
                f"{type(self.incident_type).__name__}."
            )

        if not isinstance(self.source_service, str) or not self.source_service.strip():
            raise RunbookInputError("source_service must be a non-empty string.")
        self.source_service = self.source_service.strip()

        severity_label = self.severity.strip().upper() if isinstance(self.severity, str) else ""
        if severity_label in VALID_SEVERITIES:
            self.severity = severity_label
        else:
            logger.error(
                "Unrecognised severity %r on incident %s; recording as CRITICAL.",
                self.severity,
                self.incident_id,
            )
            self.severity = "CRITICAL"
            self.severity_was_coerced = True

        self.metric_value = _require_finite(self.metric_value, "metric_value")
        self.threshold_value = _require_finite(self.threshold_value, "threshold_value")
        self.timestamp_iso = _normalise_timestamp(self.timestamp_iso)


@dataclass(frozen=True)
class PlaybookStep:
    """One entry in a pre-approved remediation sequence."""

    action: RemediationAction
    #: If this step succeeds the incident is remediated and the remaining steps
    #: are skipped. Models "try the cheap fix first": a successful socket
    #: reconnect should not then move the session to a backup venue.
    terminal_on_success: bool = False
    #: If this step fails, abandon the rest of the playbook. Defaults to False
    #: so that a failed cancel does not cost you the kill switch behind it.
    halt_on_failure: bool = False


@dataclass(frozen=True)
class RemediationStep:
    """The record of one attempted step. Frozen: an audit entry is not a draft."""

    step_number: int
    action: RemediationAction
    status: StepStatus
    detail: str
    duration_ms: float = 0.0


@dataclass
class IncidentRunbookReport:
    """Outcome of one runbook execution, and the audit artifact for it."""

    incident_id: str
    incident_type: IncidentType
    status: IncidentStatus
    steps_executed: List[RemediationStep]
    total_time_taken_ms: float
    audit_notes: str
    is_dry_run: bool = False
    #: True whenever a step failed, timed out, had no handler, or no playbook
    #: existed -- exactly ``bool(escalation_reasons)``. Branch on this, not on
    #: the status string.
    requires_human_escalation: bool = False
    escalation_reasons: List[str] = field(default_factory=list)
    #: Carried over from the alert. A wiring defect in the alert source worth
    #: fixing, but not a remediation failure, so it does not escalate the run.
    severity_was_coerced: bool = False
    #: Times this ``incident_id`` was delivered again after the first execution.
    #: Alert transports redeliver; a non-zero count is information, not an error.
    duplicate_delivery_count: int = 0
    executed_at_utc_iso: str = ""


#: Pre-approved playbooks. DORA Art. 17(3)(c) requires a financial entity to
#: "assign roles and responsibilities that need to be activated for different
#: ICT-related incident types and scenarios"; this table is that assignment for
#: the automated portion of the response.
_DEFAULT_PLAYBOOKS: Dict[IncidentType, Tuple[PlaybookStep, ...]] = {
    # Reconnect first. Only move venues if the socket will not come back --
    # failing over after a successful reconnect is an unnecessary mid-session
    # venue change with its own queue-position and entitlement consequences.
    IncidentType.FEED_DISCONNECT: (
        PlaybookStep(RemediationAction.RECONNECT_SOCKET, terminal_on_success=True),
        PlaybookStep(RemediationAction.FAILOVER_VENUE),
    ),
    # Throttling is reversible and local; failover is neither. Try it first.
    IncidentType.LATENCY_SPIKE: (
        PlaybookStep(RemediationAction.THROTTLE_ORDER_RATE, terminal_on_success=True),
        PlaybookStep(RemediationAction.FAILOVER_VENUE),
    ),
    # The cancel is attempted through the broker that is already failing, so it
    # is expected to fail sometimes. That is precisely why it must not halt the
    # failover behind it. Exchange-side cancel-on-disconnect is the real
    # backstop for orders resting at a venue you can no longer reach.
    IncidentType.BROKER_API_OUTAGE: (
        PlaybookStep(RemediationAction.CANCEL_OPEN_ORDERS),
        PlaybookStep(RemediationAction.FAILOVER_VENUE),
    ),
    # RTS 6 Art. 12(1) cancel, then the Art. 14(2)(f) shutdown. The kill switch
    # is attempted even if the cancel fails.
    IncidentType.DRAWDOWN_BREACH: (
        PlaybookStep(RemediationAction.CANCEL_OPEN_ORDERS),
        PlaybookStep(RemediationAction.TRIGGER_KILL_SWITCH),
    ),
    IncidentType.ORDER_THROTTLE: (
        PlaybookStep(RemediationAction.THROTTLE_ORDER_RATE),
    ),
}


class RunbookIncidentAutomationEngine:
    """Executes pre-approved remediation playbooks for trading incidents.

    The engine is a sequencer with an audit trail. It performs no remediation
    itself: bind a callable to each ``RemediationAction`` with
    ``register_handler`` before relying on it in production, and verify the
    wiring with ``unhandled_actions()`` and a dry run.

    Thread safety: ``execute_runbook`` holds an internal lock for the whole
    execution, so two concurrent deliveries of the same alert cannot both run
    the playbook. Handlers therefore run one at a time per engine instance, and
    **a handler must not call back into the engine** -- with a step timeout set
    the handler runs on a worker thread, so touching the engine from inside one
    blocks on the lock its own execution is holding until the step times out.
    Handlers should be leaf calls into your risk, feed or routing systems.
    """

    def __init__(
        self,
        is_dry_run: bool = False,
        step_timeout_seconds: Optional[float] = DEFAULT_STEP_TIMEOUT_SECONDS,
        max_audit_history: Optional[int] = DEFAULT_MAX_AUDIT_HISTORY,
    ) -> None:
        """
        Args:
            is_dry_run: When True no handler is invoked; handler *wiring* is
                still checked, and the outcome is ``DRY_RUN_COMPLETE``.
            step_timeout_seconds: Seconds to wait for one handler before
                recording ``TIMED_OUT``. ``None`` calls handlers inline with no
                bound at all -- only appropriate when every handler already
                enforces its own deadline.
            max_audit_history: In-memory report cap; ``None`` for unbounded.
        """
        if step_timeout_seconds is not None:
            step_timeout_seconds = float(step_timeout_seconds)
            if not math.isfinite(step_timeout_seconds) or step_timeout_seconds <= 0:
                raise RunbookConfigurationError(
                    "step_timeout_seconds must be a positive finite number or None, got "
                    f"{step_timeout_seconds!r}."
                )
        if max_audit_history is not None and max_audit_history < 1:
            raise RunbookConfigurationError(
                f"max_audit_history must be >= 1 or None, got {max_audit_history!r}."
            )

        self.is_dry_run = bool(is_dry_run)
        self.step_timeout_seconds = step_timeout_seconds
        self.max_audit_history = max_audit_history

        self._handlers: Dict[RemediationAction, RemediationHandler] = {}
        self._playbooks: Dict[IncidentType, Tuple[PlaybookStep, ...]] = dict(_DEFAULT_PLAYBOOKS)
        self._audit_history: List[IncidentRunbookReport] = []
        self._reports_by_id: Dict[str, IncidentRunbookReport] = {}
        self._lock = threading.RLock()

        if self.is_dry_run:
            logger.warning(
                "Runbook engine constructed in DRY RUN mode: no remediation will execute."
            )

    # ---------------------------------------------------------------- wiring

    def register_handler(self, action: RemediationAction, handler: RemediationHandler) -> None:
        """Bind the callable that actually performs ``action``.

        The handler signals failure by raising, or by returning exactly
        ``False``. Any other return value is treated as success.
        """
        if not isinstance(action, RemediationAction):
            raise RunbookConfigurationError(
                f"action must be a RemediationAction, got {type(action).__name__}."
            )
        if not callable(handler):
            raise RunbookConfigurationError(
                f"handler for {action.value} is not callable: {handler!r}."
            )
        with self._lock:
            if action in self._handlers:
                logger.warning("Replacing existing handler for %s.", action.value)
            self._handlers[action] = handler

    def register_playbook(
        self, incident_type: IncidentType, steps: Sequence[PlaybookStep]
    ) -> None:
        """Replace the playbook for ``incident_type``.

        An empty sequence is rejected: a playbook that does nothing would report
        ``RESOLVED`` while the incident runs on. Remove the incident type from
        your alert routing instead.
        """
        if not isinstance(incident_type, IncidentType):
            raise RunbookConfigurationError(
                f"incident_type must be an IncidentType, got {type(incident_type).__name__}."
            )
        if not steps:
            raise RunbookConfigurationError(
                f"Playbook for {incident_type.value} is empty. An empty playbook would "
                "report success without remediating anything."
            )
        for step in steps:
            if not isinstance(step, PlaybookStep):
                raise RunbookConfigurationError(
                    f"Playbook entries must be PlaybookStep, got {type(step).__name__}."
                )
        with self._lock:
            self._playbooks[incident_type] = tuple(steps)

    def get_playbook(self, incident_type: IncidentType) -> Tuple[PlaybookStep, ...]:
        """Return the registered playbook, or an empty tuple if none exists."""
        with self._lock:
            return self._playbooks.get(incident_type, ())

    def unhandled_actions(self) -> Tuple[RemediationAction, ...]:
        """Actions reachable from a registered playbook with no handler bound.

        Call this at startup. Discovering that ``TRIGGER_KILL_SWITCH`` is
        unwired belongs in a deployment gate, not in the report of the incident
        that needed it.
        """
        with self._lock:
            reachable = {step.action for steps in self._playbooks.values() for step in steps}
            return tuple(sorted(reachable - set(self._handlers), key=lambda a: a.value))

    # ------------------------------------------------------------- execution

    def execute_runbook(
        self, alert: IncidentAlert, force_reexecute: bool = False
    ) -> IncidentRunbookReport:
        """Run the pre-approved playbook for ``alert`` and return its record.

        Idempotent on ``incident_id``: a redelivered alert returns the stored
        report with ``duplicate_delivery_count`` incremented and executes
        nothing. Alert transports redeliver by design, and re-running a
        ``DRAWDOWN_BREACH`` playbook means a second mass cancel and a second
        kill-switch trip.

        Args:
            alert: The classified incident.
            force_reexecute: Re-run despite a prior execution. For an
                operator-authorised retry after a partial failure only; record
                who authorised it.
        """
        if not isinstance(alert, IncidentAlert):
            raise RunbookInputError(
                f"alert must be an IncidentAlert, got {type(alert).__name__}."
            )

        # Captured once: a handler that mutates the alert it was handed must not
        # be able to file the report under a different key than the one the
        # duplicate check consulted.
        incident_id = alert.incident_id

        with self._lock:
            prior = self._reports_by_id.get(incident_id)
            if prior is not None and not force_reexecute:
                prior.duplicate_delivery_count += 1
                logger.warning(
                    "Duplicate delivery %d of incident %s; playbook not re-executed "
                    "(pass force_reexecute=True for an authorised retry).",
                    prior.duplicate_delivery_count,
                    incident_id,
                )
                return copy.deepcopy(prior)

            if prior is not None:
                logger.warning(
                    "Forced re-execution of incident %s; the prior report remains in the "
                    "audit history and is superseded.",
                    incident_id,
                )

            report = self._run_playbook(alert)
            report.incident_id = incident_id

            self._reports_by_id[incident_id] = report
            self._audit_history.append(report)
            self._trim_history_locked()
            return copy.deepcopy(report)

    def _run_playbook(self, alert: IncidentAlert) -> IncidentRunbookReport:
        """Execute every step of the playbook. Assumes ``self._lock`` is held."""
        t0 = time.perf_counter_ns()
        playbook = self._playbooks.get(alert.incident_type, ())
        executed_steps: List[RemediationStep] = []
        escalation_reasons: List[str] = []

        if not playbook:
            # No fallback. Selecting a destructive default for an unmapped
            # incident class is how a remediation makes an incident worse.
            reason = (
                f"No pre-approved playbook registered for {alert.incident_type.value}; "
                "no remediation attempted."
            )
            escalation_reasons.append(reason)
            logger.error("Incident %s: %s", alert.incident_id, reason)
            return self._finalise(alert, executed_steps, escalation_reasons, t0)

        halted = False
        remediated = False
        for idx, step in enumerate(playbook, 1):
            if halted:
                executed_steps.append(RemediationStep(
                    step_number=idx,
                    action=step.action,
                    status=StepStatus.SKIPPED_AFTER_HALT,
                    detail=f"Skipped: playbook halted at step {idx - 1}.",
                ))
                continue
            if remediated:
                executed_steps.append(RemediationStep(
                    step_number=idx,
                    action=step.action,
                    status=StepStatus.SKIPPED_ALREADY_REMEDIATED,
                    detail=f"Skipped: step {idx - 1} remediated the incident.",
                ))
                continue

            record = self._execute_step(alert, step, idx)
            executed_steps.append(record)

            if record.status in _STATUSES_COUNTING_AS_FAILURE:
                escalation_reasons.append(
                    f"Step {idx} {step.action.value}: {record.status.value} -- {record.detail}"
                )
                if step.halt_on_failure:
                    halted = True
                    escalation_reasons.append(
                        f"Playbook halted after step {idx} ({step.action.value}); "
                        "remaining steps not attempted."
                    )
            elif record.status is StepStatus.SUCCESS and step.terminal_on_success:
                remediated = True

        return self._finalise(alert, executed_steps, escalation_reasons, t0)

    def _execute_step(
        self, alert: IncidentAlert, step: PlaybookStep, step_number: int
    ) -> RemediationStep:
        """Run one step, converting every outcome into an audit record."""
        action = step.action
        handler = self._handlers.get(action)

        if handler is None:
            # True in dry run too: a dry run whose job is to prove the runbook
            # works must not report an unwired action as "would execute".
            detail = (
                f"No handler registered for {action.value}. Nothing was executed. "
                "Bind one with register_handler() before relying on this playbook."
            )
            logger.error("Incident %s step %d: %s", alert.incident_id, step_number, detail)
            return RemediationStep(step_number, action, StepStatus.NO_HANDLER_REGISTERED, detail)

        if self.is_dry_run:
            return RemediationStep(
                step_number,
                action,
                StepStatus.SKIPPED_DRY_RUN,
                f"DRY RUN: would execute {action.value} for {alert.incident_type.value}; "
                "handler is registered.",
            )

        started = time.perf_counter_ns()
        try:
            result = self._call_handler(handler, alert, action)
        except _StepTimeout:
            elapsed = round((time.perf_counter_ns() - started) / 1e6, 3)
            detail = (
                f"{action.value} exceeded step_timeout_seconds="
                f"{self.step_timeout_seconds}; the engine stopped waiting. The handler "
                "thread may still be running -- treat the action's effect as unknown."
            )
            logger.error("Incident %s step %d: %s", alert.incident_id, step_number, detail)
            return RemediationStep(step_number, action, StepStatus.TIMED_OUT, detail, elapsed)
        except Exception as exc:  # handler failure must not abort the runbook
            elapsed = round((time.perf_counter_ns() - started) / 1e6, 3)
            detail = f"{action.value} raised {type(exc).__name__}: {exc}"
            logger.exception("Incident %s step %d failed.", alert.incident_id, step_number)
            return RemediationStep(step_number, action, StepStatus.FAILED, detail, elapsed)

        elapsed = round((time.perf_counter_ns() - started) / 1e6, 3)
        if result is False:
            detail = f"{action.value} handler reported failure (returned False)."
            logger.error("Incident %s step %d: %s", alert.incident_id, step_number, detail)
            return RemediationStep(step_number, action, StepStatus.FAILED, detail, elapsed)

        return RemediationStep(
            step_number,
            action,
            StepStatus.SUCCESS,
            f"Executed {action.value} in {elapsed}ms.",
            elapsed,
        )

    def _call_handler(
        self, handler: RemediationHandler, alert: IncidentAlert, action: RemediationAction
    ) -> Any:
        """Invoke ``handler``, bounded by ``step_timeout_seconds`` if set.

        The bound is on the *wait*: Python offers no way to cancel a running
        thread, so a handler blocked in a socket read continues after
        ``_StepTimeout`` is raised. The thread is a daemon so it cannot hold up
        interpreter shutdown.
        """
        if self.step_timeout_seconds is None:
            return handler(alert)

        outcome: List[Any] = []
        failure: List[Exception] = []

        def _runner() -> None:
            try:
                outcome.append(handler(alert))
            except Exception as exc:  # re-raised on the calling thread
                failure.append(exc)

        worker = threading.Thread(
            target=_runner, name=f"runbook-{action.value.lower()}", daemon=True
        )
        worker.start()
        worker.join(self.step_timeout_seconds)
        if worker.is_alive():
            raise _StepTimeout(action.value)
        if failure:
            raise failure[0]
        return outcome[0] if outcome else None

    def _finalise(
        self,
        alert: IncidentAlert,
        executed_steps: List[RemediationStep],
        escalation_reasons: List[str],
        t0: int,
    ) -> IncidentRunbookReport:
        """Assemble the report, choosing the status the evidence supports."""
        dt_ms = round((time.perf_counter_ns() - t0) / 1e6, 3)
        requires_escalation = bool(escalation_reasons)

        if self.is_dry_run:
            status = IncidentStatus.DRY_RUN_COMPLETE
        elif requires_escalation:
            status = IncidentStatus.ESCALATED
        else:
            status = IncidentStatus.RESOLVED

        reasons = list(escalation_reasons)
        executed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        notes = (
            f"RUNBOOK INCIDENT [{status.value}] ({alert.incident_id}): "
            f"type={alert.incident_type.value}, severity={alert.severity}, "
            f"source={alert.source_service}, alert_ts={alert.timestamp_iso}, "
            f"metric={alert.metric_value} vs threshold={alert.threshold_value}, "
            f"steps={len(executed_steps)}, dry_run={self.is_dry_run}, "
            f"escalation_required={requires_escalation}, elapsed={dt_ms}ms."
        )

        if requires_escalation:
            logger.error("%s Reasons: %s", notes, "; ".join(reasons))
        else:
            logger.info(notes)

        return IncidentRunbookReport(
            incident_id=alert.incident_id,
            incident_type=alert.incident_type,
            status=status,
            steps_executed=executed_steps,
            total_time_taken_ms=dt_ms,
            audit_notes=notes,
            is_dry_run=self.is_dry_run,
            requires_human_escalation=requires_escalation,
            escalation_reasons=reasons,
            severity_was_coerced=alert.severity_was_coerced,
            executed_at_utc_iso=executed_at,
        )

    # ----------------------------------------------------------------- audit

    def _trim_history_locked(self) -> None:
        """Drop the oldest reports past ``max_audit_history``."""
        if self.max_audit_history is None:
            return
        overflow = len(self._audit_history) - self.max_audit_history
        if overflow <= 0:
            return
        dropped = self._audit_history[:overflow]
        del self._audit_history[:overflow]
        for report in dropped:
            if self._reports_by_id.get(report.incident_id) is report:
                del self._reports_by_id[report.incident_id]
        logger.warning(
            "Audit history exceeded max_audit_history=%d; dropped %d oldest report(s). "
            "This is only safe if they were persisted.",
            self.max_audit_history,
            overflow,
        )

    def get_audit_history(self) -> List[IncidentRunbookReport]:
        """Return deep copies of every retained report, oldest first.

        Copies, not references: an incident record a caller can edit after the
        fact is not an audit trail. This is in-process memory and is lost on
        restart -- persist it (DORA Art. 17(2)).
        """
        with self._lock:
            return copy.deepcopy(self._audit_history)

    def get_report(self, incident_id: str) -> Optional[IncidentRunbookReport]:
        """Return a copy of the most recent report for ``incident_id``, if any."""
        key = incident_id.strip() if isinstance(incident_id, str) else incident_id
        with self._lock:
            report = self._reports_by_id.get(key)
            return copy.deepcopy(report) if report is not None else None
