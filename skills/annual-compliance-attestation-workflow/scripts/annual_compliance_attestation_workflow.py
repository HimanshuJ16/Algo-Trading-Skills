"""
annual-compliance-attestation-workflow:
Evaluates hedge fund / broker-dealer compliance against SEC Rule 206(4)-7,
FINRA Rule 3130, FINRA Rule 3120, and SEC Rule 15c3-5 annual obligations.
"""
import dataclasses
import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import List, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "AnnualComplianceChecklist",
    "AttestationReport",
    "AnnualComplianceAttestationEngine",
]


# Stable, machine-readable requirement codes (parallel to human-readable messages).
CODE_SEC_206_4_7_POLICY_REVIEW = "REQ_SEC_206_4_7_POLICY_REVIEW"
CODE_SEC_15C3_5_ANNUAL_REVIEW = "REQ_SEC_15C3_5_ANNUAL_REVIEW"
CODE_SEC_15C3_5_CEO_CERT = "REQ_SEC_15C3_5_CEO_CERT"
CODE_FINRA_3130_CEO_CCO_MEETING = "REQ_FINRA_3130_CEO_CCO_MEETING"
CODE_FINRA_3130_CEO_CERT = "REQ_FINRA_3130_CEO_CERT"
CODE_FINRA_3130_MEETING_ORDER = "REQ_FINRA_3130_MEETING_PRECEDES_CERT"
CODE_FINRA_3130_BOARD_SUBMISSION = "REQ_FINRA_3130_04_BOARD_SUBMISSION"
CODE_FINRA_3120_REPORT = "REQ_FINRA_3120_REPORT"
CODE_QUANT_ALGO_CODE_REVIEW = "REQ_QUANT_ALGO_CODE_INTEGRITY_REVIEW"
CODE_QUANT_TRADE_SURVEILLANCE = "REQ_QUANT_TRADE_SURVEILLANCE_TEST"

# FINRA 3130.04: board/audit-committee submission no later than 45 days after
# the certification signing date (or the next scheduled meeting, whichever is earlier).
BOARD_SUBMISSION_MAX_DAYS = 45


@dataclasses.dataclass(frozen=True)
class AnnualComplianceChecklist:
    """
    Audit evidence for ONE legal entity's annual compliance attestation.

    A single instance corresponds to one legal entity (e.g., the RIA vs. its
    BD affiliate have separate certifications, anniversaries, and obligations).
    A typical quant fund structure (RIA + BD affiliate + offshore feeder)
    requires one checklist per entity.
    """

    reporting_year: int
    is_broker_dealer: bool
    legal_entity_id: str

    # SEC Rule 206(4)-7 (Investment Advisers) — annual REVIEW of written
    # policies/procedures (record per Rule 204-2(a)(16)(ii)). Note: 206(4)-7
    # mandates an annual review, not a standalone "CCO annual report."
    annual_policy_review_date: Optional[datetime] = None
    annual_review_documentation_date: Optional[datetime] = None

    # FINRA Rule 3130 (Broker-Dealers only)
    ceo_cco_meeting_date: Optional[datetime] = None
    ceo_certification_signed_date: Optional[datetime] = None
    prior_certification_date: Optional[datetime] = None
    certification_signing_date: Optional[datetime] = None
    # FINRA 3130.04 — board / audit-committee submission
    board_submission_date: Optional[datetime] = None
    audit_committee_acknowledgment_date: Optional[datetime] = None

    # FINRA Rule 3120 — supervisory-controls annual report (3130 prerequisite)
    rule_3120_report_date: Optional[datetime] = None

    # SEC Rule 15c3-5 — annual review + CEO certification of pre-trade risk
    # controls (BDs with market access).
    rule_15c3_5_annual_review_date: Optional[datetime] = None
    rule_15c3_5_ceo_certification_date: Optional[datetime] = None

    # Quantitative Fund Specifics (SEC Exam Priorities)
    algo_code_integrity_review_date: Optional[datetime] = None
    trade_surveillance_test_date: Optional[datetime] = None

    def __post_init__(self) -> None:
        if not isinstance(self.reporting_year, int) or isinstance(self.reporting_year, bool):
            raise ValueError(
                f"reporting_year must be an int in [2000, 2100]; got {self.reporting_year!r}"
            )
        if self.reporting_year < 2000 or self.reporting_year > 2100:
            raise ValueError(
                f"reporting_year must be in [2000, 2100]; got {self.reporting_year}"
            )
        if not self.legal_entity_id or not isinstance(self.legal_entity_id, str):
            raise ValueError("legal_entity_id must be a non-empty string")


