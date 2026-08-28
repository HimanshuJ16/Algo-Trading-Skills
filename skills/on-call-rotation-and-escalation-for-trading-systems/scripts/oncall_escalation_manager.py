"""
on-call-rotation-and-escalation-for-trading-systems: shift-aware on-call roster
resolution, severity-driven escalation laddering, and an auditable response-SLA
record for live trading system incidents.

Purpose
-------
Answer two questions, correctly, at 03:00: *who is on call for this tier right
now*, and *has this incident been acknowledged inside its response SLA*. Both
answers are audit artifacts. An escalation engine that quietly pages nobody, or
that records an SLA as met when the responder woke up an hour late, is worse
than no engine -- it manufactures evidence of a response that did not happen.

Clock model
-----------
Every timestamp is a **UTC epoch second** (``time.time()``). The engine cannot
detect a naive local-time timestamp by inspection, so it refuses the one symptom
it *can* see: an evaluation time meaningfully earlier than the incident creation
time. Previously that case was clamped by ``max(0.0, ...)``, which pinned
elapsed time at 0.0 and left the incident permanently at PRIMARY -- an incident
stamped with a mis-set clock simply never escalated.

Escalation timing convention
----------------------------
Thresholds are **cumulative minutes since incident creation**, not per-rule
delays. PagerDuty uses the opposite convention: its "escalates after N min" is
the time a responder has *at that level* before the incident moves to the next
rule, measured from when that level was notified (PagerDuty, "Escalation Policy
Basics"). Transposing a PagerDuty policy of 3 min then 5 min into this config
verbatim yields an executive page at t=5, not t=8. Convert before configuring.

Response SLA vs. escalation threshold
-------------------------------------
These are separate quantities and are configured separately. The SLA is the
promise ("a SEV-1 is acknowledged within 5 minutes"); the escalation thresholds
are the mechanism used to keep it. Reporting an SLA breach at the moment of a
*secondary* escalation -- as this engine previously did for SEV-2, flagging a
breach at 10 minutes against a documented 15-minute SLA -- puts a breach in the
audit trail that did not occur.

The default response SLAs follow the industry practice described in Beyer et
al., *Site Reliability Engineering* (O'Reilly, 2016), ch. 11 "Being On-Call":
"Typical values are 5 minutes for user-facing or otherwise highly time-critical
services, and 30 minutes for less time-sensitive systems." **These are
engineering conventions, not regulatory deadlines.** No regulator prescribes a
5-minute pager SLA. See ``references/standards.md`` for the obligations that
*are* mandatory, and for the incident-reporting clocks (DORA, Reg SCI) that run
in parallel with, and independently of, this pager ladder.

Acknowledgement is not resolution
---------------------------------
An acknowledged but unresolved incident is re-triggered after
``ack_timeout_mins``. Without it, "ack and go back to sleep" silences the pager
permanently. PagerDuty offers the same mechanism as a service setting ("An
acknowledged incident re-triggers after a specified amount of time"), turned off
by default; this engine enables it by default because the failure it prevents --
an unattended live trading fault -- is more expensive than an extra page.

What this engine does NOT do
----------------------------
It computes *who should be paged and whether the SLA held*. It sends nothing:
there is no PagerDuty, Opsgenie, SMS or telephony integration here, and the
``notification_channel`` field is an instruction to the caller's notifier, not
evidence that anything was delivered. It is also not a risk control -- it does
not halt trading, cancel orders, or trip a kill switch.
"""
import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "OnCallEngineer",
    "SystemIncident",
    "EscalationPolicyConfig",
    "OnCallEscalationReport",
    "OnCallEscalationManagerEngine",
    "PRIMARY",
    "SECONDARY",
    "EXECUTIVE",
    "TIER_ORDER",
    "VALID_SEVERITIES",
]

PRIMARY = "PRIMARY"
SECONDARY = "SECONDARY"
EXECUTIVE = "EXECUTIVE"

#: Escalation tiers in ascending order of authority.
TIER_ORDER: Tuple[str, ...] = (PRIMARY, SECONDARY, EXECUTIVE)

#: The only severities this engine recognises. Anything else is handled by
#: ``EscalationPolicyConfig.unknown_severity_policy`` -- never silently treated
#: as the least severe class, which is what the previous ``else: # SEV_3``
#: branch did to every unrecognised label, including "CRITICAL".
VALID_SEVERITIES: Tuple[str, ...] = ("SEV_1", "SEV_2", "SEV_3")

