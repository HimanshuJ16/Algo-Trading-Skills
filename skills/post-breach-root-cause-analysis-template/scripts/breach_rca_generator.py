"""
post-breach-root-cause-analysis-template: structured post-incident Root Cause
Analysis (RCA) builder for trading-system breaches.

Purpose
-------
After a risk-limit breach, a runaway algorithm, or a severe unexpected
drawdown, someone has to write the post-mortem. This engine does not decide
*what* went wrong; it enforces that the record of what went wrong is complete
enough to be worth keeping, and renders it in two forms: a human-readable
Markdown document and a deterministic JSON payload for an incident database.

The completeness gates it applies are house standards, not regulatory
minimums. See ``references/standards.md`` for what regulators do and do not
require, and of whom.

What it enforces
----------------
Structural errors raise ``ValueError`` -- a malformed incident record is a bug
in the caller, not an audit finding:

  * empty identifiers, blank descriptions, non-finite or negative money,
  * naive (timezone-less) datetimes anywhere,
  * ``contained_at`` earlier than ``detected_at``,
  * a severity or CAPA type outside the declared enum.

Quality gates produce *findings* and mark the RCA incomplete rather than
raising, because auditing RCA completeness is the whole point of the tool:

  ``INSUFFICIENT_5_WHYS_DEPTH``      fewer than ``min_five_whys_depth`` levels
  ``MISSING_ACTION_ITEMS``           no CAPA item at all
  ``MISSING_TIMELINE``               no timeline event
  ``CAPA_MISSING_OWNER_OR_DUE_DATE`` a CAPA item without an owner or a due date
  ``RULE_VIOLATION_ASSESSMENT_MISSING``
                                     ``possible_rule_violation`` left ``None``
  ``RCA_PAST_DUE``                   ``generated_at`` after ``rca_due_by``
  ``TERMINAL_BLAME_ATTRIBUTION``     advisory only; see below

``status`` reports the highest-precedence blocking finding
(``_STATUS_PRECEDENCE``) so a caller switching on a single string still sees
the most serious problem; ``validation_findings`` carries all of them.
Advisory findings never change ``status`` or ``is_valid_rca``.

Determinism
-----------
No wall-clock reads. ``generated_at`` is supplied by the caller, so the same
input always renders the same document and the same JSON payload -- a
requirement for a record that may have to be reproduced during an examination.

Timestamps
----------
Every datetime must be timezone-aware and is normalised to UTC before use.
This is deliberate: the most common way a trading post-mortem misleads is a
timeline assembled from hosts on different clocks, where the ordering of
events is an artifact of the clock offsets rather than of causality. Each
``TimelineEvent`` also records the ``source`` clock it came from, so a reader
can see which hosts the sequence depends on.

Sign convention
---------------
``financial_loss_usd`` and ``unauthorized_turnover_usd`` are **magnitudes**:
non-negative numbers, where a loss of 25,000 USD is ``25000.0``, not
``-25000.0``. Negative values raise. An incident with no realised loss is
``0.0``.

Blameless-review heuristic
--------------------------
``TERMINAL_BLAME_ATTRIBUTION`` fires when the *last* "why" reduces to a person
rather than to a control that failed ("human error", "operator error", ...).
It is a crude case-insensitive substring match against a short phrase list, it
is advisory, and it is not a substitute for review by a person. It exists
because stopping the drill-down at the individual is the most common defect in
trading post-mortems, and it will both miss real cases and occasionally fire
on a legitimately worded step.

Limitations
-----------
- Does **not** verify the truthfulness of anything: a five-level chain of
  fiction passes every gate.
- Does **not** compute financial impact. The numbers are caller-supplied and
  must be reconciled against the books separately.
- Does **not** file anything with anyone. It produces a record; whether that
  record triggers a reporting obligation is a legal determination, not an
  output of this module.
"""
import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

_WHITESPACE_RUN = re.compile(r"\s+")

# Advisory heuristic only -- see module docstring. Matched case-insensitively
# against the final "why" after whitespace normalisation.
_TERMINAL_BLAME_PHRASES = (
    "human error",
    "operator error",
    "user error",
    "engineer error",
    "engineer mistake",
    "trader mistake",
    "carelessness",
    "negligence",
    "forgot to",
    "failed to pay attention",
)


class Severity(Enum):
    """Incident severity. Recorded, not interpreted -- the engine gates on
    completeness, not on severity."""

    CRITICAL = "CRITICAL"
    MAJOR = "MAJOR"
    MINOR = "MINOR"