@dataclasses.dataclass(frozen=True)
class AttestationReport:
    """Tamper-evident sealed verdict over the evaluated checklist."""

    is_ready_for_attestation: bool
    missing_requirements: List[str]
    missing_requirement_codes: List[str]
    generated_at: datetime
    content_hash: str


class AnnualComplianceAttestationEngine:
    """
    Validates that a quantitative trading firm has met all prerequisites
    to sign its annual compliance attestation.
    """

    def evaluate(self, checklist: AnnualComplianceChecklist) -> AttestationReport:
        missing: List[str] = []
        codes: List[str] = []
        year = checklist.reporting_year

        def add(code: str, message: str) -> None:
            codes.append(code)
            missing.append(message)

        # 1. SEC Rule 206(4)-7 Checks (Applies to all RIAs)
        if (
            checklist.annual_policy_review_date is None
            or checklist.annual_policy_review_date.year != year
        ):
            add(
                CODE_SEC_206_4_7_POLICY_REVIEW,
                "SEC Rule 206(4)-7: Annual review of written policies and "
                "procedures was not completed this year.",
            )

        if (
            checklist.annual_review_documentation_date is None
            or checklist.annual_review_documentation_date.year != year
        ):
            add(
                CODE_SEC_206_4_7_POLICY_REVIEW,
                "SEC Rule 206(4)-7: Annual review of written policies and "
                "procedures was not documented this year.",
            )

        # 2. Quant Fund Specific Checks (SEC Exam Priorities)
        if (
            checklist.algo_code_integrity_review_date is None
            or checklist.algo_code_integrity_review_date.year != year
        ):
            add(
                CODE_QUANT_ALGO_CODE_REVIEW,
                "Quant Control: Annual algorithmic code integrity risk review is missing.",
            )

        if (
            checklist.trade_surveillance_test_date is None
            or checklist.trade_surveillance_test_date.year != year
        ):
            add(
                CODE_QUANT_TRADE_SURVEILLANCE,
                "Quant Control: Annual trade surveillance systems test is missing.",
            )

        # 3. FINRA Rule 3130 Checks (Only if registered as Broker-Dealer)
        if checklist.is_broker_dealer:
            self._evaluate_finra_3130(checklist, year, add)
            self._evaluate_finra_3120(checklist, year, add)
            self._evaluate_sec_15c3_5(checklist, year, add)

        is_ready = len(missing) == 0
        generated_at = datetime.utcnow()
        content_hash = self._compute_content_hash(
            checklist, is_ready, missing, codes, generated_at
        )

        if is_ready:
            logger.info(
                "Year %s Attestation ready. All regulatory controls passed.",
                year,
            )
        else:
            logger.warning(
                "Year %s Attestation blocked. Missing requirements: %s",
                year,
                missing,
            )
        logger.info("Attestation content_hash: %s", content_hash)

        return AttestationReport(
            is_ready_for_attestation=is_ready,
            missing_requirements=missing,
            missing_requirement_codes=codes,
            generated_at=generated_at,
            content_hash=content_hash,
        )

    # --- BD-gated rule helpers ---------------------------------------------

    def _evaluate_finra_3130(
        self,
        checklist: AnnualComplianceChecklist,
        year: int,
        add,
    ) -> None:
        # FINRA 3130(c)(2): the CEO-CCO meeting must occur at least once
        # during the 12-month period preceding the certification signing date
        # (or, if signing date is absent, within 12 months of evaluation).
        # No later than the anniversary date of the prior certification.
        signing_ref = (
            checklist.certification_signing_date
            if checklist.certification_signing_date is not None
            else datetime.utcnow()
        )
        window_start = signing_ref - timedelta(days=365)

        if checklist.ceo_cco_meeting_date is None:
            add(
                CODE_FINRA_3130_CEO_CCO_MEETING,
                "FINRA Rule 3130: Mandatory CEO-CCO compliance meeting did not "
                "occur within the rolling 12-month window preceding certification.",
            )
        else:
            meeting = checklist.ceo_cco_meeting_date
            if meeting < window_start or meeting > signing_ref:
                add(
                    CODE_FINRA_3130_CEO_CCO_MEETING,
                    "FINRA Rule 3130: CEO-CCO compliance meeting did not occur "
                    "within the rolling 12-month window preceding certification.",
                )
            elif (
                checklist.prior_certification_date is not None
                and meeting > checklist.prior_certification_date + timedelta(days=365)
            ):
                # No later than the anniversary of the prior certification.
                add(
                    CODE_FINRA_3130_CEO_CCO_MEETING,
                    "FINRA Rule 3130: CEO-CCO meeting post-dates the anniversary "
                    "of the prior certification.",
                )

        # CEO certification must be signed (timestamped).
        if (
            checklist.ceo_certification_signed_date is None
            or checklist.ceo_certification_signed_date.year != year
        ):
            add(
                CODE_FINRA_3130_CEO_CERT,
                "FINRA Rule 3130: CEO has not signed the annual compliance "
                "certification this year.",
            )

        # Temporal ordering: the CEO-CCO meeting must precede the signing of
        # the CEO certification (prevents "rubber-stamping").
        if (
            checklist.ceo_cco_meeting_date is not None
            and checklist.ceo_certification_signed_date is not None
            and checklist.ceo_cco_meeting_date > checklist.ceo_certification_signed_date
        ):
            add(
                CODE_FINRA_3130_MEETING_ORDER,
                "FINRA Rule 3130: CEO-CCO meeting post-dates the CEO "
                "certification signature (rubber-stamping risk).",
            )

        # FINRA 3130.04 — board/audit-committee submission no later than 45
        # days after the certification signing date (or next scheduled
        # meeting, whichever is earlier).
        cert_date = checklist.ceo_certification_signed_date
        if cert_date is not None:
            deadline = cert_date + timedelta(days=BOARD_SUBMISSION_MAX_DAYS)
            if checklist.board_submission_date is None:
                add(
                    CODE_FINRA_3130_BOARD_SUBMISSION,
                    "FINRA Rule 3130.04: Certification was not submitted to the "
                    "board/audit-committee within 45 days of signing.",
                )
            elif checklist.board_submission_date > deadline:
                add(
                    CODE_FINRA_3130_BOARD_SUBMISSION,
                    "FINRA Rule 3130.04: Board/audit-committee submission "
                    "exceeded the 45-day deadline after certification signing.",
                )

    def _evaluate_finra_3120(
        self,
        checklist: AnnualComplianceChecklist,
        year: int,
        add,
    ) -> None:
        # FINRA Rule 3120: designated principals must test/verify WSPs and
        # submit a Rule 3120 Report to senior management no less than annually.
        # 3120 results are a substantive prerequisite input to a 3130 cert.
        signing_ref = (
            checklist.certification_signing_date
            if checklist.certification_signing_date is not None
            else datetime.utcnow()
        )
        window_start = signing_ref - timedelta(days=365)
        if (
            checklist.rule_3120_report_date is None
            or checklist.rule_3120_report_date < window_start
            or checklist.rule_3120_report_date > signing_ref
        ):
            add(
                CODE_FINRA_3120_REPORT,
                "FINRA Rule 3120: Supervisory-controls annual report was not "
                "completed within the rolling 12-month window preceding certification.",
            )

    def _evaluate_sec_15c3_5(
        self,
        checklist: AnnualComplianceChecklist,
        year: int,
        add,
    ) -> None:
        # SEC Rule 15c3-5(d)(2)/(e): BDs with market access must annually
        # review pre-trade risk controls and the CEO must separately certify
        # that those controls comply.
        if (
            checklist.rule_15c3_5_annual_review_date is None
            or checklist.rule_15c3_5_annual_review_date.year != year
        ):
            add(
                CODE_SEC_15C3_5_ANNUAL_REVIEW,
                "SEC Rule 15c3-5: Annual review of pre-trade risk controls "
                "was not completed this year.",
            )
        if (
            checklist.rule_15c3_5_ceo_certification_date is None
            or checklist.rule_15c3_5_ceo_certification_date.year != year
        ):
            add(
                CODE_SEC_15C3_5_CEO_CERT,
                "SEC Rule 15c3-5: CEO certification of pre-trade risk-control "
                "compliance was not signed this year.",
            )

    # --- Sealing ------------------------------------------------------------

    @staticmethod
    def _compute_content_hash(
        checklist: AnnualComplianceChecklist,
        is_ready: bool,
        missing: List[str],
        codes: List[str],
        generated_at: datetime,
    ) -> str:
        payload = {
            "legal_entity_id": checklist.legal_entity_id,
            "reporting_year": checklist.reporting_year,
            "is_broker_dealer": checklist.is_broker_dealer,
            "is_ready_for_attestation": is_ready,
            "missing_requirements": missing,
            "missing_requirement_codes": codes,
            "generated_at": generated_at.isoformat(),
        }
        serialized = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
