"""
risk-limit-breach-escalation-matrix: deterministic mapping from a risk-limit
breach to a graduated response (WARN -> REDUCE -> HALT -> FLATTEN), a
notification routing set, and an acknowledgement deadline.

Purpose
-------
A single binary breach/no-breach control has only two settings: ignore, or
liquidate. This engine converts a breach into a *tier* -- how far past the limit
the metric is, and how long it has stayed there -- and returns the response that
tier warrants, together with the channels that response must be announced on and
an audit record of why. Under MiFID II RTS 6 Art. 17 a triggered post-trade
control obliges the firm to undertake "appropriate action, which may include
adjusting or shutting down the relevant trading algorithm or trading system or
an orderly withdrawal from the market" -- a ladder, not a switch.

What this engine is NOT
-----------------------
It decides; it does not act and it does not send. There is no order gateway, no
position unwinder, no kill-switch wiring, no PagerDuty/Slack/e-mail client.
``action`` is an instruction to the caller's enforcement layer and
``notification_channels`` an instruction to the caller's notifier. Neither field
is evidence that anything was cancelled, flattened or delivered. It also does
not track acknowledgements: ``ack_deadline_seconds`` is the SLA the notifier
must enforce, not a timer this engine runs.

Breach ratio convention
-----------------------
``ratio`` is the breach magnitude expressed as a multiple of the limit, and the
formula depends on which side of the limit is the bad side:

* ``LimitDirection.UPPER`` (default) -- ceilings: drawdown, gross exposure,
  leverage, VaR, position count. ``ratio = current_value / limit_value``.
  ``current_value`` must be a non-negative **magnitude**. A drawdown handed in
  as ``-25000`` against a limit of ``10000`` previously produced a ratio of
  ``-2.5``, matched no tier and returned ``NONE`` -- a 2.5x drawdown answered
  with silence. That input is now rejected rather than interpreted, because the
  engine cannot distinguish a sign convention from a two-sided exposure by
  inspection.
* ``LimitDirection.LOWER`` -- floors: available margin, free cash, liquidity
  buffer, collateral coverage. ``ratio = 1 + (limit_value - current_value) /
  limit_value``, floored at 0. Sitting exactly on the floor is 1.0 (the first
  tier) and a floor metric at zero is 2.0, so an exhausted buffer lands on the
  same top tier as a 2x ceiling breach. That mapping is a **house calibration
  chosen so one ladder can serve both directions**, not a standard.

Thresholds are compared against the exact ratio. The previous implementation
rounded to 4 decimals *before* comparing, so a leverage of 1.99996x rounded to
2.0000 and triggered a CRITICAL/FLATTEN liquidation at a threshold the metric
had not actually reached.

Duration escalation
-------------------
A breach at or beyond ``sustained_breach_seconds`` (default 300 s) is promoted
**one full rung up the configured ladder** -- severity, action, channels and
acknowledgement deadline together. The previous implementation promoted the
action alone through a hard-coded ``WARN -> REDUCE -> HALT`` chain, so a
sustained AMBER breach became a RED/HALT whose alerts still went only to Slack
and e-mail, a sustained RED breach never escalated at all, and a ladder built
from any other actions (THROTTLE, GLOBAL_KILL_SWITCH) escalated nothing,
silently. Promotion is one rung per evaluation and stops at the top rung.

The 300 s default and the 1.0/1.2/1.5/2.0 tier multipliers are **house
defaults, not regulatory or industry thresholds**. No regulator prescribes
them. See ``references/standards.md`` for the obligations that are mandatory.

Latching
--------
Escalation ratchets. Once a (``strategy_id``, ``metric_name``) incident has
reached an action, a later evaluation of the same incident cannot return a
weaker one, because a metric oscillating around a threshold would otherwise
cancel an in-flight FLATTEN on the next tick. De-escalation is a deliberate,
logged act: ``reset_incident()``. Set ``latch_escalations=False`` for a purely
stateless evaluator.

Replay safety
-------------
FLATTEN is destructive and alert pipelines retry. Re-submitting an event whose
``event_id`` **and** payload are unchanged returns the original decision marked
``is_replay=True`` and adds no second audit row. The same ``event_id`` with a
changed payload is treated as a re-evaluation of an ongoing breach -- the normal
way a monitor reports a growing ``duration_seconds`` -- and is processed.
"""
import logging
import math
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "EscalationMatrixError",
    "InvalidBreachError",
    "InvalidPolicyError",
    "ResponseAction",
    "SeverityLevel",
    "NotificationChannel",
    "LimitDirection",
    "ACTION_ORDER",
    "SEVERITY_ORDER",
    "EscalationResult",
    "BreachEvent",
    "EscalationPolicy",
    "EscalationDecision",
    "DEFAULT_POLICIES",
    "DEFAULT_SUSTAINED_BREACH_SECONDS",
    "RiskEscalationMatrix",
]