class CapaType(Enum):
    """CAPA classification.

    ``CORRECTIVE`` addresses the instance that occurred; ``PREVENTIVE``
    addresses the class of failure so it cannot recur. An RCA whose CAPA items
    are entirely corrective has fixed one incident and prevented nothing --
    ``has_preventive_action`` on the report makes that visible.
    """

    CORRECTIVE = "CORRECTIVE"
    PREVENTIVE = "PREVENTIVE"


# Finding codes, in the order they are promoted to ``RCAReport.status``.
_STATUS_PRECEDENCE = (
    "INSUFFICIENT_5_WHYS_DEPTH",
    "MISSING_ACTION_ITEMS",
    "MISSING_TIMELINE",
    "CAPA_MISSING_OWNER_OR_DUE_DATE",
    "RULE_VIOLATION_ASSESSMENT_MISSING",
    "RCA_PAST_DUE",
)

# Findings that are recorded but never invalidate the RCA.
_ADVISORY_CODES = frozenset({"TERMINAL_BLAME_ATTRIBUTION"})

_SUCCESS_STATUS = "RCA_GENERATED_SUCCESS"


def _require_sequence(value: Any, label: str) -> Sequence[Any]:
    """Reject a bare string or bytes handed in where a sequence was expected.

    ``str`` satisfies ``Sequence`` and iterates by character, so a caller who
    passes ``five_whys="Human error"`` would otherwise get an eleven-level
    "analysis" of single letters that clears the depth gate.
    """
    if isinstance(value, (str, bytes)):
        raise ValueError(
            f"{label} must be a sequence of items, not a bare string; a string "
            f"iterates by character and would fabricate the item count."
        )
    try:
        return list(value)
    except TypeError:
        raise ValueError(f"{label} must be iterable, got {type(value).__name__}.") from None


def _require_text(value: Any, label: str) -> str:
    """Return ``value`` stripped, raising if it is not a non-blank string."""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string, got {type(value).__name__}.")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{label} must not be blank.")
    return cleaned


def _single_line(text: str) -> str:
    """Collapse internal whitespace so a value cannot break Markdown structure.

    Free-text fields are rendered into single-line Markdown bullets and header
    lines; an embedded newline would silently split one entry into two.
    """
    return _WHITESPACE_RUN.sub(" ", text).strip()


