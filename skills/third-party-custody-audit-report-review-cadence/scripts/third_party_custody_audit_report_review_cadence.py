"""
third-party-custody-audit-report-review-cadence: a review-cadence engine for the
audit evidence a firm holds on the custodians that hold its assets.

What this module is and is not
------------------------------
It is a **structured, evidence-tracking governance aid**. It turns the audit
artefacts a firm has actually collected on a custodian -- SOC 1/SOC 2 Type II
reports, bridge (gap) letters, Proof of Reserves attestations, and the firm's own
implementation of the report's Complementary User Entity Controls -- into a dated,
auditable review verdict. It is not legal advice, it does not read or parse a SOC
report, and it cannot tell you whether a control objective was actually met: that
conclusion belongs to the service auditor and to the reviewer reading Section IV.

Absence of evidence is not compliance
-------------------------------------
Every check in this engine fails closed. A vendor with no SOC 1/SOC 2 Type II
report is ``NON_COMPLIANT``/``CRITICAL`` even if it has a glossy Proof of Reserves
page. A vendor whose CUEC implementation has never been assessed is reported as
unassessed, not as 100% implemented. A governance engine that returns "compliant"
for a custodian it knows nothing about is worse than no engine.

Four corrections worth stating explicitly
-----------------------------------------
Earlier revisions of this skill got the following wrong, and the fixes change
output:

* **A Proof of Reserves attestation is not a substitute for a SOC report.** The
  PCAOB Office of the Investor Advocate advisory of 2023-03-08 states that PoR
  engagements "are not audits" and that the resulting reports "do not provide any
  meaningful assurance". An earlier revision fell back to the most recently
  submitted report of *any* type when no SOC report existed, so a custodian with
  only a PoR attestation could be rated ``COMPLIANT``/``LOW``. It now cannot.

* **A bridge letter is an unaudited management assertion.** It is signed by the
  service organisation's management, not by the service auditor, and carries no
  audit assurance, so relying on one caps the vendor at ``MEDIUM`` and never leaves
  it ``LOW``. Nor can it substitute for an expired report: an earlier revision
  consulted bridge letters *only* once the annual cadence had already lapsed, and
  accepted a letter of any span, so an 18-month "bridge" over a year-stale report
  read as compliant. Coverage is now scored as the unbridged window since the last
  audited or bridged date, against ``max_unbridged_gap_days`` (default 90) -- the
  industry practice of bridging no more than about three months. The AICPA SOC
  guidance does not address bridge letters at all, so no standard sets this
  number.

* **The AICPA sets no minimum Type II coverage period.** Periods in practice run
  3-12 months. ``min_type2_coverage_days`` (default 180) is a *firm policy*
  threshold, not a standard, and is reported as such.

* **Configured cadences are now honoured.** ``review_cadence_days`` and
  ``por_cadence_days`` were previously stored and ignored; staleness was
  hard-coded to 365 days and Proof of Reserves was never evaluated.

Where the SOC obligation actually comes from
--------------------------------------------
None of the instruments this skill cites obliges a firm to collect a SOC report
from an *unaffiliated* third-party custodian. Advisers Act rule 206(4)-2(a)(6)(ii)
requires an internal control report from a PCAOB-registered and PCAOB-inspected
accountant at least once each calendar year only where the adviser or a **related
person** is the qualified custodian. The annual cadence enforced here is therefore
firm policy for unaffiliated custodians and a rule-driven floor for related-person
custodians. See ``references/standards.md`` for the full source list.

Determinism
-----------
``evaluate_vendor_compliance`` accepts ``current_date``. It defaults to today only
as a convenience; pass it explicitly so a review is reproducible and the audit
trail records what was known when. Evaluation is side-effect free -- call
``record_review`` to stamp a review as performed.
"""
from __future__ import annotations

import datetime
import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ReportType(Enum):
    SOC1_TYPE2 = "SOC1_TYPE2"              # Financial controls (SSAE 18 / AT-C 320, ISAE 3402)
    SOC2_TYPE2 = "SOC2_TYPE2"              # Security, Availability & Confidentiality (TSC)
    PROOF_OF_RESERVES = "PROOF_OF_RESERVES"# On-chain reserve attestation -- NOT an audit
    ISO27001 = "ISO27001"                  # Information Security Management System
    FINANCIAL_AUDIT = "FINANCIAL_AUDIT"    # Audited financial statements


#: The only report types that evidence operating effectiveness of a custodian's
#: controls over a period. Nothing else substitutes for them.
TYPE2_SOC_REPORT_TYPES = frozenset({ReportType.SOC1_TYPE2, ReportType.SOC2_TYPE2})