#: Sub-second differences between an incident timestamp and an evaluation
#: timestamp are ordinary clock jitter between hosts and are clamped to zero.
#: Anything larger is a wrong clock or a wrong timezone and is rejected.
DEFAULT_MAX_CLOCK_SKEW_SECONDS = 2.0


def _as_epoch_seconds(value: object, label: str) -> float:
    """
    Coerce ``value`` to a finite float epoch second or raise.

    Alert payloads arrive as JSON, where a timestamp is as likely to be the
    string "1700000000" as a number. A string passes a bare ``math.isfinite``
    check and then raises ``TypeError`` from an unrelated subtraction much
    later, so every timestamp entering the engine is converted at the boundary.
    """
    try:
        coerced = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError(f"{label} is not a number: {value!r}.") from None
    if not math.isfinite(coerced):
        raise ValueError(f"{label} is not a finite epoch second: {value!r}.")
    return coerced


@dataclass
class OnCallEngineer:
    """
    One roster entry. ``shift_start_utc`` / ``shift_end_utc`` are optional UTC
    epoch seconds bounding the shift as the half-open interval ``[start, end)``.

    Half-open is deliberate: with a closed interval, the outgoing and incoming
    engineer are both "on call" at the handover instant, and a page at that
    instant either double-pages or -- worse -- leaves each assuming the other
    owns it.

    An entry with neither bound set is **always on call** for its tier and acts
    as the fallback when no scheduled shift covers the moment of the page.
    """
    engineer_id: str
    name: str
    tier: str                            # 'PRIMARY', 'SECONDARY', 'EXECUTIVE'
    phone: str
    email: str
    shift_start_utc: Optional[float] = None
    shift_end_utc: Optional[float] = None

    def __post_init__(self) -> None:
        self.tier = str(self.tier).strip().upper()
        if self.tier not in TIER_ORDER:
            raise ValueError(
                f"OnCallEngineer '{self.engineer_id}' has tier {self.tier!r}; "
                f"expected one of {list(TIER_ORDER)}."
            )
        # Coerce rather than merely test: a JSON roster delivers epoch seconds as
        # strings, which pass a finiteness check and then raise TypeError inside
        # an unrelated comparison hours later.
        for label in ("shift_start_utc", "shift_end_utc"):
            value = getattr(self, label)
            if value is None:
                continue
            try:
                coerced = float(value)
            except (TypeError, ValueError):
                raise ValueError(
                    f"{label} for '{self.engineer_id}' is not a number: {value!r}."
                ) from None
            if not math.isfinite(coerced):
                raise ValueError(
                    f"{label} for '{self.engineer_id}' is not a finite epoch second."
                )
            setattr(self, label, coerced)
        if (self.shift_start_utc is not None and self.shift_end_utc is not None
                and self.shift_end_utc <= self.shift_start_utc):
            raise ValueError(
                f"Shift for '{self.engineer_id}' ends at or before it starts "
                f"({self.shift_start_utc} -> {self.shift_end_utc})."
            )

    @property
    def has_shift(self) -> bool:
        """True when this entry is bounded by a schedule rather than always-on."""
        return self.shift_start_utc is not None or self.shift_end_utc is not None

    def is_on_shift(self, at_utc: float) -> bool:
        """Whether this engineer is on call at ``at_utc`` (half-open interval)."""
        if self.shift_start_utc is not None and at_utc < self.shift_start_utc:
            return False
        if self.shift_end_utc is not None and at_utc >= self.shift_end_utc:
            return False
        return True

    def contact_for(self, channel: str) -> str:
        """
        Contact string this engineer can be reached on for ``channel``.

        Chat channels fall back to ``engineer_id``, which the caller's notifier
        must map to a real chat handle -- this dataclass holds no chat identity,
        so a chat page cannot be proven undeliverable here the way a missing
        phone number can.
        """
        if channel in ("PHONE_CALL", "SMS"):
            return self.phone
        if channel == "EMAIL":
            return self.email
        return self.engineer_id


