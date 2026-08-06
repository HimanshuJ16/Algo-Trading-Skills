import logging
import datetime
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Set
from enum import Enum

logger = logging.getLogger(__name__)


class ReportType(Enum):
    SOC1_TYPE2 = "SOC1_TYPE2"            # Financial controls (SSAE 18 / ISAE 3402)
    SOC2_TYPE2 = "SOC2_TYPE2"            # Security, Availability & Confidentiality
    PROOF_OF_RESERVES = "PROOF_OF_RESERVES"# On-chain crypto reserve attestation
    ISO27001 = "ISO27001"                # Information Security Management System
    FINANCIAL_AUDIT = "FINANCIAL_AUDIT"   # Audited financial statements


class AuditOpinion(Enum):
    UNQUALIFIED = "UNQUALIFIED"  # Clean opinion (Passed)
    QUALIFIED = "QUALIFIED"      # Passed with exceptions/qualifications
    ADVERSE = "ADVERSE"          # Failed controls
    DISCLAIMER = "DISCLAIMER"    # Auditor unable to form opinion


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


class CustodyAuditError(Exception):
    """Base exception for custody audit review errors."""
    pass


class OverdueAuditError(CustodyAuditError):
    """Raised when an audit report or review cadence is past due."""
    pass


class QualifiedOpinionError(CustodyAuditError):
    """Raised when an audit opinion is qualified or adverse."""
    pass


@dataclass
class CustodyVendor:
    vendor_id: str
    name: str  # e.g., 'Coinbase Custody', 'BitGo', 'Fireblocks'
    asset_classes_held: List[str]  # e.g., ['BTC', 'ETH', 'USD']
    total_aum_usd: float
    review_cadence_days: int = 365  # Annual review default
    por_cadence_days: int = 90      # Quarterly Proof of Reserves default


@dataclass
class AuditReport:
    report_id: str
    vendor_id: str
    report_type: ReportType
    opinion: AuditOpinion
    coverage_start: datetime.date
    coverage_end: datetime.date
    report_date: datetime.date
    deficiencies_found: int = 0
    cuecs_required: List[str] = field(default_factory=list)  # Complementary User Entity Controls


@dataclass
class GapLetter:
    letter_id: str
    vendor_id: str
    report_id: str
    period_start: datetime.date
    period_end: datetime.date
    no_material_changes_asserted: bool = True
    signed_date: Optional[datetime.date] = None