class EscalationMatrixError(Exception):
    """Base exception for risk escalation matrix errors."""


class InvalidBreachError(EscalationMatrixError):
    """Raised when breach inputs are invalid, ambiguous, or non-finite."""


class InvalidPolicyError(EscalationMatrixError):
    """Raised when an escalation ladder is not a coherent ladder."""


class ResponseAction(str, Enum):
    NONE = "NONE"
    WARN = "WARN"
    THROTTLE = "THROTTLE"
    REDUCE = "REDUCE"
    HALT = "HALT"
    FLATTEN = "FLATTEN"
    GLOBAL_KILL_SWITCH = "GLOBAL_KILL_SWITCH"


class SeverityLevel(str, Enum):
    INFO = "INFO"
    AMBER = "AMBER"
    RED = "RED"
    CRITICAL = "CRITICAL"


class NotificationChannel(str, Enum):
    DASHBOARD = "DASHBOARD"
    SLACK = "SLACK"
    EMAIL = "EMAIL"
    PAGERDUTY = "PAGERDUTY"
    COMPLIANCE_TICKET = "COMPLIANCE_TICKET"


class LimitDirection(str, Enum):
    """Which side of the limit constitutes a breach."""
    UPPER = "UPPER"   # ceiling: breach when the metric rises to/above the limit
    LOWER = "LOWER"   # floor:   breach when the metric falls to/below the limit


#: Severity of each response action, ascending. Used to enforce that a ladder is
#: monotone, and that a latched incident is never implicitly de-escalated.
ACTION_ORDER: Dict[ResponseAction, int] = {
    ResponseAction.NONE: 0,
    ResponseAction.WARN: 1,
    ResponseAction.THROTTLE: 2,
    ResponseAction.REDUCE: 3,
    ResponseAction.HALT: 4,
    ResponseAction.FLATTEN: 5,
    ResponseAction.GLOBAL_KILL_SWITCH: 6,
}

#: Severity labels, ascending.
SEVERITY_ORDER: Dict[SeverityLevel, int] = {
    SeverityLevel.INFO: 0,
    SeverityLevel.AMBER: 1,
    SeverityLevel.RED: 2,
    SeverityLevel.CRITICAL: 3,
}

#: House default for "sustained". Not a regulatory or industry threshold.
DEFAULT_SUSTAINED_BREACH_SECONDS = 300.0

#: Replay-protection cache size. Beyond this many distinct event ids the oldest
#: fingerprints are evicted, and a re-submission of an evicted id is processed
#: as a fresh event rather than recognised as a replay.
_DEFAULT_REPLAY_CACHE_SIZE = 10_000


def _coerce_float(value: object, label: str) -> float:
    """
    Coerce ``value`` to a finite float or raise ``InvalidBreachError``.

    Breach payloads arrive as JSON, where a metric is as likely to be the string
    ``"25000.0"`` as a number, and where ``True`` would otherwise coerce to
    ``1.0`` and be evaluated as a real risk metric. Both are rejected here, at
    the boundary, rather than surfacing as a ``TypeError`` from an unrelated
    division several frames later.
    """
    if isinstance(value, bool):
        raise InvalidBreachError(f"{label} must be a number, got bool {value!r}.")
    try:
        coerced = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise InvalidBreachError(f"{label} is not a number: {value!r}.") from None
    if not math.isfinite(coerced):
        raise InvalidBreachError(
            f"{label} must be finite, got {value!r}. A NaN risk metric compares "
            f"False against every threshold and would report 'no breach'."
        )
    return coerced


def _require_text(value: object, label: str) -> str:
    """Require a non-blank string identifier; an audit row without one is useless."""
    if not isinstance(value, str) or not value.strip():
        raise InvalidBreachError(f"{label} must be a non-empty string, got {value!r}.")
    return value.strip()