@dataclass
class SystemIncident:
    """
    A single incident. ``severity`` is validated on registration rather than
    here, so that ingestion adapters can hand over whatever label their upstream
    alerting system produced and let the configured ``unknown_severity_policy``
    decide what happens to it.
    """
    incident_id: str
    severity: str                        # 'SEV_1', 'SEV_2', 'SEV_3'
    title: str
    description: str
    created_at_utc: float
    acknowledged: bool = False
    acknowledged_by: Optional[str] = None
    #: First acknowledgement, and the only one used to measure response latency
    #: against the SLA. A later re-acknowledgement after a re-trigger must not
    #: overwrite it, or a 40-minute response is retroactively recorded as fast.
    acknowledged_at_utc: Optional[float] = None
    resolved: bool = False
    resolved_at_utc: Optional[float] = None
    #: Most recent acknowledgement, used only to time the ack-timeout re-trigger.
    last_ack_at_utc: Optional[float] = None
    #: True when ``severity`` was not recognised and was coerced by policy.
    severity_was_coerced: bool = False
    #: Severity exactly as supplied, retained for the audit trail when coerced.
    reported_severity: Optional[str] = None
    #: Tiers already paged, so a polling caller does not re-page on every tick.
    notified_tiers: List[str] = field(default_factory=list)
    #: The acknowledgement timestamp whose lapse was already counted, so a
    #: polling caller does not increment the counter on every tick.
    last_retriggered_ack_utc: Optional[float] = None
    #: How many times the acknowledgement timed out without a resolution. A
    #: re-acknowledgement restores ACKNOWLEDGED status, so without this counter
    #: an incident that was acknowledged promptly and then abandoned for half an
    #: hour is indistinguishable from one that was handled.
    retrigger_count: int = 0


@dataclass
class EscalationPolicyConfig:
    """
    Escalation thresholds are **cumulative minutes since incident creation**
    (see the module docstring). Response SLAs are configured separately from the
    thresholds that enforce them.
    """
    sev1_sec_escalate_mins: float = 3.0    # PRIMARY -> SECONDARY at t=3 from creation
    sev1_exec_escalate_mins: float = 5.0   # PRIMARY -> EXECUTIVE at t=5 from creation
    sev2_sec_escalate_mins: float = 10.0
    sev3_sec_escalate_mins: float = 30.0

    #: Terminal escalation for the lower severities. Without one, an
    #: unacknowledged SEV-2 sits at SECONDARY indefinitely with no further
    #: recourse. SEV-3 has none by default: waking an executive for an
    #: informational warning is the alert-fatigue failure this skill prevents.
    sev2_exec_escalate_mins: Optional[float] = 30.0
    sev3_exec_escalate_mins: Optional[float] = None

    #: Acknowledgement deadlines. Engineering conventions from Google SRE
    #: practice, not regulatory deadlines -- see the module docstring.
    sev1_response_sla_mins: float = 5.0
    sev2_response_sla_mins: float = 15.0
    sev3_response_sla_mins: float = 60.0

    #: Re-trigger an acknowledged but unresolved incident after this many
    #: minutes. ``None`` disables it and restores the "an acknowledgement
    #: silences the pager forever" behaviour -- do not disable it for SEV-1
    #: without a compensating control.
    ack_timeout_mins: Optional[float] = 30.0

    #: 'ESCALATE' treats an unrecognised severity as SEV_1 and logs an error;
    #: 'REJECT' refuses the incident outright. ESCALATE is the default because
    #: dropping an alert is a worse outcome than an over-page, and because the
    #: previous behaviour -- silently demoting it to SEV_3 and Slack -- is the
    #: worst of the three.
    unknown_severity_policy: str = "ESCALATE"

    max_clock_skew_seconds: float = DEFAULT_MAX_CLOCK_SKEW_SECONDS

    def __post_init__(self) -> None:
        if self.unknown_severity_policy not in ("ESCALATE", "REJECT"):
            raise ValueError(
                "unknown_severity_policy must be 'ESCALATE' or 'REJECT', got "
                f"{self.unknown_severity_policy!r}."
            )
        if self.max_clock_skew_seconds < 0:
            raise ValueError("max_clock_skew_seconds must be >= 0.")
        if self.ack_timeout_mins is not None and self.ack_timeout_mins <= 0:
            raise ValueError("ack_timeout_mins must be positive, or None to disable.")
        for sev in VALID_SEVERITIES:
            sla = self.response_sla_mins(sev)
            if not math.isfinite(sla) or sla <= 0:
                raise ValueError(
                    f"{sev} response SLA must be finite and positive, got {sla}."
                )
            # A non-monotonic ladder silently makes a tier unreachable: with
            # sev1_sec=6 and sev1_exec=5, the SECONDARY rung is never selected
            # and the secondary engineer is never paged at all.
            thresholds = [t for t, _ in self.ladder(sev)]
            if thresholds != sorted(thresholds) or len(set(thresholds)) != len(thresholds):
                raise ValueError(
                    f"{sev} escalation thresholds {thresholds} must be strictly increasing; "
                    "a non-increasing ladder makes an escalation tier unreachable."
                )

    def response_sla_mins(self, severity: str) -> float:
        """Acknowledgement deadline, in minutes from creation, for ``severity``."""
        return {
            "SEV_1": self.sev1_response_sla_mins,
            "SEV_2": self.sev2_response_sla_mins,
            "SEV_3": self.sev3_response_sla_mins,
        }[severity]

    def ladder(self, severity: str) -> List[Tuple[float, str]]:
        """
        Escalation rungs for ``severity`` as ``(cumulative_minutes, tier)``,
        ascending. Rung 0 is always PRIMARY at t=0: the first tier is notified
        immediately, never after a delay.
        """
        rungs: List[Tuple[float, str]] = [(0.0, PRIMARY)]
        if severity == "SEV_1":
            rungs.append((self.sev1_sec_escalate_mins, SECONDARY))
            rungs.append((self.sev1_exec_escalate_mins, EXECUTIVE))
        elif severity == "SEV_2":
            rungs.append((self.sev2_sec_escalate_mins, SECONDARY))
            if self.sev2_exec_escalate_mins is not None:
                rungs.append((self.sev2_exec_escalate_mins, EXECUTIVE))
        else:
            rungs.append((self.sev3_sec_escalate_mins, SECONDARY))
            if self.sev3_exec_escalate_mins is not None:
                rungs.append((self.sev3_exec_escalate_mins, EXECUTIVE))
        return rungs

    def channel_for(self, severity: str) -> str:
        """
        Notification channel by severity. SEV-3 is deliberately a chat message,
        never a phone call: paging a human out of bed for an informational
        warning is how a rotation is trained to ignore its pager.
        """
        return {"SEV_1": "PHONE_CALL", "SEV_2": "SMS", "SEV_3": "SLACK"}[severity]