@dataclass
class CUECCheck:
    cuec_id: str
    description: str
    is_implemented: bool
    verification_evidence: str


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
    Institutional Audit Review Cadence Engine for Third-Party Custodians.
    
    Tracks vendor audit report releases (SOC 1/2 Type II, Proof of Reserves),
    validates auditor opinions, monitors bridge/gap letter coverage, verifies
    Complementary User Entity Controls (CUECs), and enforces risk escalation.
    """

    def __init__(self):
        self.vendors: Dict[str, CustodyVendor] = {}
        self.audit_reports: Dict[str, List[AuditReport]] = {}
        self.gap_letters: Dict[str, List[GapLetter]] = {}
        self.cuec_status: Dict[str, List[CUECCheck]] = {}
        self.last_reviews: Dict[str, datetime.date] = {}
        logger.info("Initialized Custody Audit Review Cadence Engine")

    def register_vendor(self, vendor: CustodyVendor) -> None:
        """Registers a third-party custody vendor for audit monitoring."""
        self.vendors[vendor.vendor_id] = vendor
        self.audit_reports[vendor.vendor_id] = []
        self.gap_letters[vendor.vendor_id] = []
        self.cuec_status[vendor.vendor_id] = []
        logger.info(f"Registered Custody Vendor: {vendor.name} (AUM: ${vendor.total_aum_usd:,.2f} USD)")

    def submit_audit_report(self, report: AuditReport) -> None:
        """Submits a newly received audit report for a registered vendor."""
        if report.vendor_id not in self.vendors:
            raise CustodyAuditError(f"Vendor ID {report.vendor_id} is not registered.")

        # Ensure coverage is at least 6 months for Type II reports
        if report.report_type in (ReportType.SOC1_TYPE2, ReportType.SOC2_TYPE2):
            coverage_days = (report.coverage_end - report.coverage_start).days
            if coverage_days < 180:
                logger.warning(
                    f"Report {report.report_id} coverage ({coverage_days} days) is less than 6 months for Type II."
                )

        self.audit_reports[report.vendor_id].append(report)
        logger.info(
            f"Submitted {report.report_type.value} report for {report.vendor_id} "
            f"(Opinion: {report.opinion.value}, Deficiencies: {report.deficiencies_found})"
        )

    def submit_gap_letter(self, gap_letter: GapLetter) -> None:
        """Submits a bridge/gap letter asserting no material changes during gap period."""
        if gap_letter.vendor_id not in self.vendors:
            raise CustodyAuditError(f"Vendor ID {gap_letter.vendor_id} is not registered.")

        if not gap_letter.no_material_changes_asserted:
            logger.error(f"Gap letter {gap_letter.letter_id} indicates material control changes!")

        self.gap_letters[gap_letter.vendor_id].append(gap_letter)
        logger.info(f"Submitted Gap Letter for vendor {gap_letter.vendor_id} (Period: {gap_letter.period_start} to {gap_letter.period_end})")

    def update_cuec_checks(self, vendor_id: str, cuec_checks: List[CUECCheck]) -> None:
        """Updates internal implementation verification of Complementary User Entity Controls."""
        if vendor_id not in self.vendors:
            raise CustodyAuditError(f"Vendor ID {vendor_id} is not registered.")

        self.cuec_status[vendor_id] = cuec_checks
        implemented = sum(1 for c in cuec_checks if c.is_implemented)
        logger.info(f"Updated CUEC status for {vendor_id}: {implemented}/{len(cuec_checks)} implemented.")

    def evaluate_vendor_compliance(
        self, vendor_id: str, current_date: Optional[datetime.date] = None
    ) -> ReviewResult:
        """
        Evaluates current audit review status, gap coverage, CUEC implementation,
        and assigns risk ratings and next review due dates.
        """
        if current_date is None:
            current_date = datetime.date.today()

        if vendor_id not in self.vendors:
            raise CustodyAuditError(f"Vendor ID {vendor_id} is not registered.")

        vendor = self.vendors[vendor_id]
        reports = self.audit_reports.get(vendor_id, [])
        gap_lets = self.gap_letters.get(vendor_id, [])
        cuecs = self.cuec_status.get(vendor_id, [])

        audit_trail = [f"Evaluating custody audit compliance for {vendor.name} on {current_date}"]
        findings = []
        risk_rating = RiskRating.LOW
        status = ComplianceStatus.COMPLIANT

        # 1. Check if any audit reports exist
        if not reports:
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

        # Get latest SOC 1 / SOC 2 report
        latest_soc = max(
            [r for r in reports if r.report_type in (ReportType.SOC1_TYPE2, ReportType.SOC2_TYPE2)],
            key=lambda r: r.coverage_end,
            default=reports[-1],
        )

        audit_trail.append(
            f"Latest SOC Report: {latest_soc.report_id} ({latest_soc.report_type.value}), "
            f"Coverage End: {latest_soc.coverage_end}, Opinion: {latest_soc.opinion.value}"
        )

        # 2. Opinion Check
        if latest_soc.opinion in (AuditOpinion.QUALIFIED, AuditOpinion.ADVERSE, AuditOpinion.DISCLAIMER):
            findings.append(f"Audit opinion is {latest_soc.opinion.value}. Control deficiency flagged by auditor.")
            risk_rating = RiskRating.CRITICAL
            status = ComplianceStatus.ESCALATED

        if latest_soc.deficiencies_found > 0:
            findings.append(f"Auditor reported {latest_soc.deficiencies_found} control deficiencies.")
            if risk_rating != RiskRating.CRITICAL:
                risk_rating = RiskRating.HIGH

        # 3. Check Cadence / Expired Report Coverage
        days_since_coverage = (current_date - latest_soc.coverage_end).days
        audit_trail.append(f"Days elapsed since SOC coverage end: {days_since_coverage} days")

        if days_since_coverage > 365:
            # Check if valid Gap Letter covers the gap period
            valid_gap = False
            for gl in gap_lets:
                if gl.period_start <= latest_soc.coverage_end and gl.no_material_changes_asserted:
                    gap_age = (current_date - gl.period_end).days
                    if gap_age <= 180:  # Gap letter valid up to 180 days
                        valid_gap = True
                        audit_trail.append(f"Valid Gap Letter {gl.letter_id} on file covering up to {gl.period_end}")
                        break

            if not valid_gap:
                findings.append(f"SOC report expired ({days_since_coverage} days ago) and no valid Gap Letter on file.")
                if status != ComplianceStatus.ESCALATED:
                    status = ComplianceStatus.OVERDUE
                if risk_rating in (RiskRating.LOW, RiskRating.MEDIUM):
                    risk_rating = RiskRating.HIGH

        # 4. Complementary User Entity Controls (CUEC) Implementation Check
        impl_cuecs = sum(1 for c in cuecs if c.is_implemented)
        cuec_pct = (impl_cuecs / len(cuecs) * 100.0) if cuecs else 100.0
        audit_trail.append(f"CUEC Implementation: {cuec_pct:.1f}% ({impl_cuecs}/{len(cuecs)})")

        if cuec_pct < 100.0:
            missing_cuecs = [c.cuec_id for c in cuecs if not c.is_implemented]
            findings.append(f"Unimplemented internal CUEC controls: {', '.join(missing_cuecs)}")
            if risk_rating == RiskRating.LOW:
                risk_rating = RiskRating.MEDIUM

        # Determine next review due date
        next_due = latest_soc.coverage_end + datetime.timedelta(days=vendor.review_cadence_days)
        self.last_reviews[vendor_id] = current_date

        logger.info(
            f"Evaluated Vendor {vendor.name}: Status={status.value}, Risk={risk_rating.value}, CUEC={cuec_pct:.1f}%"
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

    def get_overdue_vendors(self, current_date: Optional[datetime.date] = None) -> List[CustodyVendor]:
        """Returns list of custody vendors whose audit reviews are overdue."""
        if current_date is None:
            current_date = datetime.date.today()

        overdue = []
        for vendor_id, vendor in self.vendors.items():
            res = self.evaluate_vendor_compliance(vendor_id, current_date)
            if res.status in (ComplianceStatus.OVERDUE, ComplianceStatus.NON_COMPLIANT):
                overdue.append(vendor)
        return overdue

