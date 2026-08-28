"""Multi-jurisdiction record retention floors for trading records.

What this module does
---------------------
Given a record, the record class it belongs to, and every jurisdiction whose
rules bind it, this module computes the **earliest date on which retention of
that record is no longer compelled by the built-in rule table** — taking the
*latest* such date across all applicable jurisdictions — and reports whether the
firm's own configured retention duration meets the regulatory floor.

What this module does NOT do
----------------------------
1. **It never authorises a deletion.** ``RetentionStatus.ELIGIBLE_FOR_REVIEW``
   means "no rule in the table still compels retention", not "delete this".
   Litigation holds, regulatory investigations, tax law, AML law, contractual
   commitments, and internal policy all extend retention independently of the
   securities rules modelled here.
2. **It is not a legal determination.** ``DEFAULT_RETENTION_RULES`` encodes
   generally applicable floors for the firm types named in each rule's
   ``citation``. Which rule applies to *your* firm depends on your licence,
   membership, activity, and record type. Confirm every row with counsel before
   it drives a purge.
3. **It does not model every obligation in any regime.** Only the classes in
   ``RecordClass`` are modelled, at the granularity stated in each citation.
   Where a regime sets different periods for sub-classes not modelled here,
   supply your own rules via the ``rules`` constructor argument.

Directional safety
------------------
Every design choice resolves toward *retaining longer*: an unknown jurisdiction
yields ``INDETERMINATE`` rather than a purge date, a multi-jurisdiction record
takes the latest floor rather than the first match, and a missing clock-start
date is an error rather than an assumed value.

Sources for every figure in ``DEFAULT_RETENTION_RULES`` are in
``references/standards.md``, verified August 2026.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence as AbcSequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "RetentionError",
    "RecordClass",
    "ClockStart",
    "RetentionStatus",
    "RetentionRule",
    "RetentionRecord",
    "RetentionAssessment",
    "RetentionComplianceReport",
    "RecordRetentionPeriodsByJurisdictionEngine",
    "DEFAULT_RETENTION_RULES",
]


class RetentionError(ValueError):
    """Raised when input is structurally unusable for a retention decision."""


class RecordClass(Enum):
    """Classes of record with materially different retention floors.

    The distinction matters: SEC Rule 17a-4 preserves blotters and ledgers for
    six years but business communications for three, so a single per-country
    number is wrong for at least one of them.
    """

    TRADE_AND_LEDGER = "TRADE_AND_LEDGER"
    ORDER_AUDIT_TRAIL = "ORDER_AUDIT_TRAIL"
    COMMUNICATION = "COMMUNICATION"
    CLIENT_ACCOUNT = "CLIENT_ACCOUNT"
    OTHER = "OTHER"


class ClockStart(Enum):
    """What event the retention period is measured from.

    Getting this wrong is a silent multi-year error: SEC Rule 17a-4(e)(5) runs
    six years from *account closure*, not from record creation, so a record
    created in 2015 for an account closed in 2025 is retained until 2031.
    """

    RECORD_CREATION = "RECORD_CREATION"
    ACCOUNT_CLOSURE = "ACCOUNT_CLOSURE"


class RetentionStatus(Enum):
    """Outcome of a per-record assessment."""

    #: At least one applicable rule still compels retention.
    RETAIN = "RETAIN"
    #: No rule in the table still compels retention. Not a deletion approval.
    ELIGIBLE_FOR_REVIEW = "ELIGIBLE_FOR_REVIEW"
    #: A hold is asserted; retention is compelled regardless of any rule.
    LEGAL_HOLD = "LEGAL_HOLD"
    #: The floor could not be determined. Retain and investigate.
    INDETERMINATE = "INDETERMINATE"


@dataclass(frozen=True)
class RetentionRule:
    """One retention floor, for one record class, in one jurisdiction.

    Attributes:
        jurisdiction: Upper-case jurisdiction key (e.g. ``"US"``).
        record_class: Class of record the floor applies to.
        min_years: Minimum whole years the record must be retained.
        clock_start: Event the period runs from.
        authority: Regulator or statute-maker.
        citation: The specific instrument and provision setting ``min_years``.
        accessible_years: Where the regime requires an initial sub-period in a
            readily accessible place, its length in years; otherwise ``None``.
        extension_years: Where a competent authority may require a longer
            period on request, that longer period; otherwise ``None``.
        notes: Scope caveats a reader needs before relying on the row.
    """

    jurisdiction: str
    record_class: RecordClass
    min_years: int
    clock_start: ClockStart
    authority: str
    citation: str
    accessible_years: Optional[int] = None
    extension_years: Optional[int] = None
    notes: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.min_years, int) or self.min_years < 0:
            raise RetentionError(
                f"{self.jurisdiction}/{self.record_class}: min_years must be a "
                f"non-negative int, got {self.min_years!r}")
        if self.accessible_years is not None and not (
                0 <= self.accessible_years <= self.min_years):
            raise RetentionError(
                f"{self.citation}: accessible_years must lie within "
                f"[0, min_years], got {self.accessible_years!r}")
        if self.extension_years is not None and self.extension_years < self.min_years:
            raise RetentionError(
                f"{self.citation}: extension_years ({self.extension_years}) must "
                f"be >= min_years ({self.min_years})")


# ---------------------------------------------------------------------------
# Built-in rule table. Every figure is sourced in references/standards.md.
# Where two instruments bind the same firm, the row states the longer floor and
# names both, because the longer floor is the one that governs.
# ---------------------------------------------------------------------------
DEFAULT_RETENTION_RULES: Tuple[RetentionRule, ...] = (
    # --- United States: SEC-registered broker-dealer that is a FINRA member ---
    RetentionRule(
        jurisdiction="US",
        record_class=RecordClass.TRADE_AND_LEDGER,
        min_years=6,
        clock_start=ClockStart.RECORD_CREATION,
        authority="SEC / FINRA",
        citation="17 CFR 240.17a-4(a); FINRA Rule 4511(b)",
        accessible_years=2,
        notes=("Blotters, general ledger, customer account ledgers and the "
               "ledger of long/short positions (records under 17a-3(a)(1)-(3) "
               "and (a)(5))."),
    ),
    RetentionRule(
        jurisdiction="US",
        record_class=RecordClass.ORDER_AUDIT_TRAIL,
        min_years=6,
        clock_start=ClockStart.RECORD_CREATION,
        authority="SEC / FINRA",
        citation="FINRA Rule 4511(b) (SEC Rule 17a-4(b)(1) alone would give 3 years)",
        accessible_years=2,
        notes=("Order memoranda under 17a-3(a)(6)-(7) carry a three-year floor "
               "under 17a-4(b)(1); FINRA members are held to the six-year floor "
               "in Rule 4511(b). A non-FINRA-member firm should override this "
               "row to 3 years."),
    ),
    RetentionRule(
        jurisdiction="US",
        record_class=RecordClass.COMMUNICATION,
        min_years=3,
        clock_start=ClockStart.RECORD_CREATION,
        authority="SEC",
        citation="17 CFR 240.17a-4(b)(4)",
        accessible_years=2,
        notes=("Communications received and sent relating to the firm's "
               "business as such. FINRA Rule 4511(b)'s six-year residual does "
               "not displace this, because 17a-4(b)(4) specifies a period."),
    ),
    RetentionRule(
        jurisdiction="US",
        record_class=RecordClass.CLIENT_ACCOUNT,
        min_years=6,
        clock_start=ClockStart.ACCOUNT_CLOSURE,
        authority="SEC",
        citation="17 CFR 240.17a-4(e)(5)",
        notes=("Runs from account closure, or from the date the information is "
               "replaced or updated - not from record creation."),
    ),
    RetentionRule(
        jurisdiction="US",
        record_class=RecordClass.OTHER,
        min_years=6,
        clock_start=ClockStart.RECORD_CREATION,
        authority="FINRA",
        citation="FINRA Rule 4511(b)",
        notes=("Residual floor for FINRA books and records with no period "
               "specified elsewhere."),
    ),

    # --- United Kingdom: FCA common platform firm, MiFID business ---
    *(
        RetentionRule(
            jurisdiction="UK",
            record_class=rc,
            min_years=5,
            clock_start=ClockStart.RECORD_CREATION,
            authority="FCA",
            citation="SYSC 9.1.2R (telephone/electronic communications: SYSC 10A)",
            extension_years=7,
            notes=("Applies to MiFID business. For non-MiFID business SYSC "
                   "9.1.5G is principles-based, not a fixed period."),
        )
        for rc in RecordClass
    ),

    # --- European Union: MiFID II investment firm ---
    *(
        RetentionRule(
            jurisdiction="EU",
            record_class=rc,
            min_years=5,
            clock_start=ClockStart.RECORD_CREATION,
            authority="ESMA / national competent authorities",
            citation="MiFID II Art. 16(6) and 16(7); Del. Reg. (EU) 2017/565 Art. 72",
            extension_years=7,
            notes=("The period runs from the date of the record or "
                   "communication. Member-State law may go further."),
        )
        for rc in RecordClass
    ),

    # --- Singapore: MAS capital markets services licence holder ---
    *(
        RetentionRule(
            jurisdiction="SG",
            record_class=rc,
            min_years=5,
            clock_start=ClockStart.RECORD_CREATION,
            authority="MAS",
            citation="Securities and Futures Act 2001 s.102 and the SF (Licensing "
                     "and Conduct of Business) Regulations",
            notes=("Primary text could not be retrieved during the August 2026 "
                   "review; see references/standards.md before relying on this "
                   "row for a purge decision."),
        )
        for rc in RecordClass
    ),

    # --- Australia: Corporations Act company / AFS licensee ---
    *(
        RetentionRule(
            jurisdiction="AU",
            record_class=rc,
            min_years=7,
            clock_start=ClockStart.RECORD_CREATION,
            authority="ASIC",
            citation="Corporations Act 2001 s.286(2); ASIC market integrity rules",
            notes=("Seven years after the transactions covered by the record "
                   "are completed."),
        )
        for rc in RecordClass
    ),

    # --- India: SEBI-registered stock broker / Indian company ---
    RetentionRule(
        jurisdiction="IN",
        record_class=RecordClass.TRADE_AND_LEDGER,
        min_years=8,
        clock_start=ClockStart.RECORD_CREATION,
        authority="MCA / SEBI",
        citation="Companies Act 2013 s.128(5) (SEBI (Stock Brokers) Regulations "
                 "1992 reg. 18 alone would give 5 years)",
        notes=("Books of account of an Indian company must be kept for eight "
               "financial years; the SEBI broker floor of five years is the "
               "shorter of the two and does not govern where both apply."),
    ),
    *(
        RetentionRule(
            jurisdiction="IN",
            record_class=rc,
            min_years=5,
            clock_start=ClockStart.RECORD_CREATION,
            authority="SEBI",
            citation="SEBI (Stock Brokers) Regulations 1992 reg. 18; "
                     "Securities Contracts (Regulation) Rules 1957 r.15",
            notes=("Five years is the SEBI floor. Records that are also books "
                   "of account of an Indian company attract Companies Act 2013 "
                   "s.128(5)'s eight financial years."),
        )
        for rc in RecordClass
        if rc is not RecordClass.TRADE_AND_LEDGER
    ),
)


@dataclass
class RetentionRecord:
    """A record whose retention obligation is to be assessed.

    Attributes:
        record_id: Unique identifier within a batch.
        record_class: Which retention floor applies.
        jurisdictions: Every jurisdiction whose rules bind this record. The
            latest resulting purge date governs; supplying only one jurisdiction
            for a record that is in fact subject to several understates the
            obligation.
        creation_date: ISO-8601 date (``YYYY-MM-DD``) or offset-aware datetime.
        clock_start_date: Required when an applicable rule measures from an
            event other than record creation (e.g. account closure). Left
            ``None``, such a record is reported ``INDETERMINATE`` rather than
            silently measured from creation.
        policy_retention_years: The firm's own configured retention duration for
            this record, if any, checked against the regulatory floor.
        legal_hold: ``True`` while a hold is asserted. Forces ``LEGAL_HOLD``.
    """

    record_id: str
    record_class: RecordClass
    jurisdictions: Sequence[str]
    creation_date: str
    clock_start_date: Optional[str] = None
    policy_retention_years: Optional[float] = None
    legal_hold: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.record_id, str) or not self.record_id.strip():
            raise RetentionError(f"record_id must be a non-empty string, got {self.record_id!r}")
        if not isinstance(self.record_class, RecordClass):
            raise RetentionError(
                f"{self.record_id}: record_class must be a RecordClass, "
                f"got {self.record_class!r}")
        if isinstance(self.jurisdictions, str) or not isinstance(self.jurisdictions, AbcSequence):
            raise RetentionError(
                f"{self.record_id}: jurisdictions must be a sequence of codes, "
                f"not {self.jurisdictions!r} - a bare string would be read "
                f"character by character")
        normalised: List[str] = []
        for code in self.jurisdictions:
            if not isinstance(code, str) or not code.strip():
                raise RetentionError(
                    f"{self.record_id}: jurisdiction codes must be non-empty "
                    f"strings, got {code!r}")
            upper = code.strip().upper()
            if upper not in normalised:
                normalised.append(upper)
        if not normalised:
            raise RetentionError(f"{self.record_id}: at least one jurisdiction is required")
        self.jurisdictions = tuple(normalised)

        # Fail fast on unparseable dates rather than mid-batch.
        _parse_date(self.creation_date, "creation_date", self.record_id)
        if self.clock_start_date is not None:
            _parse_date(self.clock_start_date, "clock_start_date", self.record_id)

        if self.policy_retention_years is not None:
            value = self.policy_retention_years
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise RetentionError(
                    f"{self.record_id}: policy_retention_years must be a number, "
                    f"got {value!r}")
            if not math.isfinite(value) or value < 0:
                raise RetentionError(
                    f"{self.record_id}: policy_retention_years must be finite and "
                    f">= 0, got {value!r}")
        if not isinstance(self.legal_hold, bool):
            raise RetentionError(
                f"{self.record_id}: legal_hold must be a bool, got {self.legal_hold!r}")


@dataclass(frozen=True)
class RetentionAssessment:
    """Per-record outcome.

    ``earliest_permissible_purge_date`` is the date on which the last applicable
    floor has fully elapsed; the record is eligible for disposition *review* on
    and after that date. It is ``None`` whenever the floor is unknown.
    """

    record_id: str
    record_class: RecordClass
    jurisdictions: Tuple[str, ...]
    status: RetentionStatus
    binding_jurisdiction: Optional[str] = None
    binding_citation: Optional[str] = None
    required_years: Optional[int] = None
    retention_start: Optional[date] = None
    earliest_permissible_purge_date: Optional[date] = None
    days_until_eligible: Optional[int] = None
    readily_accessible_until: Optional[date] = None
    policy_shortfall_years: Optional[float] = None
    applied_citations: Tuple[str, ...] = ()
    issues: Tuple[str, ...] = ()


@dataclass(frozen=True)
class RetentionComplianceReport:
    """Batch outcome. ``ISSUES_FOUND`` covers indeterminate records and policy
    shortfalls; records legitimately still inside their retention period are not
    an issue."""

    as_of: date
    total_records: int
    retain_count: int
    eligible_for_review_count: int
    legal_hold_count: int
    indeterminate_count: int
    policy_shortfall_count: int
    assessments: Tuple[RetentionAssessment, ...]
    overall_status: str
    audit_notes: str


class RecordRetentionPeriodsByJurisdictionEngine:
    """Resolves record retention floors across jurisdictions.

    Args:
        rules: Rule table to use. Defaults to ``DEFAULT_RETENTION_RULES``.
            Later rules for the same ``(jurisdiction, record_class)`` replace
            earlier ones, so a caller can append overrides.
        extension_requested: Jurisdiction codes where the competent authority
            has requested the extended period (e.g. the FCA or an EU NCA asking
            for seven years instead of five). Rules with ``extension_years`` set
            then use the longer figure.

    The engine holds no mutable state between calls; assessments depend only on
    the rule table, the record, and the ``as_of`` date passed in.
    """

    def __init__(
        self,
        rules: Iterable[RetentionRule] = DEFAULT_RETENTION_RULES,
        *,
        extension_requested: Iterable[str] = (),
    ) -> None:
        table: Dict[Tuple[str, RecordClass], RetentionRule] = {}
        for rule in rules:
            if not isinstance(rule, RetentionRule):
                raise RetentionError(f"rules must contain RetentionRule instances, got {rule!r}")
            table[(rule.jurisdiction.upper(), rule.record_class)] = rule
        if not table:
            raise RetentionError("rule table is empty; nothing could be assessed")
        self._rules: Mapping[Tuple[str, RecordClass], RetentionRule] = table
        requested: List[str] = []
        for code in extension_requested:
            if not isinstance(code, str) or not code.strip():
                raise RetentionError(
                    f"extension_requested must contain non-empty jurisdiction "
                    f"codes, got {code!r}")
            requested.append(code.strip().upper())
        self._extension_requested = frozenset(requested)

    # -- rule lookup --------------------------------------------------------

    @property
    def jurisdictions(self) -> Tuple[str, ...]:
        """Jurisdiction codes present in the rule table, sorted."""
        return tuple(sorted({jur for jur, _ in self._rules}))

    def rule_for(self, jurisdiction: str, record_class: RecordClass) -> Optional[RetentionRule]:
        """Return the rule for a jurisdiction and class, falling back to that
        jurisdiction's ``OTHER`` rule. ``None`` if the jurisdiction is unknown."""
        key = jurisdiction.strip().upper()
        exact = self._rules.get((key, record_class))
        if exact is not None:
            return exact
        return self._rules.get((key, RecordClass.OTHER))

    def required_years(self, rule: RetentionRule) -> int:
        """Years compelled by ``rule``, honouring any requested extension."""
        if rule.extension_years is not None and rule.jurisdiction.upper() in self._extension_requested:
            return rule.extension_years
        return rule.min_years

    # -- assessment ---------------------------------------------------------

    def assess(self, record: RetentionRecord, as_of: date) -> RetentionAssessment:
        """Assess one record against every jurisdiction that binds it."""
        if not isinstance(as_of, date) or isinstance(as_of, datetime):
            raise RetentionError(f"as_of must be a datetime.date, got {as_of!r}")

        issues: List[str] = []
        citations: List[str] = []
        creation = _parse_date(record.creation_date, "creation_date", record.record_id)
        explicit_start = (
            _parse_date(record.clock_start_date, "clock_start_date", record.record_id)
            if record.clock_start_date is not None else None)

        best: Optional[Tuple[date, date, RetentionRule, int]] = None  # purge, start, rule, years
        accessible_until: Optional[date] = None
        determinable = True

        for jurisdiction in record.jurisdictions:
            rule = self.rule_for(jurisdiction, record.record_class)
            if rule is None:
                determinable = False
                issues.append(
                    f"No rule for jurisdiction {jurisdiction!r}; retention floor unknown. "
                    f"Known jurisdictions: {', '.join(self.jurisdictions)}.")
                continue

            citations.append(f"{jurisdiction}: {rule.citation}")

            if rule.clock_start is ClockStart.RECORD_CREATION:
                start = creation
            elif explicit_start is None:
                determinable = False
                issues.append(
                    f"{jurisdiction} rule ({rule.citation}) measures from "
                    f"{rule.clock_start.value}; clock_start_date is required and was "
                    f"not supplied. Refusing to measure from creation_date.")
                continue
            else:
                start = explicit_start

            years = self.required_years(rule)
            purge = _add_years(start, years)
            if best is None or purge > best[0]:
                best = (purge, start, rule, years)

            # The readily-accessible sub-period is a separate obligation and is
            # not necessarily imposed by the rule that sets the longest overall
            # floor: a record bound by both the UK (5 years, no sub-period) and
            # the US (6 years, first 2 accessible) still owes the US one.
            if rule.accessible_years is not None:
                candidate = _add_years(start, rule.accessible_years)
                if accessible_until is None or candidate > accessible_until:
                    accessible_until = candidate

        policy_shortfall: Optional[float] = None
        if record.policy_retention_years is not None and not determinable:
            # Comparing a policy against a partially resolved floor would
            # understate the gap; say so rather than report a smaller number.
            issues.append(
                "Configured retention was not compared against the floor, because "
                "the floor could not be fully resolved.")
        elif best is not None and record.policy_retention_years is not None:
            deficit = best[3] - record.policy_retention_years
            if deficit > 0:
                policy_shortfall = round(deficit, 6)
                issues.append(
                    f"Configured retention of {record.policy_retention_years} years is "
                    f"{policy_shortfall} years short of the {best[3]}-year floor set by "
                    f"{best[2].citation}.")

        if not determinable or best is None:
            status = RetentionStatus.INDETERMINATE
        elif record.legal_hold:
            status = RetentionStatus.LEGAL_HOLD
        elif as_of >= best[0]:
            status = RetentionStatus.ELIGIBLE_FOR_REVIEW
        else:
            status = RetentionStatus.RETAIN

        if record.legal_hold and status is RetentionStatus.INDETERMINATE:
            issues.append("Legal hold is asserted; retain regardless of the unresolved floor.")

        if best is None or not determinable:
            return RetentionAssessment(
                record_id=record.record_id,
                record_class=record.record_class,
                jurisdictions=tuple(record.jurisdictions),
                status=status,
                policy_shortfall_years=policy_shortfall,
                applied_citations=tuple(citations),
                issues=tuple(issues),
            )

        purge_date, start_date, rule, years = best

        return RetentionAssessment(
            record_id=record.record_id,
            record_class=record.record_class,
            jurisdictions=tuple(record.jurisdictions),
            status=status,
            binding_jurisdiction=rule.jurisdiction,
            binding_citation=rule.citation,
            required_years=years,
            retention_start=start_date,
            earliest_permissible_purge_date=purge_date,
            days_until_eligible=max((purge_date - as_of).days, 0),
            readily_accessible_until=accessible_until,
            policy_shortfall_years=policy_shortfall,
            applied_citations=tuple(citations),
            issues=tuple(issues),
        )

    def assess_all(
        self,
        records: Sequence[RetentionRecord],
        as_of: date,
    ) -> RetentionComplianceReport:
        """Assess a batch. Duplicate ``record_id`` values are rejected, because
        an audit report with two rows for one identifier cannot be reconciled."""
        seen: Dict[str, int] = {}
        for index, record in enumerate(records):
            if not isinstance(record, RetentionRecord):
                raise RetentionError(
                    f"records[{index}] must be a RetentionRecord, got {record!r}")
            if record.record_id in seen:
                raise RetentionError(
                    f"duplicate record_id {record.record_id!r} at positions "
                    f"{seen[record.record_id]} and {index}")
            seen[record.record_id] = index

        assessments = tuple(self.assess(record, as_of) for record in records)

        retain = sum(1 for a in assessments if a.status is RetentionStatus.RETAIN)
        eligible = sum(1 for a in assessments if a.status is RetentionStatus.ELIGIBLE_FOR_REVIEW)
        held = sum(1 for a in assessments if a.status is RetentionStatus.LEGAL_HOLD)
        indeterminate = sum(1 for a in assessments if a.status is RetentionStatus.INDETERMINATE)
        shortfalls = sum(1 for a in assessments if a.policy_shortfall_years is not None)

        has_issues = indeterminate > 0 or shortfalls > 0
        overall = "ISSUES_FOUND" if has_issues else "NO_ISSUES_FOUND"

        notes = (
            f"RETENTION ASSESSMENT [{overall}] as of {as_of.isoformat()}: "
            f"total = {len(assessments)}, retain = {retain}, "
            f"eligible for disposition review = {eligible}, legal hold = {held}, "
            f"indeterminate = {indeterminate}, policy shortfalls = {shortfalls}. "
            f"ELIGIBLE_FOR_REVIEW is not a deletion approval."
        )
        if has_issues:
            logger.warning(notes)
        else:
            logger.info(notes)

        return RetentionComplianceReport(
            as_of=as_of,
            total_records=len(assessments),
            retain_count=retain,
            eligible_for_review_count=eligible,
            legal_hold_count=held,
            indeterminate_count=indeterminate,
            policy_shortfall_count=shortfalls,
            assessments=assessments,
            overall_status=overall,
            audit_notes=notes,
        )


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _parse_date(value: object, field_name: str, record_id: str) -> date:
    """Parse ``YYYY-MM-DD`` or an offset-aware ISO-8601 datetime into a UTC date.

    A naive datetime is rejected: a record stamped ``2019-01-01T23:30:00`` is on
    either side of a year boundary depending on the zone, and a retention
    deadline must not depend on an unstated assumption.
    """
    if not isinstance(value, str) or not value.strip():
        raise RetentionError(f"{record_id}: {field_name} must be a non-empty ISO-8601 string, "
                             f"got {value!r}")
    text = value.strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RetentionError(
            f"{record_id}: {field_name} {value!r} is not an ISO-8601 date or datetime") from exc
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise RetentionError(
            f"{record_id}: {field_name} {value!r} is a naive datetime; supply a UTC "
            f"offset or a plain YYYY-MM-DD date")
    return parsed.astimezone(timezone.utc).date()


def _add_years(start: date, years: int) -> date:
    """Add whole calendar years, mapping 29 February onto 28 February.

    Calendar arithmetic, not ``365 * years`` days: over a six-year window the
    day-count approximation drifts by one or two days, which is enough to
    authorise a purge a day early.
    """
    try:
        return start.replace(year=start.year + years)
    except ValueError:
        # 29 February in a non-leap target year.
        return start.replace(year=start.year + years, month=2, day=28)
