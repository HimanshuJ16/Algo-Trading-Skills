"""
regulatory-change-monitoring-service-integration: ingest regulatory change
feeds, resolve the deadline that actually binds, and route open action items to
compliance and engineering owners.

Purpose
-------
Answer one question on every polling cycle: *which published rule changes still
require work from us, and how long is left?* The engine filters a feed to the
regulators (and optionally the subject areas) the firm is exposed to, resolves
each update's binding deadline, flags open items that are urgent or already
overdue, and produces an auditable report. It does not fetch feeds, parse RSS,
or interpret legal text -- it is the deterministic assessment stage that sits
behind whatever ingestion adapter the firm runs.

Effective date vs compliance date (the reason this engine has two date fields)
------------------------------------------------------------------------------
For most rulemakings these are *different dates*, and the one that drives
implementation work is the compliance date. The SEC's T+1 settlement rulemaking
is the canonical example:

    Federal Register document 2023-03566, "Shortening the Securities
    Transaction Settlement Cycle"
      publication_date : 2023-03-06
      effective_on     : 2023-05-05     <- the only date in the structured feed
      dates (free text): "Effective date: May 5, 2023."
      compliance date  : 2024-05-28     <- appears only in the release body
                                           (SEC press release 2023-29: "The
                                           compliance date for the final rules
                                           is May 28, 2024.")

An engine keyed on the feed's structured effective date would have raised a
30-day alarm in April 2023 and been silent through the 15-month window in which
the work actually had to happen. ``RegulatoryUpdate.compliance_date`` is
therefore the deadline when present; ``effective_date`` is the documented
fallback, and every assessment records which of the two was used
(``deadline_basis``) so the report is auditable.

The EU has the same split under different names: Directive 2014/65/EU (MiFID II)
entered into force 2014-07-02 and applied from 2018-01-03 -- a date that was
itself moved by a later amending directive, (EU) 2016/1034. Deadlines move;
re-ingesting a revised update must replace the prior record, not duplicate it
(see ``process_updates`` on duplicate ``update_id``).

Regulatory position (read ``references/standards.md`` before citing anything)
-----------------------------------------------------------------------------
No regulator mandates a regulatory-change monitoring *feed*, a polling
frequency, or an alert window. The 30-day urgency window and the 24-hour poll
cadence in this skill are house defaults, not rules. What is mandatory is the
supervisory obligation the monitoring supports: FINRA Rule 3110(a)/(b)(1)
(supervisory system and written procedures "reasonably designed to achieve
compliance with applicable securities laws and regulations, and with applicable
FINRA rules") and, for EU/EEA firms engaged in algorithmic trading, the annual
self-assessment and validation of "overall compliance with Article 17 of
Directive 2014/65/EU" under RTS 6 (Commission Delegated Regulation (EU)
2017/589) Article 9.

Limitations (documented, deliberate)
------------------------------------
- **Calendar days, not business days.** Deadlines are compared as naive
  calendar dates. No holiday calendar, no jurisdiction-local timezone. A 30-day
  window can contain 19 or 23 working days depending on where it falls.
- **No legal interpretation.** ``severity``, ``action_required`` and
  ``impacted_subdomains`` are supplied by the caller's classification stage
  (vendor taxonomy, analyst triage, or model). This engine trusts them and only
  validates their shape.
- **Single deadline per update.** Phased rulemakings with several compliance
  dates must be ingested as one update per phase; there is no phase model.
- **No supersession graph.** A withdrawn or replaced rule is handled by
  re-ingesting the corrected record, not by linking versions.
- **Assessment is not remediation.** ``STATUS_COMPLIANT`` reflects the caller
  setting ``remediation_complete``; nothing here verifies that the work was done.
"""
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Iterable, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

DATE_FORMAT = "%Y-%m-%d"

VALID_SEVERITIES: Tuple[str, ...] = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
URGENT_SEVERITIES = frozenset({"CRITICAL", "HIGH"})