def _coerce_direction(value: object) -> LimitDirection:
    """
    Coerce ``value`` to a :class:`LimitDirection` or raise ``InvalidBreachError``.

    ``LimitDirection("SIDEWAYS")`` raises a bare ``ValueError``, which is not an
    ``EscalationMatrixError`` and so escapes the ``except EscalationMatrixError``
    handler every caller is told to wrap this engine in -- a typo'd direction in
    a JSON payload would take the monitoring loop down with it.
    """
    if isinstance(value, LimitDirection):
        return value
    try:
        return LimitDirection(value)  # type: ignore[arg-type]
    except ValueError:
        raise InvalidBreachError(
            f"direction must be one of "
            f"{[d.value for d in LimitDirection]}, got {value!r}."
        ) from None


def _normalise_timestamp(value: object) -> str:
    """
    Validate an ISO-8601 breach timestamp and normalise it to UTC ``...Z`` form.

    An offset is mandatory. A naive timestamp in an escalation audit trail cannot
    be reconciled against exchange or broker records without knowing the writer's
    local zone, and the engine cannot infer it.
    """
    raw = _require_text(value, "timestamp_iso")
    candidate = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        raise InvalidBreachError(
            f"timestamp_iso is not an ISO-8601 timestamp: {value!r}."
        ) from None
    if parsed.utcoffset() is None:
        raise InvalidBreachError(
            f"timestamp_iso {value!r} carries no UTC offset. Supply one "
            f"(e.g. '2026-08-05T10:00:00Z'); a naive timestamp is not auditable."
        )
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class EscalationResult:
    """Result container for the legacy :meth:`RiskEscalationMatrix.evaluate` API."""
    action: ResponseAction
    level: float


@dataclass
class BreachEvent:
    """
    One observation of a risk metric against its limit.

    This is a plain data carrier and performs **no validation of its own** --
    every field is validated at the engine boundary in
    :meth:`RiskEscalationMatrix.process_breach_event`, so a malformed payload
    fails where it is evaluated rather than where it is constructed.

    ``duration_seconds`` is supplied by the caller: the engine holds no clock and
    cannot measure how long a breach has persisted. A caller that always passes
    0.0 gets no duration escalation, however long the breach actually runs.
    """
    event_id: str
    metric_name: str                     # e.g. 'DAILY_DRAWDOWN', 'MAX_LEVERAGE', 'POSITION_CAP'
    strategy_id: str
    current_value: float                 # UPPER: a non-negative magnitude
    limit_value: float                   # must be > 0
    timestamp_iso: str                   # ISO-8601, UTC offset required
    duration_seconds: float = 0.0
    direction: LimitDirection = LimitDirection.UPPER


@dataclass(frozen=True)
class EscalationPolicy:
    """
    One rung of the escalation ladder.

    Frozen, with ``channels`` normalised to a tuple, so a shared ladder (the
    module-level :data:`DEFAULT_POLICIES` in particular) cannot be mutated
    through one engine instance and silently change another's responses.
    """
    ratio_threshold: float               # e.g. 1.0 (100%), 1.2 (120%), 1.5 (150%), 2.0 (200%)
    severity: SeverityLevel
    action: ResponseAction
    channels: Sequence[NotificationChannel]
    ack_timeout_seconds: int             # SLA for risk manager acknowledgement

    def __post_init__(self) -> None:
        object.__setattr__(self, "channels", tuple(self.channels))


DEFAULT_POLICIES: Tuple[EscalationPolicy, ...] = (
    EscalationPolicy(1.0, SeverityLevel.INFO, ResponseAction.WARN,
                     (NotificationChannel.DASHBOARD, NotificationChannel.SLACK), 900),
    EscalationPolicy(1.2, SeverityLevel.AMBER, ResponseAction.REDUCE,
                     (NotificationChannel.SLACK, NotificationChannel.EMAIL), 300),
    EscalationPolicy(1.5, SeverityLevel.RED, ResponseAction.HALT,
                     (NotificationChannel.SLACK, NotificationChannel.PAGERDUTY), 120),
    EscalationPolicy(2.0, SeverityLevel.CRITICAL, ResponseAction.FLATTEN,
                     (NotificationChannel.PAGERDUTY, NotificationChannel.COMPLIANCE_TICKET), 60),
)