class AuditOpinion(Enum):
    UNQUALIFIED = "UNQUALIFIED"  # Clean opinion (Passed)
    QUALIFIED = "QUALIFIED"      # Passed with exceptions/qualifications
    ADVERSE = "ADVERSE"          # Failed controls
    DISCLAIMER = "DISCLAIMER"    # Auditor unable to form opinion


#: Opinions that mean the auditor could not give a clean report on the controls.
ADVERSE_OPINIONS = frozenset(
    {AuditOpinion.QUALIFIED, AuditOpinion.ADVERSE, AuditOpinion.DISCLAIMER}
)


class ComplianceStatus(Enum):
    COMPLIANT = "COMPLIANT"
    PENDING_REVIEW = "PENDING_REVIEW"
    OVERDUE = "OVERDUE"
    NON_COMPLIANT = "NON_COMPLIANT"
    ESCALATED = "ESCALATED"


class RiskRating(Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


#: Severity order, used to raise a rating monotonically. A check may raise the
#: rating; no check may lower one another check already set.
_RISK_ORDER: Tuple[RiskRating, ...] = (
    RiskRating.LOW,
    RiskRating.MEDIUM,
    RiskRating.HIGH,
    RiskRating.CRITICAL,
)


def _raise_risk(current: RiskRating, floor: RiskRating) -> RiskRating:
    """Returns the more severe of ``current`` and ``floor``."""
    return max(current, floor, key=_RISK_ORDER.index)


class CustodyAuditError(Exception):
    """Base exception for custody audit review errors."""
    pass


class OverdueAuditError(CustodyAuditError):
    """
    Raised when an audit report or review cadence is past due.

    The engine itself *reports* rather than raises: it returns a
    :class:`ReviewResult` so a caller can record every finding. This exception is
    provided for callers that want a fail-fast wrapper around a review gate.
    """
    pass


class QualifiedOpinionError(CustodyAuditError):
    """
    Raised when an audit opinion is qualified, adverse, or disclaimed.

    As with :class:`OverdueAuditError`, the engine returns findings rather than
    raising; this is for callers implementing a hard capital-allocation gate.
    """
    pass


def _require_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CustodyAuditError(f"{label} must be a non-empty string.")
    return value


def _require_date(value: object, label: str) -> datetime.date:
    """
    Requires a plain ``datetime.date``.

    ``datetime.datetime`` is a subclass of ``date`` but does not subtract against
    one, so an accidental ``datetime.now()`` would otherwise reach the arithmetic
    and fail with an opaque ``TypeError``. Rejecting it here also avoids silently
    assuming a timezone for an audit date.
    """
    if isinstance(value, datetime.datetime) or not isinstance(value, datetime.date):
        raise CustodyAuditError(f"{label} must be a datetime.date (not a datetime).")
    return value


@dataclass
class CustodyVendor:
    """
    A third-party custodian under audit-review governance.

    Cadence fields are firm policy, not standards. ``review_cadence_days`` is the
    maximum age of the latest SOC Type II coverage end before the vendor is overdue
    for a fresh report. ``max_unbridged_gap_days`` is how much time since the end of
    audited (or bridged) coverage may pass unbridged -- industry practice bridges no
    more than about three months, and the AICPA SOC guidance does not address bridge
    letters at all. ``min_type2_coverage_days`` is the shortest Type II observation
    period the firm accepts -- the AICPA sets no minimum, and periods of 3 to 12
    months occur in practice.
    """

    vendor_id: str
    name: str  # e.g., 'Coinbase Custody', 'BitGo', 'Fireblocks'
    asset_classes_held: List[str]  # e.g., ['BTC', 'ETH', 'USD']
    total_aum_usd: float
    review_cadence_days: int = 365   # Annual review default
    por_cadence_days: int = 90       # Quarterly Proof of Reserves default
    max_unbridged_gap_days: int = 90
    min_type2_coverage_days: int = 180
    requires_proof_of_reserves: bool = False

    def __post_init__(self) -> None:
        _require_text(self.vendor_id, "vendor_id")
        _require_text(self.name, "name")
        if not isinstance(self.asset_classes_held, list):
            raise CustodyAuditError("asset_classes_held must be a list of strings.")
        if isinstance(self.total_aum_usd, bool) or not isinstance(
            self.total_aum_usd, (int, float)
        ):
            raise CustodyAuditError("total_aum_usd must be numeric.")
        if not math.isfinite(float(self.total_aum_usd)) or self.total_aum_usd < 0:
            raise CustodyAuditError("total_aum_usd must be a finite, non-negative number.")
        for label, value in (
            ("review_cadence_days", self.review_cadence_days),
            ("por_cadence_days", self.por_cadence_days),
            ("min_type2_coverage_days", self.min_type2_coverage_days),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise CustodyAuditError(f"{label} must be a positive integer of days.")
        if (
            isinstance(self.max_unbridged_gap_days, bool)
            or not isinstance(self.max_unbridged_gap_days, int)
            or self.max_unbridged_gap_days < 0
        ):
            raise CustodyAuditError("max_unbridged_gap_days must be a non-negative integer.")


@dataclass
class AuditReport:
    """
    One audit artefact received from a custodian.

    ``cuecs_required`` is the list of Complementary User Entity Control identifiers
    the report places on the *user entity*. Populating it is what lets the engine
    distinguish "CUEC not implemented" from "CUEC never assessed".
    """

    report_id: str
    vendor_id: str
    report_type: ReportType
    opinion: AuditOpinion
    coverage_start: datetime.date
    coverage_end: datetime.date
    report_date: datetime.date
    deficiencies_found: int = 0
    cuecs_required: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        _require_text(self.report_id, "report_id")
        _require_text(self.vendor_id, "vendor_id")
        if not isinstance(self.report_type, ReportType):
            raise CustodyAuditError("report_type must be a ReportType member.")
        if not isinstance(self.opinion, AuditOpinion):
            raise CustodyAuditError("opinion must be an AuditOpinion member.")
        for label, value in (
            ("coverage_start", self.coverage_start),
            ("coverage_end", self.coverage_end),
            ("report_date", self.report_date),
        ):
            _require_date(value, label)
        if self.coverage_end < self.coverage_start:
            raise CustodyAuditError(
                f"Report {self.report_id}: coverage_end {self.coverage_end} precedes "
                f"coverage_start {self.coverage_start}."
            )
        if self.report_date < self.coverage_end:
            raise CustodyAuditError(
                f"Report {self.report_id}: report_date {self.report_date} precedes "
                f"coverage_end {self.coverage_end}; a report cannot be issued before the "
                f"period it covers has ended."
            )
        if isinstance(self.deficiencies_found, bool) or not isinstance(
            self.deficiencies_found, int
        ):
            raise CustodyAuditError("deficiencies_found must be an integer.")
        if self.deficiencies_found < 0:
            raise CustodyAuditError("deficiencies_found cannot be negative.")


@dataclass
class GapLetter:
    """
    A bridge (gap) letter: the service organisation's *management* assertion that
    no material control changes occurred between ``period_start`` and
    ``period_end``. It is not signed by the service auditor and carries no audit
    assurance; ``report_id`` must name the SOC report it bridges.
    """

    letter_id: str
    vendor_id: str
    report_id: str
    period_start: datetime.date
    period_end: datetime.date
    no_material_changes_asserted: bool = True
    signed_date: Optional[datetime.date] = None

    def __post_init__(self) -> None:
        _require_text(self.letter_id, "letter_id")
        _require_text(self.vendor_id, "vendor_id")
        _require_text(self.report_id, "report_id")
        for label, value in (
            ("period_start", self.period_start),
            ("period_end", self.period_end),
        ):
            _require_date(value, label)
        if self.period_end < self.period_start:
            raise CustodyAuditError(
                f"Gap letter {self.letter_id}: period_end {self.period_end} precedes "
                f"period_start {self.period_start}."
            )
        if self.signed_date is not None:
            _require_date(self.signed_date, "signed_date")


@dataclass
class CUECCheck:
    """
    The firm's own verification that one Complementary User Entity Control named by
    the custodian's SOC report is implemented internally.

    ``is_implemented=True`` with blank ``verification_evidence`` is treated as
    unevidenced, i.e. not implemented.
    """

    cuec_id: str
    description: str
    is_implemented: bool
    verification_evidence: str

    def __post_init__(self) -> None:
        _require_text(self.cuec_id, "cuec_id")
        if not isinstance(self.is_implemented, bool):
            raise CustodyAuditError("is_implemented must be a bool.")

    @property
    def is_evidenced(self) -> bool:
        """True only when the control is implemented *and* evidence was recorded."""
        return bool(self.is_implemented) and bool(
            isinstance(self.verification_evidence, str) and self.verification_evidence.strip()
        )


@dataclass
class ReviewResult:
    vendor_id: str
    vendor_name: str
    status: ComplianceStatus
    risk_rating: RiskRating
    last_review_date: datetime.date
    next_due_date: datetime.date
    implemented_cuec_pct: float
    findings: List[str] = field(default_factory=list)
    audit_trail: List[str] = field(default_factory=list)


class CustodyAuditReviewEngine:
    """
    Institutional audit review cadence engine for third-party custodians.

    Tracks vendor audit artefacts (SOC 1/2 Type II, Proof of Reserves), validates
    auditor opinions, checks bridge/gap letter coverage, verifies Complementary
    User Entity Controls, and assigns a monotonic risk rating. Evaluation is pure:
    it reads recorded state and returns a :class:`ReviewResult`.
    """

    def __init__(self) -> None:
        self.vendors: Dict[str, CustodyVendor] = {}
        self.audit_reports: Dict[str, List[AuditReport]] = {}
        self.gap_letters: Dict[str, List[GapLetter]] = {}
        self.cuec_status: Dict[str, List[CUECCheck]] = {}
        self.last_reviews: Dict[str, datetime.date] = {}
        logger.info("Initialized Custody Audit Review Cadence Engine")

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def register_vendor(self, vendor: CustodyVendor, replace: bool = False) -> None:
        """
        Registers a third-party custody vendor for audit monitoring.

        Re-registering an existing ``vendor_id`` is rejected unless ``replace=True``:
        silently overwriting would discard every audit report, gap letter and CUEC
        check already recorded against that vendor.
        """
        if not isinstance(vendor, CustodyVendor):
            raise CustodyAuditError("register_vendor expects a CustodyVendor.")
        if vendor.vendor_id in self.vendors and not replace:
            raise CustodyAuditError(
                f"Vendor ID {vendor.vendor_id} is already registered. Pass replace=True "
                f"to overwrite it and discard its recorded evidence."
            )

        self.vendors[vendor.vendor_id] = vendor
        self.audit_reports[vendor.vendor_id] = []
        self.gap_letters[vendor.vendor_id] = []
        self.cuec_status[vendor.vendor_id] = []
        self.last_reviews.pop(vendor.vendor_id, None)
        logger.info(
            f"Registered Custody Vendor: {vendor.name} (AUM: ${vendor.total_aum_usd:,.2f} USD)"
        )

    def submit_audit_report(self, report: AuditReport) -> None:
        """
        Submits a newly received audit report for a registered vendor.

        Duplicate ``report_id`` values are rejected: double-counting the same SOC
        report is how a stale evidence file comes to look current.
        """
        if not isinstance(report, AuditReport):
            raise CustodyAuditError("submit_audit_report expects an AuditReport.")
        if report.vendor_id not in self.vendors:
            raise CustodyAuditError(f"Vendor ID {report.vendor_id} is not registered.")

        existing = self.audit_reports[report.vendor_id]
        if any(r.report_id == report.report_id for r in existing):
            raise CustodyAuditError(
                f"Report ID {report.report_id} already submitted for vendor {report.vendor_id}."
            )

        vendor = self.vendors[report.vendor_id]
        if report.report_type in TYPE2_SOC_REPORT_TYPES:
            coverage_days = (report.coverage_end - report.coverage_start).days
            if coverage_days < vendor.min_type2_coverage_days:
                # Surfaced again as a finding at evaluation time; a log line alone
                # never reaches the reviewer reading the ReviewResult.
                logger.warning(
                    f"Report {report.report_id} coverage ({coverage_days} days) is below the "
                    f"firm policy minimum of {vendor.min_type2_coverage_days} days for a "
                    f"Type II report."
                )

        existing.append(report)
        logger.info(
            f"Submitted {report.report_type.value} report for {report.vendor_id} "
            f"(Opinion: {report.opinion.value}, Deficiencies: {report.deficiencies_found})"
        )

    def submit_gap_letter(self, gap_letter: GapLetter) -> None:
        """
        Submits a bridge/gap letter asserting no material changes during the gap
        period. Validity is decided at evaluation time against the SOC report the
        letter names; recording a defective letter is deliberate, so the defect
        appears in the review rather than vanishing at ingestion.
        """
        if not isinstance(gap_letter, GapLetter):
            raise CustodyAuditError("submit_gap_letter expects a GapLetter.")
        if gap_letter.vendor_id not in self.vendors:
            raise CustodyAuditError(f"Vendor ID {gap_letter.vendor_id} is not registered.")

        existing = self.gap_letters[gap_letter.vendor_id]
        if any(gl.letter_id == gap_letter.letter_id for gl in existing):
            raise CustodyAuditError(
                f"Gap letter ID {gap_letter.letter_id} already submitted for vendor "
                f"{gap_letter.vendor_id}."
            )

        if not gap_letter.no_material_changes_asserted:
            logger.error(
                f"Gap letter {gap_letter.letter_id} indicates material control changes!"
            )

        existing.append(gap_letter)
        logger.info(
            f"Submitted Gap Letter for vendor {gap_letter.vendor_id} "
            f"(Period: {gap_letter.period_start} to {gap_letter.period_end})"
        )

    def update_cuec_checks(self, vendor_id: str, cuec_checks: List[CUECCheck]) -> None:
        """
        Replaces the recorded implementation verification of Complementary User
        Entity Controls for a vendor. Duplicate ``cuec_id`` values are rejected so
        that a control cannot be recorded as both implemented and not.
        """
        if vendor_id not in self.vendors:
            raise CustodyAuditError(f"Vendor ID {vendor_id} is not registered.")
        if not isinstance(cuec_checks, list) or not all(
            isinstance(c, CUECCheck) for c in cuec_checks
        ):
            raise CustodyAuditError("cuec_checks must be a list of CUECCheck.")

        seen = set()
        for check in cuec_checks:
            if check.cuec_id in seen:
                raise CustodyAuditError(f"Duplicate CUEC id {check.cuec_id} in submission.")
            seen.add(check.cuec_id)

        self.cuec_status[vendor_id] = list(cuec_checks)
        implemented = sum(1 for c in cuec_checks if c.is_evidenced)
        logger.info(
            f"Updated CUEC status for {vendor_id}: {implemented}/{len(cuec_checks)} "
            f"implemented with evidence."
        )

    def record_review(self, vendor_id: str, review_date: datetime.date) -> None:
        """
        Records that a review was actually performed on ``review_date``.

        Kept separate from :meth:`evaluate_vendor_compliance` so that evaluating a
        vendor -- including the bulk evaluation behind :meth:`get_overdue_vendors`
        -- never mutates the review history as a side effect.
        """
        if vendor_id not in self.vendors:
            raise CustodyAuditError(f"Vendor ID {vendor_id} is not registered.")
        _require_date(review_date, "review_date")
        self.last_reviews[vendor_id] = review_date

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def evaluate_vendor_compliance(
        self, vendor_id: str, current_date: Optional[datetime.date] = None
    ) -> ReviewResult:
        """
        Evaluates audit coverage, bridge-letter validity, Proof of Reserves
        freshness and CUEC implementation, and returns a status, a risk rating and
        the next review due date.

        Pass ``current_date`` explicitly for reproducible output. The call has no
        side effects; use :meth:`record_review` to stamp the review.
        """
        if current_date is None:
            current_date = datetime.date.today()
        _require_date(current_date, "current_date")
        if vendor_id not in self.vendors:
            raise CustodyAuditError(f"Vendor ID {vendor_id} is not registered.")

        vendor = self.vendors[vendor_id]
        reports = self.audit_reports.get(vendor_id, [])
        gap_lets = self.gap_letters.get(vendor_id, [])
        cuecs = self.cuec_status.get(vendor_id, [])

        audit_trail = [
            f"Evaluating custody audit compliance for {vendor.name} on {current_date}"
        ]
        findings: List[str] = []
        risk_rating = RiskRating.LOW
        status = ComplianceStatus.COMPLIANT

        # 1. A SOC 1 / SOC 2 Type II report is the only acceptable evidence of
        #    operating effectiveness. Nothing else stands in for one -- in
        #    particular a Proof of Reserves attestation is not an audit
        #    (PCAOB Office of the Investor Advocate advisory, 2023-03-08).
        soc_reports = [r for r in reports if r.report_type in TYPE2_SOC_REPORT_TYPES]
        if not soc_reports:
            if reports:
                other = ", ".join(sorted({r.report_type.value for r in reports}))
                findings.append(
                    f"No SOC 1/SOC 2 Type II report on file. Reports held ({other}) do not "
                    f"evidence operating effectiveness of custodian controls."
                )
                audit_trail.append(
                    "CRITICAL: Only non-SOC artefacts on file; a Proof of Reserves or "
                    "certification artefact is not a substitute for a Type II examination."
                )
            else:
                findings.append("No audit reports on file.")
                audit_trail.append("CRITICAL: Missing SOC / Audit reports.")
            return ReviewResult(
                vendor_id=vendor_id,
                vendor_name=vendor.name,
                status=ComplianceStatus.NON_COMPLIANT,
                risk_rating=RiskRating.CRITICAL,
                last_review_date=current_date,
                next_due_date=current_date,
                implemented_cuec_pct=0.0,
                findings=findings,
                audit_trail=audit_trail,
            )

        # SOC 1 and SOC 2 cover different control objectives, so neither supersedes
        # the other: evaluate the latest report of *each* type held, or a clean SOC 2
        # would mask a qualified SOC 1. Cadence and CUEC scoping then use the most
        # recent report overall.
        latest_by_type = {
            report_type: max(
                (r for r in soc_reports if r.report_type is report_type),
                key=lambda r: (r.coverage_end, r.report_date, r.report_id),
            )
            for report_type in sorted(
                {r.report_type for r in soc_reports}, key=lambda t: t.value
            )
        }
        latest_soc = max(
            latest_by_type.values(),
            key=lambda r: (r.coverage_end, r.report_date, r.report_id),
        )
        audit_trail.append(
            f"Latest SOC Report: {latest_soc.report_id} ({latest_soc.report_type.value}), "
            f"Coverage End: {latest_soc.coverage_end}, Opinion: {latest_soc.opinion.value}"
        )

        for report in latest_by_type.values():
            if report is not latest_soc:
                audit_trail.append(
                    f"Also held: {report.report_id} ({report.report_type.value}), "
                    f"Coverage End: {report.coverage_end}, Opinion: {report.opinion.value}"
                )

            # 2. Opinion check, per report type.
            if report.opinion in ADVERSE_OPINIONS:
                findings.append(
                    f"{report.report_type.value} audit opinion is {report.opinion.value} "
                    f"({report.report_id}). Control deficiency flagged by auditor."
                )
                risk_rating = _raise_risk(risk_rating, RiskRating.CRITICAL)
                status = ComplianceStatus.ESCALATED

            if report.deficiencies_found > 0:
                # An unqualified opinion can still list exceptions in the Section IV
                # test results; those are what deficiencies_found records.
                findings.append(
                    f"Auditor reported {report.deficiencies_found} control deficiencies "
                    f"in {report.report_id}."
                )
                risk_rating = _raise_risk(risk_rating, RiskRating.HIGH)

            # 3. Observation period length (firm policy, not an AICPA minimum).
            coverage_days = (report.coverage_end - report.coverage_start).days
            audit_trail.append(
                f"{report.report_type.value} observation period: {coverage_days} days"
            )
            if coverage_days < vendor.min_type2_coverage_days:
                findings.append(
                    f"{report.report_type.value} observation period is {coverage_days} days "
                    f"({report.report_id}), below the firm policy minimum of "
                    f"{vendor.min_type2_coverage_days} days."
                )
                risk_rating = _raise_risk(risk_rating, RiskRating.HIGH)

        # 4. Cadence / expired coverage, and the bridge letter that may bridge it.
        days_since_coverage = (current_date - latest_soc.coverage_end).days
        audit_trail.append(f"Days elapsed since SOC coverage end: {days_since_coverage} days")

        if days_since_coverage < 0:
            findings.append(
                f"Latest SOC coverage end {latest_soc.coverage_end} is after the evaluation "
                f"date {current_date}; audit evidence dates are inconsistent."
            )
            risk_rating = _raise_risk(risk_rating, RiskRating.HIGH)
        else:
            # 4a. Is the report itself expired? A bridge letter is a short-term
            #     device; it cannot stand in for a report a year out of date.
            if days_since_coverage > vendor.review_cadence_days:
                findings.append(
                    f"SOC report expired: coverage ended {days_since_coverage} days ago, "
                    f"beyond the {vendor.review_cadence_days}-day review cadence. A "
                    f"management bridge letter cannot substitute for a report this stale."
                )
                if status != ComplianceStatus.ESCALATED:
                    status = ComplianceStatus.OVERDUE
                risk_rating = _raise_risk(risk_rating, RiskRating.HIGH)

            # 4b. How much time since coverage ended is covered by neither an audit
            #     report nor a bridge letter?
            valid_gap, rejections = self._find_valid_gap_letter(
                latest_soc, gap_lets, current_date
            )
            audit_trail.extend(rejections)

            covered_to = latest_soc.coverage_end
            if valid_gap is not None:
                covered_to = max(covered_to, valid_gap.period_end)
                audit_trail.append(
                    f"Valid Gap Letter {valid_gap.letter_id} bridges "
                    f"{latest_soc.coverage_end} to {valid_gap.period_end}"
                )
                # A bridge letter is management's own assertion and is not audited,
                # so relying on one never leaves the vendor at LOW risk.
                findings.append(
                    f"The period {latest_soc.coverage_end} to {valid_gap.period_end} rests "
                    f"on unaudited management bridge letter {valid_gap.letter_id}, which "
                    f"carries no audit assurance."
                )
                risk_rating = _raise_risk(risk_rating, RiskRating.MEDIUM)

            unbridged_days = (current_date - covered_to).days
            audit_trail.append(
                f"Unbridged window since {covered_to}: {unbridged_days} days "
                f"(limit {vendor.max_unbridged_gap_days})"
            )
            if unbridged_days > vendor.max_unbridged_gap_days:
                findings.append(
                    f"{unbridged_days} days since {covered_to} are covered by neither an "
                    f"audit report nor a valid bridge letter, beyond the "
                    f"{vendor.max_unbridged_gap_days}-day limit."
                )
                risk_rating = _raise_risk(risk_rating, RiskRating.MEDIUM)

        # 5. Proof of Reserves freshness. A PoR attestation never lowers risk; it is
        #    checked only because a stale or missing one is itself a finding.
        por_finding, por_floor, por_trail = self._evaluate_proof_of_reserves(
            reports, vendor, current_date
        )
        audit_trail.extend(por_trail)
        if por_finding is not None:
            findings.append(por_finding)
            risk_rating = _raise_risk(risk_rating, por_floor)

        # 6. Complementary User Entity Controls, required by any SOC type held.
        cuec_pct, cuec_findings, cuec_floor, cuec_trail = self._evaluate_cuecs(
            latest_soc, list(latest_by_type.values()), cuecs
        )
        audit_trail.extend(cuec_trail)
        findings.extend(cuec_findings)
        risk_rating = _raise_risk(risk_rating, cuec_floor)

        next_due = latest_soc.coverage_end + datetime.timedelta(
            days=vendor.review_cadence_days
        )

        logger.info(
            f"Evaluated Vendor {vendor.name}: Status={status.value}, "
            f"Risk={risk_rating.value}, CUEC={cuec_pct:.1f}%"
        )

        return ReviewResult(
            vendor_id=vendor_id,
            vendor_name=vendor.name,
            status=status,
            risk_rating=risk_rating,
            last_review_date=current_date,
            next_due_date=next_due,
            implemented_cuec_pct=cuec_pct,
            findings=findings,
            audit_trail=audit_trail,
        )

    # ------------------------------------------------------------------
    # Evaluation helpers
    # ------------------------------------------------------------------

    def _find_valid_gap_letter(
        self,
        latest_soc: AuditReport,
        gap_letters: List[GapLetter],
        current_date: datetime.date,
    ) -> Tuple[Optional[GapLetter], List[str]]:
        """
        Returns the strongest valid bridge letter for ``latest_soc``, plus an audit
        trail of why each rejected letter was rejected.

        A letter is accepted only when it bridges *this* report, is signed on or
        after the period it attests to, asserts no material changes, starts no later
        than the day after the report's coverage ends (an uncovered window is not
        bridged), and does not attest to the future. How far an accepted letter
        actually carries coverage is scored by the caller against
        ``max_unbridged_gap_days``: a letter that ran out months ago is not
        "invalid", it simply stops bridging where it ends.
        """
        rejections: List[str] = []
        candidates: List[GapLetter] = []

        for gl in gap_letters:
            reason: Optional[str] = None
            if gl.report_id != latest_soc.report_id:
                reason = f"bridges report {gl.report_id}, not {latest_soc.report_id}"
            elif not gl.no_material_changes_asserted:
                reason = "does not assert absence of material control changes"
            elif gl.signed_date is None:
                reason = "is unsigned"
            elif gl.signed_date < gl.period_end:
                reason = (
                    f"was signed {gl.signed_date}, before the end of the period it attests "
                    f"to ({gl.period_end})"
                )
            elif gl.signed_date > current_date:
                reason = f"carries a future signature date {gl.signed_date}"
            elif gl.period_start > latest_soc.coverage_end + datetime.timedelta(days=1):
                reason = (
                    f"starts {gl.period_start}, leaving an uncovered window after coverage "
                    f"end {latest_soc.coverage_end}"
                )
            elif gl.period_end > current_date:
                reason = f"attests to a future period ending {gl.period_end}"

            if reason is None:
                candidates.append(gl)
            else:
                rejections.append(f"Gap Letter {gl.letter_id} rejected: {reason}.")

        if not candidates:
            return None, rejections
        return max(candidates, key=lambda gl: (gl.period_end, gl.letter_id)), rejections

    def _evaluate_proof_of_reserves(
        self,
        reports: List[AuditReport],
        vendor: CustodyVendor,
        current_date: datetime.date,
    ) -> Tuple[Optional[str], RiskRating, List[str]]:
        """
        Checks Proof of Reserves freshness against ``por_cadence_days``.

        A PoR engagement is not an audit and provides no assurance over liabilities
        (PCAOB Investor Advisory, 2023-03-08), so a fresh attestation is never
        treated as mitigating; only a missing or stale one produces a finding.
        """
        por_reports = [r for r in reports if r.report_type == ReportType.PROOF_OF_RESERVES]

        if not por_reports:
            if vendor.requires_proof_of_reserves:
                return (
                    "No Proof of Reserves attestation on file, and the vendor is configured "
                    "to require one.",
                    RiskRating.MEDIUM,
                    ["Proof of Reserves: required by policy, none on file."],
                )
            return None, RiskRating.LOW, ["Proof of Reserves: not required for this vendor."]

        latest_por = max(
            por_reports, key=lambda r: (r.coverage_end, r.report_date, r.report_id)
        )
        age_days = (current_date - latest_por.coverage_end).days
        trail = [
            f"Latest Proof of Reserves {latest_por.report_id} as of {latest_por.coverage_end} "
            f"({age_days} days old; not an audit -- PCAOB advisory 2023-03-08)."
        ]
        if age_days > vendor.por_cadence_days:
            return (
                f"Proof of Reserves attestation is {age_days} days old, past the "
                f"{vendor.por_cadence_days}-day cadence.",
                RiskRating.MEDIUM,
                trail,
            )
        return None, RiskRating.LOW, trail

    def _evaluate_cuecs(
        self,
        latest_soc: AuditReport,
        soc_reports: List[AuditReport],
        cuecs: List[CUECCheck],
    ) -> Tuple[float, List[str], RiskRating, List[str]]:
        """
        Scores Complementary User Entity Control implementation against the union of
        the controls every current SOC report requires and the controls the firm has
        assessed.

        An unassessed CUEC is never counted as implemented: a SOC report that names
        no CUECs is a report whose CUEC section has not been captured, not a report
        that imposes none.
        """
        required_ids = [
            c
            for report in soc_reports
            for c in report.cuecs_required
            if isinstance(c, str) and c.strip()
        ]
        by_id = {c.cuec_id: c for c in cuecs}
        universe = sorted(set(required_ids) | set(by_id))

        if not universe:
            return (
                0.0,
                [
                    f"CUEC implementation has not been assessed: report "
                    f"{latest_soc.report_id} records no required Complementary User Entity "
                    f"Controls and no internal checks are on file."
                ],
                RiskRating.MEDIUM,
                ["CUEC Implementation: not assessed (0 controls recorded)."],
            )

        implemented = [cid for cid in universe if cid in by_id and by_id[cid].is_evidenced]
        unassessed = [cid for cid in universe if cid not in by_id]
        unevidenced = [
            cid
            for cid in universe
            if cid in by_id and by_id[cid].is_implemented and not by_id[cid].is_evidenced
        ]
        not_implemented = [
            cid for cid in universe if cid in by_id and not by_id[cid].is_implemented
        ]

        cuec_pct = len(implemented) / len(universe) * 100.0
        trail = [f"CUEC Implementation: {cuec_pct:.1f}% ({len(implemented)}/{len(universe)})"]

        findings: List[str] = []
        if not_implemented:
            findings.append(
                f"Unimplemented internal CUEC controls: {', '.join(not_implemented)}"
            )
        if unevidenced:
            findings.append(
                f"CUEC controls marked implemented without verification evidence: "
                f"{', '.join(unevidenced)}"
            )
        if unassessed:
            findings.append(
                f"CUEC controls required by report {latest_soc.report_id} but never assessed "
                f"internally: {', '.join(unassessed)}"
            )

        floor = RiskRating.MEDIUM if cuec_pct < 100.0 else RiskRating.LOW
        return cuec_pct, findings, floor, trail

    # ------------------------------------------------------------------
    # Portfolio views
    # ------------------------------------------------------------------

    def evaluate_all_vendors(
        self, current_date: Optional[datetime.date] = None
    ) -> List[ReviewResult]:
        """Evaluates every registered vendor, in registration order."""
        if current_date is None:
            current_date = datetime.date.today()
        return [
            self.evaluate_vendor_compliance(vendor_id, current_date)
            for vendor_id in self.vendors
        ]

    def get_overdue_vendors(
        self, current_date: Optional[datetime.date] = None
    ) -> List[CustodyVendor]:
        """
        Returns vendors whose audit reviews are overdue or which have no acceptable
        audit evidence on file.

        A vendor whose auditor gave a qualified, adverse or disclaimed opinion is
        ``ESCALATED`` rather than ``OVERDUE`` and is therefore *not* returned here:
        use :meth:`get_vendors_requiring_escalation` for the Risk Committee view.
        """
        overdue_statuses = (ComplianceStatus.OVERDUE, ComplianceStatus.NON_COMPLIANT)
        return [
            self.vendors[res.vendor_id]
            for res in self.evaluate_all_vendors(current_date)
            if res.status in overdue_statuses
        ]

    def get_vendors_requiring_escalation(
        self, current_date: Optional[datetime.date] = None
    ) -> List[ReviewResult]:
        """
        Returns the review results for every vendor rated ``CRITICAL`` or with status
        ``ESCALATED`` -- the population the risk escalation matrix sends to the Risk
        Committee with new capital allocation frozen.
        """
        return [
            res
            for res in self.evaluate_all_vendors(current_date)
            if res.status == ComplianceStatus.ESCALATED
            or res.risk_rating == RiskRating.CRITICAL
        ]
