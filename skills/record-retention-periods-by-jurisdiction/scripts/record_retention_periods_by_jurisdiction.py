import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

# Default jurisdictional minimum retention periods (years)
DEFAULT_RETENTION_RULES: Dict[str, int] = {
    "US": 7,    # SEC Rule 17a-4
    "UK": 5,    # FCA SYSC 9
    "SG": 5,    # MAS
    "AU": 7,    # ASIC
    "IN": 8,    # SEBI
    "EU": 5,    # MiFID II
}

@dataclass
class ComplianceResult:
    is_compliant: bool
    reason: str

@dataclass
class RetentionRecord:
    record_id: str
    record_type: str                     # 'TRADE', 'COMMUNICATION', 'ORDER_AUDIT'
    jurisdiction: str                    # e.g. 'US', 'UK', 'SG'
    creation_date: str                   # ISO format e.g. '2018-03-15'
    current_retention_years: float       # How many years the record has been / will be retained

@dataclass
class RetentionCheckResult:
    record_id: str
    jurisdiction: str
    min_required_years: int
    current_retention_years: float
    years_surplus_or_deficit: float
    is_compliant: bool
    status: str                          # 'COMPLIANT', 'NON_COMPLIANT', 'UNKNOWN_JURISDICTION'

@dataclass
class RetentionComplianceReport:
    total_records: int
    compliant_count: int
    non_compliant_count: int
    unknown_jurisdiction_count: int
    results: List[RetentionCheckResult]
    overall_status: str                  # 'ALL_COMPLIANT', 'ISSUES_FOUND'
    audit_notes: str

class RecordRetentionPeriodsByJurisdictionEngine:
    """
    Multi-jurisdiction trade record retention compliance engine enforcing minimum
    document retention periods per regulatory authority.
    """
    def __init__(self, custom_rules: Optional[Dict[str, int]] = None):
        self.retention_rules = dict(DEFAULT_RETENTION_RULES)
        if custom_rules:
            self.retention_rules.update(custom_rules)

    def check(self, data: dict) -> ComplianceResult:
        """Legacy check method retained for backward compatibility."""
        if data.get("valid"):
            return ComplianceResult(True, "Valid")
        return ComplianceResult(False, "Invalid")

    def validate_retention(self, records: List[RetentionRecord]) -> RetentionComplianceReport:
        """
        Validates each record's retention period against jurisdictional minimums.
        """
        results: List[RetentionCheckResult] = []
        compliant = 0
        non_compliant = 0
        unknown = 0

        for rec in records:
            jur_upper = rec.jurisdiction.upper()
            min_req = self.retention_rules.get(jur_upper)

            if min_req is None:
                unknown += 1
                results.append(RetentionCheckResult(
                    record_id=rec.record_id,
                    jurisdiction=rec.jurisdiction,
                    min_required_years=0,
                    current_retention_years=rec.current_retention_years,
                    years_surplus_or_deficit=0.0,
                    is_compliant=False,
                    status="UNKNOWN_JURISDICTION"
                ))
                continue

            surplus = rec.current_retention_years - min_req
            is_ok = surplus >= 0.0

            if is_ok:
                compliant += 1
            else:
                non_compliant += 1

            results.append(RetentionCheckResult(
                record_id=rec.record_id,
                jurisdiction=rec.jurisdiction,
                min_required_years=min_req,
                current_retention_years=rec.current_retention_years,
                years_surplus_or_deficit=round(surplus, 1),
                is_compliant=is_ok,
                status="COMPLIANT" if is_ok else "NON_COMPLIANT"
            ))

        total = len(records)
        has_issues = non_compliant > 0 or unknown > 0
        overall = "ISSUES_FOUND" if has_issues else "ALL_COMPLIANT"

        notes = (
            f"RETENTION COMPLIANCE [{overall}]: Total = {total}, "
            f"Compliant = {compliant}, Non-Compliant = {non_compliant}, "
            f"Unknown Jurisdiction = {unknown}."
        )

        if has_issues:
            logger.warning(notes)
        else:
            logger.info(notes)

        return RetentionComplianceReport(
            total_records=total,
            compliant_count=compliant,
            non_compliant_count=non_compliant,
            unknown_jurisdiction_count=unknown,
            results=results,
            overall_status=overall,
            audit_notes=notes
        )