def _require_amount(value: Any, label: str) -> float:
    """Validate a non-negative, finite USD magnitude."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number, got {type(value).__name__}.")
    amount = float(value)
    if not math.isfinite(amount):
        raise ValueError(f"{label} must be finite, got {value!r}.")
    if amount < 0.0:
        raise ValueError(
            f"{label} is a magnitude and must be >= 0 (a 25,000 USD loss is "
            f"25000.0, not -25000.0); got {amount!r}."
        )
    return amount


def _require_utc(value: Any, label: str) -> datetime:
    """Validate a timezone-aware datetime and normalise it to UTC.

    Naive datetimes are rejected rather than assumed to be UTC: assuming is how
    a timeline ends up ordered by clock offset instead of by causality.
    """
    if not isinstance(value, datetime):
        raise ValueError(f"{label} must be a datetime, got {type(value).__name__}.")
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(
            f"{label} must be timezone-aware; naive datetimes are rejected "
            f"rather than assumed to be UTC."
        )
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class TimelineEvent:
    """One entry in the incident chronology.

    Args:
        timestamp: Timezone-aware instant, normalised to UTC.
        description: What happened. Rendered as a single line.
        source: The clock the timestamp came from (host, venue, broker log).
            Recorded so a reader can see which clocks the ordering depends on.
    """

    timestamp: datetime
    description: str
    source: str = "UNSPECIFIED"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "timestamp", _require_utc(self.timestamp, "TimelineEvent.timestamp")
        )
        object.__setattr__(
            self,
            "description",
            _single_line(_require_text(self.description, "TimelineEvent.description")),
        )
        object.__setattr__(
            self, "source", _single_line(_require_text(self.source, "TimelineEvent.source"))
        )


@dataclass(frozen=True)
class CapaItem:
    """A Corrective or Preventive Action.

    ``owner`` and ``due_date`` are optional at construction so an incomplete
    CAPA item can be *reported* as incomplete rather than being impossible to
    represent. The engine raises a ``CAPA_MISSING_OWNER_OR_DUE_DATE`` finding
    for any item missing either.
    """

    description: str
    owner: Optional[str] = None
    due_date: Optional[datetime] = None
    capa_type: CapaType = CapaType.CORRECTIVE

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "description",
            _single_line(_require_text(self.description, "CapaItem.description")),
        )
        if self.owner is not None:
            object.__setattr__(
                self, "owner", _single_line(_require_text(self.owner, "CapaItem.owner"))
            )
        if self.due_date is not None:
            object.__setattr__(self, "due_date", _require_utc(self.due_date, "CapaItem.due_date"))
        if not isinstance(self.capa_type, CapaType):
            raise ValueError(
                f"CapaItem.capa_type must be a CapaType, got {type(self.capa_type).__name__}."
            )

    @property
    def is_assigned(self) -> bool:
        """True when the item has both a named owner and a due date."""
        return self.owner is not None and self.due_date is not None


@dataclass(frozen=True)
class BreachIncidentSpec:
    """The raw incident record fed to the generator.

    Args:
        incident_id: Firm-internal identifier, e.g. ``INC-2026-001``.
        strategy_id: Strategy or desk the breach originated from.
        breach_type: The firm's own taxonomy label, e.g.
            ``POSITION_LIMIT_EXCEEDED``. Free text: taxonomies differ between
            firms and no standard one exists, so it is validated as non-blank
            and not against a fixed list.
        severity: A :class:`Severity`.
        detected_at: When responsible personnel first had a reasonable basis to
            conclude the breach had occurred. Timezone-aware.
        contained_at: When the breach stopped growing (kill switch engaged,
            positions flattened, algorithm disabled). Timezone-aware, and not
            before ``detected_at``.
        financial_loss_usd: Realised P&L impact, as a non-negative magnitude.
        unauthorized_turnover_usd: Notional traded outside the mandate, as a
            non-negative magnitude. ``0.0`` when the breach was a limit
            excursion with no unauthorised trading.
        five_whys: Ordered drill-down, root cause last.
        timeline_events: Chronology; the order of the input does not matter,
            the engine sorts by UTC timestamp.
        action_items: CAPA items.
        possible_rule_violation: The author's explicit determination of whether
            the incident may constitute a violation of an applicable rule.
            ``None`` means "not yet assessed" and produces a finding -- this is
            a determination that must be made deliberately, not defaulted. See
            ``references/standards.md`` for why it matters and by when.
        rca_due_by: Optional deadline the firm has set for this RCA under its
            own policy or its applicable reporting regime. The engine does not
            know your regime and will not invent a deadline.
    """

    incident_id: str
    strategy_id: str
    breach_type: str
    severity: Severity
    detected_at: datetime
    contained_at: datetime
    financial_loss_usd: float
    unauthorized_turnover_usd: float
    five_whys: Sequence[str]
    timeline_events: Sequence[TimelineEvent]
    action_items: Sequence[CapaItem]
    possible_rule_violation: Optional[bool] = None
    rca_due_by: Optional[datetime] = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "incident_id", _single_line(_require_text(self.incident_id, "incident_id"))
        )
        object.__setattr__(
            self, "strategy_id", _single_line(_require_text(self.strategy_id, "strategy_id"))
        )
        object.__setattr__(
            self, "breach_type", _single_line(_require_text(self.breach_type, "breach_type"))
        )

        if not isinstance(self.severity, Severity):
            raise ValueError(
                f"severity must be a Severity enum member, got {type(self.severity).__name__}."
            )

        detected = _require_utc(self.detected_at, "detected_at")
        contained = _require_utc(self.contained_at, "contained_at")
        if contained < detected:
            raise ValueError(
                f"contained_at ({contained.isoformat()}) precedes detected_at "
                f"({detected.isoformat()}); an incident cannot be contained "
                f"before it is detected."
            )
        object.__setattr__(self, "detected_at", detected)
        object.__setattr__(self, "contained_at", contained)

        object.__setattr__(
            self,
            "financial_loss_usd",
            _require_amount(self.financial_loss_usd, "financial_loss_usd"),
        )
        object.__setattr__(
            self,
            "unauthorized_turnover_usd",
            _require_amount(self.unauthorized_turnover_usd, "unauthorized_turnover_usd"),
        )

        whys = [
            _single_line(_require_text(w, f"five_whys[{i}]"))
            for i, w in enumerate(_require_sequence(self.five_whys, "five_whys"))
        ]
        object.__setattr__(self, "five_whys", tuple(whys))

        object.__setattr__(
            self, "timeline_events", _require_sequence(self.timeline_events, "timeline_events")
        )
        for i, event in enumerate(self.timeline_events):
            if not isinstance(event, TimelineEvent):
                raise ValueError(
                    f"timeline_events[{i}] must be a TimelineEvent, "
                    f"got {type(event).__name__}."
                )
        object.__setattr__(self, "timeline_events", tuple(self.timeline_events))

        object.__setattr__(
            self, "action_items", _require_sequence(self.action_items, "action_items")
        )
        for i, item in enumerate(self.action_items):
            if not isinstance(item, CapaItem):
                raise ValueError(
                    f"action_items[{i}] must be a CapaItem, got {type(item).__name__}."
                )
        object.__setattr__(self, "action_items", tuple(self.action_items))

        if self.possible_rule_violation is not None and not isinstance(
            self.possible_rule_violation, bool
        ):
            raise ValueError(
                f"possible_rule_violation must be a bool or None, got "
                f"{type(self.possible_rule_violation).__name__}."
            )

        if self.rca_due_by is not None:
            object.__setattr__(self, "rca_due_by", _require_utc(self.rca_due_by, "rca_due_by"))

    @property
    def containment_seconds(self) -> float:
        """Seconds from detection to containment."""
        return (self.contained_at - self.detected_at).total_seconds()


@dataclass
class RCAReport:
    """Output of :meth:`BreachRcaGenerator.generate_rca_report`.

    ``status`` is the highest-precedence blocking finding, or
    ``RCA_GENERATED_SUCCESS``. ``validation_findings`` lists every finding,
    advisory ones included, so a caller must not infer "no other problems"
    from ``status`` alone.
    """

    incident_id: str
    strategy_id: str
    severity: str
    financial_loss_usd: float
    unauthorized_turnover_usd: float
    containment_seconds: float
    five_whys_depth: int
    action_item_count: int
    unassigned_action_items: int
    has_preventive_action: bool
    timeline_clock_sources: List[str]
    is_valid_rca: bool
    status: str
    validation_findings: List[str] = field(default_factory=list)
    audit_notes: str = ""
    markdown_document: str = ""
    json_payload: str = ""


class BreachRcaGenerator:
    """Validates a breach incident record and renders it as an RCA document.

    Args:
        min_five_whys_depth: Minimum drill-down levels before the RCA counts as
            complete. The default of 3 is a house convention, not a published
            standard; see ``references/standards.md``.
        require_preventive_action: When True, an RCA whose CAPA items are all
            ``CORRECTIVE`` is still valid but the absence of a preventive
            action is logged. Kept advisory deliberately: some incidents
            genuinely have no preventive action beyond the fix.
    """

    def __init__(
        self, min_five_whys_depth: int = 3, require_preventive_action: bool = False
    ) -> None:
        if isinstance(min_five_whys_depth, bool) or not isinstance(min_five_whys_depth, int):
            raise ValueError("min_five_whys_depth must be an int.")
        if min_five_whys_depth < 1:
            raise ValueError(f"min_five_whys_depth must be >= 1, got {min_five_whys_depth}.")
        self.min_five_whys_depth = min_five_whys_depth
        self.require_preventive_action = bool(require_preventive_action)

    # ----------------------------------------------------------------- audit

    def _collect_findings(self, spec: BreachIncidentSpec, generated_at: datetime) -> List[str]:
        """Apply the completeness gates.

        Order here does not matter; ``status`` precedence is applied separately
        in :meth:`_status_from`.
        """
        findings: List[str] = []

        if len(spec.five_whys) < self.min_five_whys_depth:
            findings.append("INSUFFICIENT_5_WHYS_DEPTH")

        if not spec.action_items:
            findings.append("MISSING_ACTION_ITEMS")
        elif any(not item.is_assigned for item in spec.action_items):
            findings.append("CAPA_MISSING_OWNER_OR_DUE_DATE")

        if not spec.timeline_events:
            findings.append("MISSING_TIMELINE")

        if spec.possible_rule_violation is None:
            findings.append("RULE_VIOLATION_ASSESSMENT_MISSING")

        if spec.rca_due_by is not None and generated_at > spec.rca_due_by:
            findings.append("RCA_PAST_DUE")

        if spec.five_whys and self._looks_like_terminal_blame(spec.five_whys[-1]):
            findings.append("TERMINAL_BLAME_ATTRIBUTION")

        return findings

    @staticmethod
    def _looks_like_terminal_blame(final_why: str) -> bool:
        """Advisory heuristic: does the root cause reduce to a person?

        Crude by design and documented as such. False positives and false
        negatives are both expected; this flags a step for human review, it
        does not adjudicate one.
        """
        lowered = final_why.lower()
        return any(phrase in lowered for phrase in _TERMINAL_BLAME_PHRASES)

    @staticmethod
    def _status_from(findings: Sequence[str]) -> str:
        """Highest-precedence blocking finding, or the success status."""
        for code in _STATUS_PRECEDENCE:
            if code in findings:
                return code
        return _SUCCESS_STATUS

    # ---------------------------------------------------------------- render

    @staticmethod
    def _sorted_timeline(spec: BreachIncidentSpec) -> List[TimelineEvent]:
        """Chronological order by UTC timestamp.

        ``sorted`` is stable, so events sharing a timestamp keep the order the
        caller supplied -- which is the only ordering information available for
        events a clock cannot separate.
        """
        return sorted(spec.timeline_events, key=lambda e: e.timestamp)

    def _build_markdown(
        self,
        spec: BreachIncidentSpec,
        timeline: Sequence[TimelineEvent],
        findings: Sequence[str],
        generated_at: datetime,
    ) -> str:
        lines: List[str] = [
            f"# ROOT CAUSE ANALYSIS (RCA) REPORT: {spec.incident_id}",
            "",
            f"- **Strategy**: `{spec.strategy_id}`",
            f"- **Breach type**: `{spec.breach_type}`",
            f"- **Severity**: `{spec.severity.value}`",
            f"- **Detected (UTC)**: {spec.detected_at.isoformat()}",
            f"- **Contained (UTC)**: {spec.contained_at.isoformat()}"
            f" ({spec.containment_seconds:,.0f}s after detection)",
            f"- **Report generated (UTC)**: {generated_at.isoformat()}",
            "",
            "## 1. Financial Impact",
            "",
            f"- Realised loss: `${spec.financial_loss_usd:,.2f}`",
            f"- Unauthorised turnover: `${spec.unauthorized_turnover_usd:,.2f}`",
            "",
            "> Figures are caller-supplied magnitudes and must be reconciled "
            "against the books.",
            "",
            "## 2. Chronological Timeline (UTC)",
            "",
        ]

        if timeline:
            for event in timeline:
                lines.append(
                    f"- **{event.timestamp.isoformat()}** [`{event.source}`]: "
                    f"{event.description}"
                )
        else:
            lines.append("- _No timeline events recorded._")

        lines.extend(["", "## 3. 5-Whys Analysis", ""])
        if spec.five_whys:
            for idx, why in enumerate(spec.five_whys, 1):
                lines.append(f"{idx}. **Why?** {why}")
        else:
            lines.append("_No 5-Whys analysis recorded._")

        lines.extend(["", "## 4. Corrective and Preventive Actions (CAPA)", ""])
        if spec.action_items:
            for item in spec.action_items:
                owner = item.owner if item.owner else "**UNASSIGNED**"
                due = item.due_date.date().isoformat() if item.due_date else "**NO DUE DATE**"
                lines.append(
                    f"- [ ] ({item.capa_type.value}) {item.description} "
                    f"-- owner: {owner}, due: {due}"
                )
        else:
            lines.append("- _No CAPA action items recorded._")

        lines.extend(["", "## 5. Rule-Violation Assessment", ""])
        if spec.possible_rule_violation is None:
            lines.append(
                "- **NOT ASSESSED.** Whether this incident may constitute a rule "
                "violation has not been determined. That determination can start "
                "a reporting clock; see `references/standards.md`."
            )
        elif spec.possible_rule_violation:
            lines.append(
                "- **Possible rule violation identified.** Escalate to Compliance "
                "immediately -- an internal conclusion that a violation occurred "
                "starts a reporting clock in some jurisdictions (see "
                "`references/standards.md`)."
            )
        else:
            lines.append("- Assessed: no rule violation identified by the RCA author.")

        lines.extend(["", "## 6. Audit Findings", ""])
        if findings:
            for code in findings:
                marker = "ADVISORY" if code in _ADVISORY_CODES else "BLOCKING"
                lines.append(f"- `{code}` ({marker})")
        else:
            lines.append("- None. All completeness gates passed.")

        return "\n".join(lines)

    @staticmethod
    def _build_payload(
        spec: BreachIncidentSpec,
        timeline: Sequence[TimelineEvent],
        findings: Sequence[str],
        status: str,
        generated_at: datetime,
    ) -> Dict[str, Any]:
        return {
            "incident_id": spec.incident_id,
            "strategy_id": spec.strategy_id,
            "breach_type": spec.breach_type,
            "severity": spec.severity.value,
            "detected_at_utc": spec.detected_at.isoformat(),
            "contained_at_utc": spec.contained_at.isoformat(),
            "generated_at_utc": generated_at.isoformat(),
            "containment_seconds": spec.containment_seconds,
            "financial_loss_usd": spec.financial_loss_usd,
            "unauthorized_turnover_usd": spec.unauthorized_turnover_usd,
            "five_whys": list(spec.five_whys),
            "timeline": [
                {
                    "timestamp_utc": e.timestamp.isoformat(),
                    "source": e.source,
                    "description": e.description,
                }
                for e in timeline
            ],
            "action_items": [
                {
                    "description": item.description,
                    "owner": item.owner,
                    "due_date": item.due_date.date().isoformat() if item.due_date else None,
                    "capa_type": item.capa_type.value,
                }
                for item in spec.action_items
            ],
            "possible_rule_violation": spec.possible_rule_violation,
            "rca_due_by_utc": spec.rca_due_by.isoformat() if spec.rca_due_by else None,
            "status": status,
            "validation_findings": list(findings),
        }

    # ---------------------------------------------------------------- public

    def generate_rca_report(
        self, spec: BreachIncidentSpec, generated_at: datetime
    ) -> RCAReport:
        """Audit a breach incident record and render the RCA.

        Args:
            spec: The incident record. Structural defects have already raised
                at construction time.
            generated_at: Timezone-aware instant the report is being produced.
                Supplied by the caller rather than read from the clock so the
                output is reproducible.

        Returns:
            An :class:`RCAReport`. The Markdown document and JSON payload are
            always rendered, including when the RCA is incomplete -- an
            incomplete post-mortem still needs to be readable, and its gaps are
            listed in section 6 of the document.

        Raises:
            ValueError: if ``spec`` is not a :class:`BreachIncidentSpec` or
                ``generated_at`` is not a timezone-aware datetime.
        """
        if not isinstance(spec, BreachIncidentSpec):
            raise ValueError(f"spec must be a BreachIncidentSpec, got {type(spec).__name__}.")
        generated_at = _require_utc(generated_at, "generated_at")

        findings = self._collect_findings(spec, generated_at)
        status = self._status_from(findings)
        blocking = [c for c in findings if c not in _ADVISORY_CODES]
        is_valid = not blocking

        timeline = self._sorted_timeline(spec)
        markdown = self._build_markdown(spec, timeline, findings, generated_at)
        payload = self._build_payload(spec, timeline, findings, status, generated_at)

        unassigned = sum(1 for item in spec.action_items if not item.is_assigned)
        has_preventive = any(item.capa_type is CapaType.PREVENTIVE for item in spec.action_items)

        if is_valid:
            notes = (
                f"RCA generated for {spec.incident_id}: "
                f"5-Whys depth={len(spec.five_whys)}, CAPA items={len(spec.action_items)}, "
                f"timeline events={len(timeline)}."
            )
            logger.info(notes)
        else:
            notes = (
                f"RCA incomplete for {spec.incident_id}: "
                f"blocking findings={','.join(blocking)}."
            )
            logger.warning(notes)

        if findings and not blocking:
            logger.info(
                "RCA %s passed all blocking gates with advisory findings: %s",
                spec.incident_id,
                ",".join(findings),
            )
        if self.require_preventive_action and not has_preventive and spec.action_items:
            logger.info(
                "RCA %s has no PREVENTIVE CAPA item; every action addresses this "
                "instance only.",
                spec.incident_id,
            )

        return RCAReport(
            incident_id=spec.incident_id,
            strategy_id=spec.strategy_id,
            severity=spec.severity.value,
            financial_loss_usd=spec.financial_loss_usd,
            unauthorized_turnover_usd=spec.unauthorized_turnover_usd,
            containment_seconds=spec.containment_seconds,
            five_whys_depth=len(spec.five_whys),
            action_item_count=len(spec.action_items),
            unassigned_action_items=unassigned,
            has_preventive_action=has_preventive,
            timeline_clock_sources=sorted({e.source for e in timeline}),
            is_valid_rca=is_valid,
            status=status,
            validation_findings=findings,
            audit_notes=notes,
            markdown_document=markdown,
            json_payload=json.dumps(payload, indent=2, sort_keys=True),
        )