STATUS_ACTION_REQUIRED = "ACTION_REQUIRED"
STATUS_OVERDUE = "OVERDUE"
STATUS_MONITORING = "MONITORING"
STATUS_COMPLIANT = "COMPLIANT"

OVERALL_ACTION_REQUIRED = "ACTION_REQUIRED"
OVERALL_MONITORING_ONLY = "MONITORING_ONLY"
OVERALL_NO_UPDATES = "NO_UPDATES"

BASIS_COMPLIANCE_DATE = "COMPLIANCE_DATE"
BASIS_EFFECTIVE_DATE = "EFFECTIVE_DATE"

DEFAULT_MONITORED_REGULATORS: Tuple[str, ...] = ("SEC", "FCA", "SEBI", "ESMA", "MAS")
DEFAULT_URGENT_ACTION_WINDOW_DAYS = 30


@dataclass
class ComplianceResult:
    """Legacy ComplianceResult for backward compatibility."""
    is_compliant: bool
    reason: str


@dataclass
class RegulatoryUpdate:
    """One published regulatory change, already classified upstream.

    ``effective_date`` is when the instrument takes legal effect;
    ``compliance_date`` is when the obligations it imposes must be met. When
    they differ, the compliance date is the deadline that drives engineering
    work -- see the module docstring for the T+1 example. Leave
    ``compliance_date`` as ``None`` when the rulemaking sets no separate date;
    the engine then falls back to ``effective_date`` and records the fallback.
    """
    update_id: str
    regulator: str                       # e.g. 'SEC', 'FCA', 'SEBI', 'ESMA', 'MAS'
    title: str
    effective_date: str                  # ISO calendar date, 'YYYY-MM-DD'
    impacted_subdomains: List[str]       # e.g. ['SETTLEMENT', 'TICK_SIZE']
    severity: str                        # 'CRITICAL', 'HIGH', 'MEDIUM', 'LOW'
    action_required: bool = True
    summary: str = ""
    compliance_date: Optional[str] = None  # ISO calendar date, if distinct
    remediation_complete: bool = False     # set once the work has been signed off
    source_url: str = ""


@dataclass
class ChangeImpactAssessment:
    """Deadline and urgency assessment for one retained update."""
    update_id: str
    regulator: str
    severity: str
    days_until_effective: int            # days until the *binding* deadline
    requires_immediate_action: bool
    status: str                          # ACTION_REQUIRED / OVERDUE / MONITORING / COMPLIANT
    deadline_iso: str = ""
    deadline_basis: str = BASIS_EFFECTIVE_DATE
    is_overdue: bool = False
    title: str = ""
    impacted_subdomains: List[str] = field(default_factory=list)


@dataclass
class RegulatoryChangeReport:
    """Auditable output of one assessment cycle.

    ``action_required_count`` counts *open* items only: updates flagged
    ``action_required`` that have not been marked ``remediation_complete``.
    Filtered counts are reported rather than discarded so that a silent drop is
    never mistaken for an empty feed.
    """
    total_updates: int
    critical_count: int
    action_required_count: int
    assessments: List[ChangeImpactAssessment]
    overall_status: str                  # ACTION_REQUIRED / MONITORING_ONLY / NO_UPDATES
    audit_notes: str
    immediate_action_count: int = 0
    overdue_count: int = 0
    compliant_count: int = 0
    filtered_regulator_count: int = 0
    filtered_subdomain_count: int = 0
    evaluation_date_iso: str = ""
    earliest_deadline_days: Optional[int] = None
    filtered_regulators: List[str] = field(default_factory=list)


