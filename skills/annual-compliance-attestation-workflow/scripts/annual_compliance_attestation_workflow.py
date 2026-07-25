"""
annual-compliance-attestation-workflow:
Evaluates hedge fund / broker-dealer compliance against SEC Rule 206(4)-7 and FINRA Rule 3130.
"""
import dataclasses
import logging
from typing import List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class AnnualComplianceChecklist:
    reporting_year: int
    is_broker_dealer: bool
    
    # SEC Rule 206(4)-7 (Investment Advisers)
    annual_policy_review_date: Optional[datetime] = None
    cco_report_submitted_date: Optional[datetime] = None
    
    # FINRA Rule 3130 (Broker-Dealers only)
    ceo_cco_meeting_date: Optional[datetime] = None
    ceo_certification_signed: bool = False
    
    # Quantitative Fund Specifics
    algo_code_integrity_review_date: Optional[datetime] = None
    trade_surveillance_test_date: Optional[datetime] = None


@dataclasses.dataclass
class AttestationReport:
    is_ready_for_attestation: bool
    missing_requirements: List[str]


class AnnualComplianceAttestationEngine:
    """
    Validates that a quantitative trading firm has met all prerequisites
    to sign their annual compliance attestation.
    """

    def evaluate(self, checklist: AnnualComplianceChecklist) -> AttestationReport:
        missing = []
        year = checklist.reporting_year

        # 1. SEC Rule 206(4)-7 Checks (Applies to all RIAs)
        if not checklist.annual_policy_review_date or checklist.annual_policy_review_date.year != year:
            missing.append("SEC Rule 206(4)-7: Annual review of written policies was not completed this year.")
            
        if not checklist.cco_report_submitted_date or checklist.cco_report_submitted_date.year != year:
            missing.append("SEC Rule 206(4)-7: CCO annual report was not submitted this year.")

        # 2. Quant Fund Specific Checks (SEC Exam Priorities)
        if not checklist.algo_code_integrity_review_date or checklist.algo_code_integrity_review_date.year != year:
            missing.append("Quant Control: Annual algorithmic code integrity risk review is missing.")
            
        if not checklist.trade_surveillance_test_date or checklist.trade_surveillance_test_date.year != year:
            missing.append("Quant Control: Annual trade surveillance systems test is missing.")

        # 3. FINRA Rule 3130 Checks (Only if registered as Broker-Dealer)
        if checklist.is_broker_dealer:
            if not checklist.ceo_cco_meeting_date or checklist.ceo_cco_meeting_date.year != year:
                missing.append("FINRA Rule 3130: Mandatory CEO-CCO compliance meeting did not occur this year.")
                
            if not checklist.ceo_certification_signed:
                missing.append("FINRA Rule 3130: CEO has not signed the annual compliance certification.")

        is_ready = len(missing) == 0
        
        if is_ready:
            logger.info(f"Year {year} Attestation ready. All regulatory controls passed.")
        else:
            logger.error(f"Year {year} Attestation blocked. Missing requirements: {missing}")

        return AttestationReport(
            is_ready_for_attestation=is_ready,
            missing_requirements=missing
        )