@dataclass
class OnCallEscalationReport:
    incident_id: str
    severity: str
    elapsed_minutes: float
    current_assigned_tier: str
    current_responder_name: str
    notification_channel: str            # 'PHONE_CALL', 'SMS', 'SLACK', 'NONE'
    is_sla_breached: bool
    status: str                          # see OnCallEscalationManagerEngine docstring
    audit_notes: str

    #: Roster id of the assigned responder, or None when nobody was resolved.
    #: Distinct from ``current_responder_name`` -- the previous version wrote
    #: the acknowledging engineer's *id* into the name field.
    current_responder_id: Optional[str] = None
    #: False when no engineer could be resolved for the assigned tier, or the
    #: resolved engineer has no contact address for the required channel. A
    #: report with this False describes a page that will reach nobody.
    is_notification_deliverable: bool = True
    delivery_warnings: List[str] = field(default_factory=list)
    #: True only on the tick where a tier is reached for the first time. Page on
    #: this, not on every poll, or a 5-second polling loop dials the primary
    #: engineer 36 times before the first escalation is even due. An ack-timeout
    #: re-trigger resets the record, so it is always True on the re-trigger tick
    #: even when that tier had already been paged before the acknowledgement.
    is_new_escalation: bool = False
    #: Minutes from creation to first acknowledgement, once acknowledged.
    ack_latency_minutes: Optional[float] = None
    acknowledged_by_id: Optional[str] = None
    #: The deadline ``ack_latency_minutes`` is judged against.
    response_sla_minutes: float = 0.0
    #: True when the incident's severity label was unrecognised and coerced.
    severity_was_coerced: bool = False
    reported_severity: Optional[str] = None
    #: Number of ack-timeout re-triggers so far. Non-zero on an otherwise
    #: ACKNOWLEDGED incident means it was acknowledged and then abandoned.
    retrigger_count: int = 0