class RegulatoryChangeMonitoringServiceIntegrationEngine:
    """Deterministic assessment stage for a regulatory change monitoring feed.

    Args:
        monitored_regulators: Authorities the firm is exposed to. Matched
            case-insensitively after stripping. ``None`` uses the five default
            authorities; an explicitly empty sequence raises, because "monitor
            nothing" is never a safe silent default.
        urgent_action_window_days: Days-to-deadline at or below which an open
            CRITICAL/HIGH item is flagged for immediate action. A house
            escalation default, not a regulatory threshold.
        monitored_subdomains: Optional subject-area filter (e.g.
            ``{'SETTLEMENT', 'SHORT_SELLING'}``) used to suppress feed noise.
            ``None`` retains every subject area. An update carrying *no*
            subdomains is retained even when this filter is active -- see
            ``_matches_subdomains``.
    """

    def __init__(
        self,
        monitored_regulators: Optional[Sequence[str]] = None,
        urgent_action_window_days: int = DEFAULT_URGENT_ACTION_WINDOW_DAYS,
        monitored_subdomains: Optional[Iterable[str]] = None,
    ) -> None:
        if monitored_regulators is None:
            regulators: List[str] = list(DEFAULT_MONITORED_REGULATORS)
        else:
            regulators = [str(r).strip() for r in monitored_regulators if str(r).strip()]
            if not regulators:
                raise ValueError(
                    "monitored_regulators is empty: pass None for the default "
                    "authority list, never an empty sequence -- an empty filter "
                    "would silently discard every update in the feed."
                )
        self.monitored_regulators: List[str] = regulators
        self._regulator_index: Set[str] = {r.upper() for r in regulators}

        if isinstance(urgent_action_window_days, bool) or not isinstance(urgent_action_window_days, int):
            raise ValueError("urgent_action_window_days must be an int")
        if urgent_action_window_days < 0:
            raise ValueError(
                f"urgent_action_window_days must be >= 0, got {urgent_action_window_days}"
            )
        self.urgent_action_window_days: int = urgent_action_window_days

        if monitored_subdomains is None:
            self.monitored_subdomains: Optional[Set[str]] = None
        else:
            normalised = {str(s).strip().upper() for s in monitored_subdomains if str(s).strip()}
            if not normalised:
                raise ValueError(
                    "monitored_subdomains is empty: pass None to retain every "
                    "subject area rather than an empty set."
                )
            self.monitored_subdomains = normalised

    # ------------------------------------------------------------------ #
    # Legacy API                                                         #
    # ------------------------------------------------------------------ #
    def check(self, data: dict) -> ComplianceResult:
        """Legacy check method retained for backward compatibility."""
        if data.get("valid"):
            return ComplianceResult(True, "Valid")
        return ComplianceResult(False, "Invalid")

    # ------------------------------------------------------------------ #
    # Internals                                                          #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse_date(value: Optional[str], field_name: str, update_id: str) -> date:
        """Parse an ISO calendar date, raising with enough context to fix the feed.

        A malformed date is a feed defect, not a value to guess at. Substituting
        a placeholder would fabricate a deadline and write it to the audit record.
        """
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"update '{update_id}': {field_name} is empty; an ISO date "
                f"'YYYY-MM-DD' is required"
            )
        try:
            return datetime.strptime(value.strip(), DATE_FORMAT).date()
        except ValueError as exc:
            raise ValueError(
                f"update '{update_id}': {field_name} {value!r} is not an ISO "
                f"calendar date 'YYYY-MM-DD' ({exc})"
            ) from exc

    @staticmethod
    def _normalise_severity(update: RegulatoryUpdate) -> str:
        """Upper-case and validate severity.

        Case matters: a feed emitting ``'critical'`` against a case-sensitive
        comparison is silently demoted out of the urgent band and out of
        ``critical_count``. Unknown labels raise rather than defaulting low.
        """
        raw = update.severity
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"update '{update.update_id}': severity is empty")
        severity = raw.strip().upper()
        if severity not in VALID_SEVERITIES:
            raise ValueError(
                f"update '{update.update_id}': unknown severity {raw!r}; "
                f"expected one of {', '.join(VALID_SEVERITIES)}"
            )
        return severity

    def _matches_subdomains(self, update: RegulatoryUpdate) -> bool:
        """Subject-area filter. Fails *open* for unclassified updates."""
        if self.monitored_subdomains is None:
            return True
        if not isinstance(update.impacted_subdomains, (list, tuple, set)):
            raise ValueError(
                f"update '{update.update_id}': impacted_subdomains must be a "
                f"sequence of strings"
            )
        subdomains = {str(s).strip().upper() for s in update.impacted_subdomains if str(s).strip()}
        if not subdomains:
            # An update nobody has classified yet is retained deliberately:
            # dropping it would hide an unclassified rule change behind a
            # noise filter.
            logger.warning(
                "Regulatory update '%s' (%s) carries no impacted_subdomains; "
                "retained despite the subdomain filter.",
                update.update_id, update.regulator,
            )
            return True
        return bool(subdomains & self.monitored_subdomains)

    # ------------------------------------------------------------------ #
    # Assessment                                                         #
    # ------------------------------------------------------------------ #
    def process_updates(
        self,
        updates: Sequence[RegulatoryUpdate],
        current_date_iso: Optional[str] = None,
    ) -> RegulatoryChangeReport:
        """Assess a batch of regulatory updates against an assessment date.

        Args:
            updates: Regulatory updates from one polling cycle. ``update_id``
                must be unique within the batch -- a feed that re-delivers a
                revised record must replace it upstream, because counting the
                same rule twice inflates every figure in the report.
            current_date_iso: Assessment date as 'YYYY-MM-DD'. Defaults to the
                current UTC date. Pass it explicitly for reproducible audits.

        Returns:
            A ``RegulatoryChangeReport``. Updates filtered out by regulator or
            subject area are counted in the report, never silently dropped.

        Raises:
            ValueError: on a malformed date, unknown severity, blank
                ``update_id``, or a duplicate ``update_id`` within the batch.
        """
        if updates is None or isinstance(updates, (str, bytes)):
            raise ValueError("updates must be a sequence of RegulatoryUpdate objects")

        if current_date_iso is None:
            current_dt = datetime.now(timezone.utc).date()
            logger.info(
                "No current_date_iso supplied; assessing against today's UTC "
                "date %s.", current_dt.isoformat(),
            )
        else:
            current_dt = self._parse_date(current_date_iso, "current_date_iso", "<batch>")

        assessments: List[ChangeImpactAssessment] = []
        seen_ids: Set[str] = set()
        filtered_regulators: List[str] = []
        filtered_regulator_count = 0
        filtered_subdomain_count = 0
        critical = 0
        open_actions = 0
        immediate = 0
        overdue_count = 0
        compliant_count = 0

        for up in updates:
            if not isinstance(up, RegulatoryUpdate):
                raise ValueError(f"expected RegulatoryUpdate, got {type(up).__name__}")

            update_id = str(up.update_id).strip()
            if not update_id:
                raise ValueError("every regulatory update requires a non-empty update_id")
            if update_id in seen_ids:
                raise ValueError(
                    f"duplicate update_id '{update_id}' in one batch: deduplicate "
                    f"or replace revised records upstream -- double-counting a "
                    f"rule change corrupts every count in the report."
                )
            seen_ids.add(update_id)

            regulator = str(up.regulator).strip()
            if not regulator:
                raise ValueError(f"update '{update_id}': regulator is empty")
            if regulator.upper() not in self._regulator_index:
                filtered_regulator_count += 1
                if regulator not in filtered_regulators:
                    filtered_regulators.append(regulator)
                continue

            if not self._matches_subdomains(up):
                filtered_subdomain_count += 1
                continue

            severity = self._normalise_severity(up)

            # Resolve the binding deadline. effective_date is validated even
            # when a compliance_date overrides it, so a broken feed record
            # cannot hide behind the field that happens to be unused.
            effective_dt = self._parse_date(up.effective_date, "effective_date", update_id)
            if up.compliance_date is not None:
                deadline_dt = self._parse_date(up.compliance_date, "compliance_date", update_id)
                basis = BASIS_COMPLIANCE_DATE
                if deadline_dt < effective_dt:
                    logger.warning(
                        "Regulatory update '%s' (%s): compliance_date %s precedes "
                        "effective_date %s -- verify the source record.",
                        update_id, regulator, deadline_dt.isoformat(),
                        effective_dt.isoformat(),
                    )
            else:
                deadline_dt = effective_dt
                basis = BASIS_EFFECTIVE_DATE

            days_until = (deadline_dt - current_dt).days
            is_open = bool(up.action_required) and not bool(up.remediation_complete)
            is_overdue = is_open and days_until < 0

            # An open item past its deadline is a live breach, so it escalates
            # whatever its severity label says. Everything else escalates only
            # inside the configured window and only at CRITICAL/HIGH.
            urgent = is_open and (
                is_overdue
                or (severity in URGENT_SEVERITIES and days_until <= self.urgent_action_window_days)
            )

            if severity == "CRITICAL":
                critical += 1

            if not up.action_required:
                status = STATUS_MONITORING
            elif up.remediation_complete:
                status = STATUS_COMPLIANT
                compliant_count += 1
            elif is_overdue:
                status = STATUS_OVERDUE
                overdue_count += 1
                open_actions += 1
            else:
                status = STATUS_ACTION_REQUIRED
                open_actions += 1

            if urgent:
                immediate += 1

            assessments.append(ChangeImpactAssessment(
                update_id=update_id,
                regulator=regulator,
                severity=severity,
                days_until_effective=days_until,
                requires_immediate_action=urgent,
                status=status,
                deadline_iso=deadline_dt.isoformat(),
                deadline_basis=basis,
                is_overdue=is_overdue,
                title=str(up.title),
                impacted_subdomains=list(up.impacted_subdomains or []),
            ))

        # Deterministic triage order: nearest deadline first, update_id as the
        # tie-break so two runs over the same batch produce identical reports.
        assessments.sort(key=lambda a: (a.days_until_effective, a.update_id))

        total = len(assessments)
        if open_actions > 0:
            overall = OVERALL_ACTION_REQUIRED
        elif total > 0:
            overall = OVERALL_MONITORING_ONLY
        else:
            overall = OVERALL_NO_UPDATES

        earliest = assessments[0].days_until_effective if assessments else None

        notes = (
            f"REGULATORY CHANGE MONITOR [{overall}] as of {current_dt.isoformat()}: "
            f"Retained = {total}, Critical = {critical}, Open actions = {open_actions}, "
            f"Overdue = {overdue_count}, Immediate = {immediate}, "
            f"Closed = {compliant_count}, "
            f"Filtered (regulator) = {filtered_regulator_count}, "
            f"Filtered (subdomain) = {filtered_subdomain_count}."
        )

        if overall == OVERALL_ACTION_REQUIRED:
            logger.warning(notes)
        else:
            logger.info(notes)
        if overdue_count:
            logger.error(
                "%d regulatory update(s) are past their binding deadline with "
                "remediation still open.", overdue_count,
            )
        if filtered_regulator_count:
            logger.info(
                "Filtered %d update(s) from unmonitored authorities: %s.",
                filtered_regulator_count, ", ".join(filtered_regulators),
            )

        return RegulatoryChangeReport(
            total_updates=total,
            critical_count=critical,
            action_required_count=open_actions,
            assessments=assessments,
            overall_status=overall,
            audit_notes=notes,
            immediate_action_count=immediate,
            overdue_count=overdue_count,
            compliant_count=compliant_count,
            filtered_regulator_count=filtered_regulator_count,
            filtered_subdomain_count=filtered_subdomain_count,
            evaluation_date_iso=current_dt.isoformat(),
            earliest_deadline_days=earliest,
            filtered_regulators=filtered_regulators,
        )