@dataclass(frozen=True)
class EscalationDecision:
    """
    The immutable audit record of one escalation decision.

    Frozen, so a row handed out by :meth:`RiskEscalationMatrix.get_audit_trail`
    cannot be edited in place -- the previous version handed out live objects, so
    "immutable audit log" was a claim the type did not support. The observed
    inputs are carried on the record itself: an audit row that records the
    verdict but not the numbers behind it cannot be re-derived later.
    """
    event_id: str
    metric_name: str
    strategy_id: str
    ratio: float
    severity: SeverityLevel
    action: ResponseAction
    notification_channels: Tuple[NotificationChannel, ...]
    ack_deadline_seconds: int
    is_sustained_breach: bool
    audit_notes: str
    timestamp_iso: str = ""
    direction: LimitDirection = LimitDirection.UPPER
    current_value: float = 0.0
    limit_value: float = 0.0
    duration_seconds: float = 0.0
    matched_threshold: Optional[float] = None
    is_duration_escalated: bool = False
    is_latched: bool = False
    is_replay: bool = False

    def payload_fingerprint(self) -> Tuple[object, ...]:
        """The observed inputs, for replay detection."""
        return (self.metric_name, self.strategy_id, self.current_value,
                self.limit_value, self.duration_seconds, self.direction.value,
                self.timestamp_iso)