class OnCallEscalationManagerEngine:
    """
    Shift-aware on-call rotation and escalation manager for live trading system
    incidents.

    Report ``status`` values:

    ``ACTIVE_PRIMARY``
        Unacknowledged, still inside the primary responder's window.
    ``ESCALATED_WARNING``
        Unacknowledged and escalated past PRIMARY, SLA not yet breached.
    ``SLA_BREACH``
        Unacknowledged past the severity's response SLA.
    ``ACKNOWLEDGED``
        Acknowledged within the SLA.
    ``ACKNOWLEDGED_LATE``
        Acknowledged, but after the SLA had already been breached. Recorded as a
        breach permanently: acknowledging late does not un-breach an SLA, and
        the previous version's blanket ``is_sla_breached=False`` on any
        acknowledgement erased real breaches from the audit trail.
    ``RE_TRIGGERED_ACK_TIMEOUT``
        Acknowledged but still unresolved after ``ack_timeout_mins``; paging has
        resumed at whichever tier the elapsed time now warrants.
    ``RESOLVED``
        Closed. No further escalation.

    ``evaluate_escalation`` mutates incident state by default (it records which
    tiers have been paged so that a polling caller can deduplicate). Pass
    ``record_notification=False`` for a what-if evaluation that leaves the
    deduplication record untouched.
    """

    def __init__(
        self,
        roster: Sequence[OnCallEngineer],
        config: Optional[EscalationPolicyConfig] = None
    ) -> None:
        self.config = config or EscalationPolicyConfig()
        self.roster_by_tier: Dict[str, List[OnCallEngineer]] = {t: [] for t in TIER_ORDER}
        seen_ids: Dict[str, str] = {}
        for engineer in roster:
            if engineer.engineer_id in seen_ids:
                raise ValueError(
                    f"Duplicate engineer_id '{engineer.engineer_id}' in roster; ids must be "
                    "unique so that acknowledgements can be attributed."
                )
            seen_ids[engineer.engineer_id] = engineer.tier
            self.roster_by_tier[engineer.tier].append(engineer)

        # An unstaffed tier is not an error at construction -- a firm may run
        # without an executive rung -- but it is the single most common reason a
        # page reaches nobody, so it is surfaced now rather than at 03:00.
        for tier in TIER_ORDER:
            if not self.roster_by_tier[tier]:
                logger.warning(
                    "No engineer registered for tier %s; any escalation to that tier will be "
                    "reported as undeliverable.", tier
                )
        self.active_incidents: Dict[str, SystemIncident] = {}

    # ------------------------------------------------------------------ #
    # Incident lifecycle
    # ------------------------------------------------------------------ #
    def create_incident(self, incident: SystemIncident) -> SystemIncident:
        """
        Register an incident and return the registered object. Idempotent on
        ``incident_id``.

        A duplicate registration returns the incident already held and leaves it
        untouched. Re-registering previously overwrote the stored incident,
        which reset ``created_at_utc`` and silently discarded any existing
        acknowledgement -- so a redelivered alert webhook un-acknowledged an
        incident a responder was already working.
        """
        if not incident.incident_id:
            raise ValueError("incident_id must be a non-empty string.")
        incident.created_at_utc = _as_epoch_seconds(
            incident.created_at_utc, f"created_at_utc for '{incident.incident_id}'")

        existing = self.active_incidents.get(incident.incident_id)
        if existing is not None:
            logger.warning(
                "Duplicate create_incident for [%s]; keeping the incident registered at %s "
                "(acknowledged=%s). State was not reset.",
                incident.incident_id, existing.created_at_utc, existing.acknowledged
            )
            return existing

        reported = str(incident.severity).strip().upper()
        if reported in VALID_SEVERITIES:
            incident.severity = reported
        elif self.config.unknown_severity_policy == "REJECT":
            raise ValueError(
                f"Incident '{incident.incident_id}' has unrecognised severity "
                f"{incident.severity!r}; expected one of {list(VALID_SEVERITIES)}."
            )
        else:
            logger.error(
                "Incident [%s] has unrecognised severity %r; treating it as SEV_1. Fix the "
                "severity mapping in the alert source -- an unmapped label must never be "
                "guessed downward.", incident.incident_id, incident.severity
            )
            incident.reported_severity = reported
            incident.severity_was_coerced = True
            incident.severity = "SEV_1"

        self.active_incidents[incident.incident_id] = incident
        logger.info(
            "Created incident [%s] Severity: %s - '%s'",
            incident.incident_id, incident.severity, incident.title
        )
        return incident

    def acknowledge_incident(
        self, incident_id: str, engineer_id: str, ack_time_utc: float
    ) -> bool:
        """
        Record an acknowledgement. Returns False if the incident is unknown or
        already resolved.

        The **first** acknowledgement is the one measured against the SLA; a
        later re-acknowledgement (after an ack-timeout re-trigger) updates only
        ``last_ack_at_utc``. Overwriting the first would let a re-ack at t=41
        replace a genuine response at t=2.
        """
        inc = self.active_incidents.get(incident_id)
        if inc is None:
            logger.warning(
                "acknowledge_incident called for unknown incident_id '%s'.", incident_id
            )
            return False
        if inc.resolved:
            logger.warning(
                "Incident [%s] is already resolved; acknowledgement ignored.", incident_id
            )
            return False

        ack_time_utc = _as_epoch_seconds(ack_time_utc, "ack_time_utc")
        self._check_clock(inc, ack_time_utc, "acknowledgement")
        if not self._is_known_engineer(engineer_id):
            logger.warning(
                "Incident [%s] acknowledged by '%s', who is not on the roster. Recording it, "
                "but the responder cannot be attributed to a tier.", incident_id, engineer_id
            )

        inc.acknowledged = True
        inc.acknowledged_by = engineer_id
        inc.last_ack_at_utc = ack_time_utc
        if inc.acknowledged_at_utc is None:
            inc.acknowledged_at_utc = ack_time_utc
            latency = max(0.0, (ack_time_utc - inc.created_at_utc) / 60.0)
            logger.info(
                "Incident [%s] ACKNOWLEDGED by %s after %.2f mins (SLA %.1f mins).",
                incident_id, engineer_id, latency,
                self.config.response_sla_mins(inc.severity)
            )
        else:
            logger.info("Incident [%s] re-acknowledged by %s.", incident_id, engineer_id)
        return True

    def resolve_incident(self, incident_id: str, resolved_at_utc: float) -> bool:
        """
        Close an incident and stop all escalation. Returns False if unknown.

        Resolution does not require a prior acknowledgement (an incident can
        self-clear), but resolving an unacknowledged incident that already
        breached its SLA leaves the breach on the record.
        """
        inc = self.active_incidents.get(incident_id)
        if inc is None:
            logger.warning("resolve_incident called for unknown incident_id '%s'.", incident_id)
            return False
        if inc.resolved:
            logger.info("Incident [%s] already resolved at %s.", incident_id, inc.resolved_at_utc)
            return True

        resolved_at_utc = _as_epoch_seconds(resolved_at_utc, "resolved_at_utc")
        self._check_clock(inc, resolved_at_utc, "resolution")
        inc.resolved = True
        inc.resolved_at_utc = resolved_at_utc
        if not inc.acknowledged:
            logger.warning(
                "Incident [%s] resolved without ever being acknowledged; the response SLA "
                "record stands as breached if it was.", incident_id
            )
        logger.info(
            "Incident [%s] RESOLVED after %.2f mins.",
            incident_id, max(0.0, (resolved_at_utc - inc.created_at_utc) / 60.0)
        )
        return True

    def purge_resolved_incidents(self, resolved_before_utc: float) -> int:
        """
        Drop incidents resolved before ``resolved_before_utc`` and return the
        number removed. A long-lived pager process otherwise accumulates every
        incident it has ever seen. Purge only after the retention copy is
        durable elsewhere -- these objects are the SLA audit record.
        """
        stale = [
            i for i, inc in self.active_incidents.items()
            if inc.resolved and inc.resolved_at_utc is not None
            and inc.resolved_at_utc < resolved_before_utc
        ]
        for incident_id in stale:
            del self.active_incidents[incident_id]
        if stale:
            logger.info("Purged %d resolved incident(s) from active state.", len(stale))
        return len(stale)

    # ------------------------------------------------------------------ #
    # Evaluation
    # ------------------------------------------------------------------ #
    def evaluate_escalation(
        self,
        incident_id: str,
        current_time_utc: float,
        record_notification: bool = True
    ) -> OnCallEscalationReport:
        """
        Evaluate an incident against its severity policy at ``current_time_utc``.

        Raises ``ValueError`` for an unknown ``incident_id`` or for an
        evaluation time earlier than the incident's creation time by more than
        ``max_clock_skew_seconds``.
        """
        inc = self.active_incidents.get(incident_id)
        if inc is None:
            raise ValueError(f"Unknown incident_id '{incident_id}'.")
        current_time_utc = _as_epoch_seconds(current_time_utc, "current_time_utc")

        self._check_clock(inc, current_time_utc, "evaluation")
        elapsed_mins = max(0.0, (current_time_utc - inc.created_at_utc) / 60.0)
        sla_mins = self.config.response_sla_mins(inc.severity)
        ack_latency: Optional[float] = None
        if inc.acknowledged_at_utc is not None:
            ack_latency = max(0.0, (inc.acknowledged_at_utc - inc.created_at_utc) / 60.0)

        if inc.resolved:
            resolution_mins = max(
                0.0, ((inc.resolved_at_utc or inc.created_at_utc) - inc.created_at_utc) / 60.0
            )
            breached = (
                ack_latency > sla_mins if ack_latency is not None
                else resolution_mins > sla_mins
            )
            notes = (
                f"Incident [{incident_id}] RESOLVED after {resolution_mins:.2f} mins "
                f"({inc.severity} acknowledgement SLA {sla_mins:.1f} mins, "
                f"breached={breached})."
            )
            logger.info(notes)
            return self._closed_report(
                inc, elapsed_mins, "RESOLVED", sla_mins, ack_latency, notes, breached
            )

        # An acknowledged incident stops escalating -- until the acknowledgement
        # itself times out without a resolution.
        if inc.acknowledged and not self._ack_has_timed_out(inc, current_time_utc):
            breached = ack_latency is not None and ack_latency > sla_mins
            status = "ACKNOWLEDGED_LATE" if breached else "ACKNOWLEDGED"
            responder_name = self._name_for_engineer_id(inc.acknowledged_by)
            if ack_latency is not None:
                notes = (
                    f"Incident [{incident_id}] is {status} by {responder_name} after "
                    f"{ack_latency:.2f} mins against a {sla_mins:.1f} min {inc.severity} SLA."
                )
            else:
                notes = f"Incident [{incident_id}] is {status} by {responder_name}."
            if breached:
                logger.error("SLA BREACH (late acknowledgement): %s", notes)
            else:
                logger.info(notes)
            return self._closed_report(
                inc, elapsed_mins, status, sla_mins, ack_latency, notes, breached
            )

        # Unacknowledged, or acknowledged and re-triggered: walk the ladder.
        assigned_tier = self._tier_for_elapsed(inc.severity, elapsed_mins)
        channel = self.config.channel_for(inc.severity)
        responder, warnings = self._resolve_responder(assigned_tier, current_time_utc, channel)
        is_breached = elapsed_mins >= sla_mins
        # Count one re-trigger per acknowledgement that lapses, not one per poll.
        is_new_retrigger = inc.acknowledged and inc.last_ack_at_utc != inc.last_retriggered_ack_utc

        re_triggered = inc.acknowledged
        if re_triggered:
            status = "RE_TRIGGERED_ACK_TIMEOUT"
            if is_new_retrigger and record_notification:
                inc.retrigger_count += 1
                inc.last_retriggered_ack_utc = inc.last_ack_at_utc
                # Clear the paged-tier record so the re-trigger pages afresh.
                # Without this, an incident that had already reached this tier
                # before being acknowledged re-triggers with
                # is_new_escalation=False, and a caller that pages on that flag
                # sends nothing -- silently defeating the ack timeout.
                inc.notified_tiers.clear()
        elif is_breached:
            status = "SLA_BREACH"
        elif assigned_tier != PRIMARY:
            status = "ESCALATED_WARNING"
        else:
            status = "ACTIVE_PRIMARY"

        is_new_escalation = assigned_tier not in inc.notified_tiers
        if is_new_escalation and record_notification:
            inc.notified_tiers.append(assigned_tier)

        responder_name = responder.name if responder is not None else "UNASSIGNED"
        responder_id = responder.engineer_id if responder is not None else None
        notes = (
            f"INCIDENT ESCALATION [{incident_id} - {status}]: Severity = {inc.severity}, "
            f"Elapsed = {elapsed_mins:.1f} mins (SLA {sla_mins:.1f} mins). "
            f"Assigned Tier = {assigned_tier} ({responder_name}), Channel = {channel}."
        )
        if warnings:
            notes += " UNDELIVERABLE: " + " ".join(warnings)
            logger.error("PAGE WILL REACH NOBODY: %s", notes)
        elif is_breached or re_triggered:
            logger.error("SLA BREACH ALERT: %s", notes)
        elif assigned_tier != PRIMARY:
            logger.warning("ESCALATION ALERT: %s", notes)
        else:
            logger.info(notes)

        return OnCallEscalationReport(
            incident_id=incident_id,
            severity=inc.severity,
            elapsed_minutes=round(elapsed_mins, 2),
            current_assigned_tier=assigned_tier,
            current_responder_name=responder_name,
            notification_channel=channel,
            is_sla_breached=is_breached,
            status=status,
            audit_notes=notes,
            current_responder_id=responder_id,
            is_notification_deliverable=not warnings,
            delivery_warnings=warnings,
            is_new_escalation=is_new_escalation,
            ack_latency_minutes=round(ack_latency, 2) if ack_latency is not None else None,
            acknowledged_by_id=inc.acknowledged_by,
            response_sla_minutes=sla_mins,
            severity_was_coerced=inc.severity_was_coerced,
            reported_severity=inc.reported_severity,
            retrigger_count=inc.retrigger_count,
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _check_clock(self, inc: SystemIncident, at_utc: float, label: str) -> None:
        skew = inc.created_at_utc - at_utc
        if skew > self.config.max_clock_skew_seconds:
            raise ValueError(
                f"{label} time {at_utc} precedes incident [{inc.incident_id}] creation time "
                f"{inc.created_at_utc} by {skew:.1f}s, beyond the "
                f"{self.config.max_clock_skew_seconds:.1f}s skew tolerance. Both must be UTC "
                "epoch seconds -- a local-time timestamp here pins elapsed time at zero and "
                "the incident never escalates."
            )

    def _ack_has_timed_out(self, inc: SystemIncident, now_utc: float) -> bool:
        timeout = self.config.ack_timeout_mins
        if timeout is None or inc.last_ack_at_utc is None:
            return False
        return (now_utc - inc.last_ack_at_utc) / 60.0 >= timeout

    def _tier_for_elapsed(self, severity: str, elapsed_mins: float) -> str:
        tier = PRIMARY
        for threshold, rung_tier in self.config.ladder(severity):
            if elapsed_mins >= threshold:
                tier = rung_tier
        return tier

    def _resolve_responder(
        self, tier: str, at_utc: float, channel: str
    ) -> Tuple[Optional[OnCallEngineer], List[str]]:
        """
        Resolve who is on call for ``tier`` at ``at_utc``.

        Scheduled engineers whose shift covers the moment win over always-on
        entries; among overlapping shifts the most recently started one wins,
        with the engineer id as a deterministic tie-break. Returns
        ``(None, warnings)`` when nobody can be reached, rather than fabricating
        a placeholder responder with an empty phone number -- the previous
        version returned a synthetic "Duty Engineer" and a report that looked
        entirely normal while the page went nowhere.
        """
        candidates = self.roster_by_tier.get(tier, [])
        if not candidates:
            return None, [f"No engineer is registered for tier {tier}."]

        scheduled = [e for e in candidates if e.has_shift and e.is_on_shift(at_utc)]
        if scheduled:
            scheduled.sort(key=lambda e: (e.shift_start_utc or float("-inf"), e.engineer_id))
            chosen = scheduled[-1]
            if len(scheduled) > 1:
                logger.warning(
                    "%d overlapping %s shifts cover %s; paging '%s' (most recent shift start).",
                    len(scheduled), tier, at_utc, chosen.engineer_id
                )
        else:
            always_on = sorted(
                (e for e in candidates if not e.has_shift), key=lambda e: e.engineer_id
            )
            if not always_on:
                return None, [
                    f"Tier {tier} has {len(candidates)} scheduled engineer(s) but none on shift "
                    f"at {at_utc}, and no always-on fallback. This is a hole in the rota."
                ]
            chosen = always_on[0]
            if any(e.has_shift for e in candidates):
                logger.warning(
                    "No scheduled %s shift covers %s; falling back to always-on engineer '%s'.",
                    tier, at_utc, chosen.engineer_id
                )

        if not chosen.contact_for(channel):
            return chosen, [
                f"{chosen.name} ({chosen.engineer_id}) has no contact address for channel "
                f"{channel}."
            ]
        return chosen, []

    def _is_known_engineer(self, engineer_id: Optional[str]) -> bool:
        return any(
            e.engineer_id == engineer_id
            for engineers in self.roster_by_tier.values()
            for e in engineers
        )

    def _name_for_engineer_id(self, engineer_id: Optional[str]) -> str:
        if engineer_id is None:
            return "Unknown"
        for engineers in self.roster_by_tier.values():
            for e in engineers:
                if e.engineer_id == engineer_id:
                    return e.name
        return engineer_id

    def _closed_report(
        self,
        inc: SystemIncident,
        elapsed_mins: float,
        status: str,
        sla_mins: float,
        ack_latency: Optional[float],
        notes: str,
        is_breached: bool,
    ) -> OnCallEscalationReport:
        """Report for an incident that is no longer escalating."""
        return OnCallEscalationReport(
            incident_id=inc.incident_id,
            severity=inc.severity,
            elapsed_minutes=round(elapsed_mins, 2),
            current_assigned_tier=status,
            current_responder_name=self._name_for_engineer_id(inc.acknowledged_by),
            notification_channel="NONE",
            is_sla_breached=is_breached,
            status=status,
            audit_notes=notes,
            current_responder_id=inc.acknowledged_by,
            is_notification_deliverable=True,
            delivery_warnings=[],
            is_new_escalation=False,
            ack_latency_minutes=round(ack_latency, 2) if ack_latency is not None else None,
            acknowledged_by_id=inc.acknowledged_by,
            response_sla_minutes=sla_mins,
            severity_was_coerced=inc.severity_was_coerced,
            reported_severity=inc.reported_severity,
            retrigger_count=inc.retrigger_count,
        )