class RiskEscalationMatrix:
    """
    Graduated risk-limit breach escalation: ratio -> tier -> (severity, action,
    channels, acknowledgement deadline), with duration promotion, latching,
    replay protection and a complete audit trail.

    The engine decides only. Wiring ``action`` to an enforcement layer and
    ``notification_channels`` to a notifier is the caller's responsibility, and
    under SEC Rule 15c3-5(d) that enforcement layer must remain under the direct
    and exclusive control of the broker-dealer.
    """

    def __init__(
        self,
        warn_lvl: float = 1.0,
        reduce_lvl: float = 1.2,
        halt_lvl: float = 1.5,
        flatten_lvl: float = 2.0,
        policies: Optional[Sequence[EscalationPolicy]] = None,
        sustained_breach_seconds: float = DEFAULT_SUSTAINED_BREACH_SECONDS,
        latch_escalations: bool = True,
        replay_cache_size: int = _DEFAULT_REPLAY_CACHE_SIZE,
    ) -> None:
        for label, value in (("warn_lvl", warn_lvl), ("reduce_lvl", reduce_lvl),
                             ("halt_lvl", halt_lvl), ("flatten_lvl", flatten_lvl)):
            if _coerce_float(value, label) <= 0:
                raise InvalidPolicyError(f"{label} must be > 0, got {value}.")
        if not warn_lvl < reduce_lvl < halt_lvl < flatten_lvl:
            # Equal levels collapsed into one dict key and silently deleted a
            # tier; an inverted ladder answered a 2.1x breach with WARN.
            raise InvalidPolicyError(
                f"Legacy levels must be strictly ascending, got warn={warn_lvl}, "
                f"reduce={reduce_lvl}, halt={halt_lvl}, flatten={flatten_lvl}."
            )

        #: Legacy threshold -> action map, kept for :meth:`evaluate`.
        self.levels: Dict[float, ResponseAction] = {
            flatten_lvl: ResponseAction.FLATTEN,
            halt_lvl: ResponseAction.HALT,
            reduce_lvl: ResponseAction.REDUCE,
            warn_lvl: ResponseAction.WARN,
        }
        self._legacy_desc: Tuple[Tuple[float, ResponseAction], ...] = tuple(
            (level, self.levels[level]) for level in sorted(self.levels, reverse=True)
        )

        #: The escalation ladder in **ascending** threshold order. Ascending is
        #: what "promote one rung" means; tier matching walks it in reverse.
        self.policies: Tuple[EscalationPolicy, ...] = self._validate_ladder(
            DEFAULT_POLICIES if policies is None else policies
        )

        self.sustained_breach_seconds = _coerce_float(
            sustained_breach_seconds, "sustained_breach_seconds")
        if self.sustained_breach_seconds <= 0:
            raise InvalidPolicyError(
                f"sustained_breach_seconds must be > 0, got {sustained_breach_seconds}.")
        self.latch_escalations = bool(latch_escalations)
        if int(replay_cache_size) < 0:
            raise InvalidPolicyError("replay_cache_size must be >= 0.")
        self._replay_cache_size = int(replay_cache_size)

        self._audit_log: List[EscalationDecision] = []
        self._active_incidents: Dict[Tuple[str, str], EscalationDecision] = {}
        self._replay_cache: Dict[str, EscalationDecision] = {}

    # ------------------------------------------------------------------ setup

    @staticmethod
    def _validate_ladder(
        policies: Sequence[EscalationPolicy],
    ) -> Tuple[EscalationPolicy, ...]:
        """
        Sort a ladder ascending by threshold and reject one that is not a ladder.

        An empty ladder used to be swallowed by ``policies or DEFAULT_POLICIES``
        and silently replaced with the defaults, so an operator who configured
        away every tier got the stock ones back without being told.
        """
        if not policies:
            raise InvalidPolicyError(
                "policies must contain at least one rung; pass None for the defaults.")
        for pol in policies:
            if not isinstance(pol, EscalationPolicy):
                raise InvalidPolicyError(f"Not an EscalationPolicy: {pol!r}.")
            # Checked before the ACTION_ORDER/SEVERITY_ORDER lookups below, which
            # would otherwise raise a bare KeyError on a plain string.
            if not isinstance(pol.severity, SeverityLevel):
                raise InvalidPolicyError(
                    f"severity must be a SeverityLevel, got {pol.severity!r}.")
            if not isinstance(pol.action, ResponseAction):
                raise InvalidPolicyError(
                    f"action must be a ResponseAction, got {pol.action!r}.")
            for channel in pol.channels:
                if not isinstance(channel, NotificationChannel):
                    raise InvalidPolicyError(
                        f"channel must be a NotificationChannel, got {channel!r}.")
            threshold = _coerce_float(pol.ratio_threshold, "ratio_threshold")
            if threshold <= 0:
                raise InvalidPolicyError(f"ratio_threshold must be > 0, got {threshold}.")
            if not pol.channels:
                raise InvalidPolicyError(
                    f"Tier {threshold} routes to no channel; an unrouted breach "
                    f"response is an unannounced one.")
            if pol.ack_timeout_seconds <= 0:
                raise InvalidPolicyError(
                    f"Tier {threshold} has ack_timeout_seconds="
                    f"{pol.ack_timeout_seconds}; must be > 0.")

        ordered = tuple(sorted(policies, key=lambda p: p.ratio_threshold))
        for lower, upper in zip(ordered, ordered[1:]):
            if lower.ratio_threshold == upper.ratio_threshold:
                raise InvalidPolicyError(
                    f"Duplicate ratio_threshold {lower.ratio_threshold}; one of the "
                    f"two tiers would be unreachable.")
            if SEVERITY_ORDER[upper.severity] < SEVERITY_ORDER[lower.severity]:
                raise InvalidPolicyError(
                    f"Severity decreases from tier {lower.ratio_threshold} "
                    f"({lower.severity.value}) to {upper.ratio_threshold} "
                    f"({upper.severity.value}); a worse breach cannot be less severe.")
            if ACTION_ORDER[upper.action] < ACTION_ORDER[lower.action]:
                raise InvalidPolicyError(
                    f"Action weakens from tier {lower.ratio_threshold} "
                    f"({lower.action.value}) to {upper.ratio_threshold} "
                    f"({upper.action.value}); a worse breach cannot get a milder "
                    f"response.")
        return ordered

    # ------------------------------------------------------------------ ratio

    @staticmethod
    def compute_ratio(
        current_value: float,
        limit_value: float,
        direction: LimitDirection = LimitDirection.UPPER,
    ) -> float:
        """
        Breach magnitude as a multiple of the limit.

        See the module docstring for the two formulas and for why a negative
        UPPER metric is refused rather than reinterpreted.
        """
        current = _coerce_float(current_value, "current_value")
        limit = _coerce_float(limit_value, "limit_value")
        if limit <= 0:
            raise InvalidBreachError(
                f"limit_value must be > 0, got {limit}. A non-positive limit has no "
                f"ratio, and returning 'no action' would fail open on a live risk "
                f"control.")
        if _coerce_direction(direction) is LimitDirection.UPPER:
            if current < 0:
                raise InvalidBreachError(
                    f"current_value={current} is negative under LimitDirection.UPPER. "
                    f"Pass the breach magnitude (abs) for a signed metric such as "
                    f"drawdown, or use LimitDirection.LOWER for a floor. The engine "
                    f"will not guess: a negative ratio matches no tier and would "
                    f"report a 2.5x drawdown as 'no breach'.")
            return current / limit
        return max(0.0, 1.0 + (limit - current) / limit)

    # ----------------------------------------------------------------- legacy

    def evaluate(self, risk_metric: float, limit: float) -> EscalationResult:
        """
        Legacy threshold lookup, retained for backward compatibility.

        Deliberate fail-closed behaviour change: a non-positive ``limit``, a
        negative metric, or a NaN/Inf input now raises ``InvalidBreachError``.
        It previously returned ``ResponseAction.NONE`` -- ``evaluate(1e9, 0)``
        answered a nine-figure risk metric with "take no action".
        """
        metric = _coerce_float(risk_metric, "risk_metric")
        lim = _coerce_float(limit, "limit")
        if lim <= 0:
            raise InvalidBreachError(f"limit must be > 0, got {lim}.")
        if metric < 0:
            raise InvalidBreachError(
                f"risk_metric={metric} is negative; pass the breach magnitude.")
        ratio = metric / lim
        for level, action in self._legacy_desc:
            if ratio >= level:
                return EscalationResult(action, level)
        return EscalationResult(ResponseAction.NONE, 0.0)

    # --------------------------------------------------------------- decision

    def process_breach_event(self, event: BreachEvent) -> EscalationDecision:
        """
        Evaluate a breach event and return the graduated response.

        Order of operations: validate -> replay check -> ratio -> tier match ->
        duration promotion -> latch -> audit. Raises ``InvalidBreachError`` on
        any malformed or ambiguous input; callers must handle it rather than let
        it kill the monitoring loop.
        """
        event_id = _require_text(event.event_id, "event_id")
        metric_name = _require_text(event.metric_name, "metric_name")
        strategy_id = _require_text(event.strategy_id, "strategy_id")
        timestamp_iso = _normalise_timestamp(event.timestamp_iso)
        direction = _coerce_direction(event.direction)
        current = _coerce_float(event.current_value, "current_value")
        limit = _coerce_float(event.limit_value, "limit_value")
        duration = _coerce_float(event.duration_seconds, "duration_seconds")
        if duration < 0:
            raise InvalidBreachError(f"duration_seconds must be >= 0, got {duration}.")

        fingerprint = (metric_name, strategy_id, current, limit, duration,
                       direction.value, timestamp_iso)
        cached = self._replay_cache.get(event_id)
        if cached is not None:
            if cached.payload_fingerprint() == fingerprint:
                logger.info(
                    "Replay of event_id=%s suppressed; returning the original %s "
                    "decision without re-emitting the action.",
                    event_id, cached.action.value)
                return replace(cached, is_replay=True)
            logger.warning(
                "event_id=%s resubmitted with a changed payload; treating it as a "
                "re-evaluation of an ongoing breach, not a replay.", event_id)

        ratio = self.compute_ratio(current, limit, direction)

        matched: Optional[EscalationPolicy] = None
        matched_index = -1
        for index in range(len(self.policies) - 1, -1, -1):
            if ratio >= self.policies[index].ratio_threshold:
                matched = self.policies[index]
                matched_index = index
                break

        if matched is None:
            floor = self.policies[0].ratio_threshold
            return self._record(EscalationDecision(
                event_id=event_id,
                metric_name=metric_name,
                strategy_id=strategy_id,
                ratio=ratio,
                severity=SeverityLevel.INFO,
                action=ResponseAction.NONE,
                notification_channels=(),
                ack_deadline_seconds=0,
                is_sustained_breach=False,
                # Recorded, unlike before: a BreachEvent that turns out to be
                # sub-threshold is evidence about the upstream detector, and
                # dropping it left a mis-signed input with no trace at all.
                audit_notes=(
                    f"NO BREACH: {metric_name} for strategy '{strategy_id}' "
                    f"ratio={ratio:.4f} < lowest tier {floor:.4f} "
                    f"(direction={direction.value})."),
                timestamp_iso=timestamp_iso,
                direction=direction,
                current_value=current,
                limit_value=limit,
                duration_seconds=duration,
            ))

        severity = matched.severity
        action = matched.action
        channels: Tuple[NotificationChannel, ...] = tuple(matched.channels)
        ack_timeout = matched.ack_timeout_seconds

        is_sustained = duration >= self.sustained_breach_seconds
        is_duration_escalated = False
        note = ""
        if is_sustained and matched_index + 1 < len(self.policies):
            promoted = self.policies[matched_index + 1]
            severity = promoted.severity
            action = promoted.action
            channels = tuple(promoted.channels)
            ack_timeout = promoted.ack_timeout_seconds
            is_duration_escalated = True
            note = (f" Sustained {duration:.0f}s >= "
                    f"{self.sustained_breach_seconds:.0f}s: promoted from tier "
                    f"{matched.ratio_threshold:.2f} to {promoted.ratio_threshold:.2f}.")
        elif is_sustained:
            note = (f" Sustained {duration:.0f}s >= "
                    f"{self.sustained_breach_seconds:.0f}s at the top tier "
                    f"{matched.ratio_threshold:.2f}; no higher rung exists.")

        incident_key = (strategy_id, metric_name)
        is_latched = False
        if self.latch_escalations:
            previous = self._active_incidents.get(incident_key)
            if previous is not None and ACTION_ORDER[previous.action] > ACTION_ORDER[action]:
                note += (f" Latched: this observation warrants {action.value} but "
                         f"incident {incident_key} already reached "
                         f"{previous.action.value} (event {previous.event_id}); call "
                         f"reset_incident() to de-escalate.")
                severity = previous.severity
                action = previous.action
                channels = previous.notification_channels
                ack_timeout = previous.ack_deadline_seconds
                is_latched = True

        decision = EscalationDecision(
            event_id=event_id,
            metric_name=metric_name,
            strategy_id=strategy_id,
            ratio=ratio,
            severity=severity,
            action=action,
            notification_channels=channels,
            ack_deadline_seconds=ack_timeout,
            is_sustained_breach=is_sustained,
            audit_notes=(
                f"ESCALATION DECISION [{severity.value}]: {metric_name} for strategy "
                f"'{strategy_id}' ratio={ratio:.4f} ({current}/{limit}, "
                f"direction={direction.value}) -> Action: {action.value}, Channels: "
                f"{[c.value for c in channels]}, ack<={ack_timeout}s.{note}"),
            timestamp_iso=timestamp_iso,
            direction=direction,
            current_value=current,
            limit_value=limit,
            duration_seconds=duration,
            matched_threshold=matched.ratio_threshold,
            is_duration_escalated=is_duration_escalated,
            is_latched=is_latched,
        )

        if self.latch_escalations and action is not ResponseAction.NONE:
            self._active_incidents[incident_key] = decision
        return self._record(decision)

    # ------------------------------------------------------------------ state

    def _record(self, decision: EscalationDecision) -> EscalationDecision:
        """Append to the audit trail, remember the fingerprint, and log once."""
        self._audit_log.append(decision)
        if self._replay_cache_size:
            self._replay_cache[decision.event_id] = decision
            while len(self._replay_cache) > self._replay_cache_size:
                self._replay_cache.pop(next(iter(self._replay_cache)))
        if decision.severity is SeverityLevel.CRITICAL:
            logger.critical(decision.audit_notes)
        elif decision.severity is SeverityLevel.RED:
            logger.warning(decision.audit_notes)
        else:
            logger.info(decision.audit_notes)
        return decision

    def get_audit_trail(self) -> Tuple[EscalationDecision, ...]:
        """
        Every decision made, oldest first, as frozen records.

        The engine never truncates this list: silently dropping rows from a
        risk-control audit trail is worse than the memory. Drain and persist it.
        """
        return tuple(self._audit_log)

    def get_active_incidents(self) -> Dict[Tuple[str, str], EscalationDecision]:
        """Latched incidents by (strategy_id, metric_name), as a copy."""
        return dict(self._active_incidents)

    def reset_incident(self, strategy_id: str, metric_name: str) -> bool:
        """
        Clear the latch for one incident so it can de-escalate, returning whether
        a latch was present. De-escalation is deliberate, so it is logged.
        """
        key = (_require_text(strategy_id, "strategy_id"),
               _require_text(metric_name, "metric_name"))
        previous = self._active_incidents.pop(key, None)
        if previous is None:
            return False
        logger.warning(
            "Incident %s de-escalated by reset_incident(); latched action %s cleared "
            "(last event %s).", key, previous.action.value, previous.event_id)
        return True
