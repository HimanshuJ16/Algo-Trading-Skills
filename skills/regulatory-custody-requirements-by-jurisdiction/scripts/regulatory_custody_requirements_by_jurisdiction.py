import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Set

logger = logging.getLogger(__name__)

@dataclass
class Result:
    """Legacy Result class for backward compatibility."""
    success: bool
    message: str

@dataclass
class CustodyRuleSpec:
    jurisdiction: str                    # e.g. 'US', 'EU', 'UK', 'SG'
    regulator: str                       # e.g. 'SEC', 'ESMA_MiCA', 'FCA_CASS', 'MAS'
    requires_qualified_custodian: bool
    requires_asset_segregation: bool
    requires_annual_audit: bool
    requires_insurance: bool = False

DEFAULT_CUSTODY_RULES: Dict[str, CustodyRuleSpec] = {
    "US": CustodyRuleSpec("US", "SEC", requires_qualified_custodian=True, requires_asset_segregation=True, requires_annual_audit=True, requires_insurance=False),
    "EU": CustodyRuleSpec("EU", "ESMA_MiCA", requires_qualified_custodian=True, requires_asset_segregation=True, requires_annual_audit=True, requires_insurance=True),
    "UK": CustodyRuleSpec("UK", "FCA_CASS", requires_qualified_custodian=True, requires_asset_segregation=True, requires_annual_audit=True, requires_insurance=False),
    "SG": CustodyRuleSpec("SG", "MAS", requires_qualified_custodian=True, requires_asset_segregation=True, requires_annual_audit=True, requires_insurance=True),
}

@dataclass
class CustodySetup:
    jurisdiction: str
    custodian_name: str
    custody_type: str                    # 'QUALIFIED_CUSTODIAN', 'SELF_CUSTODY', 'EXCHANGE_CUSTODY'
    is_asset_segregated: bool
    has_annual_audit: bool
    has_insurance_coverage: bool = False

@dataclass
class CustodyViolation:
    jurisdiction: str
    violation_type: str
    detail: str

@dataclass
class JurisdictionalCustodyAuditReport:
    jurisdiction: str
    is_compliant: bool
    violations: List[CustodyViolation]
    status: str                          # 'CUSTODY_COMPLIANT', 'CUSTODY_VIOLATION', 'UNKNOWN_JURISDICTION'
    audit_notes: str

class Analyzer:
    """Legacy Analyzer class for backward compatibility."""
    def __init__(self, config: dict):
        self.config = config

    def execute(self) -> Result:
        if not self.config:
            return Result(False, "No config provided")
        return Result(True, "Success")

class RegulatoryCustodyRequirementsByJurisdictionEngine:
    """
    Engine for evaluating crypto and asset custody arrangements against jurisdictional
    regulatory standards (SEC Custody Rule, MiCA asset segregation, FCA CASS, MAS rules).
    """
    def __init__(self, custom_rules: Optional[Dict[str, CustodyRuleSpec]] = None):
        self.rules = dict(DEFAULT_CUSTODY_RULES)
        if custom_rules:
            self.rules.update(custom_rules)

    def audit_custody_setup(self, setup: CustodySetup) -> JurisdictionalCustodyAuditReport:
        """
        Audits a custody arrangement against the target jurisdiction's regulatory requirements.
        """
        jur_upper = setup.jurisdiction.upper()
        rule = self.rules.get(jur_upper)

        if not rule:
            return JurisdictionalCustodyAuditReport(
                jurisdiction=setup.jurisdiction,
                is_compliant=False,
                violations=[CustodyViolation(setup.jurisdiction, "UNSUPPORTED_JURISDICTION", f"No regulatory rules registered for jurisdiction '{setup.jurisdiction}'")],
                status="UNKNOWN_JURISDICTION",
                audit_notes=f"Unknown jurisdiction: '{setup.jurisdiction}'."
            )

        violations: List[CustodyViolation] = []

        if rule.requires_qualified_custodian and setup.custody_type.upper() != "QUALIFIED_CUSTODIAN":
            violations.append(CustodyViolation(
                jur_upper, "UNQUALIFIED_CUSTODIAN",
                f"Jurisdiction {jur_upper} requires a QUALIFIED_CUSTODIAN; got {setup.custody_type}."
            ))

        if rule.requires_asset_segregation and not setup.is_asset_segregated:
            violations.append(CustodyViolation(
                jur_upper, "COMMINGLED_ASSETS",
                f"Jurisdiction {jur_upper} requires strict client asset segregation."
            ))

        if rule.requires_annual_audit and not setup.has_annual_audit:
            violations.append(CustodyViolation(
                jur_upper, "MISSING_ANNUAL_AUDIT",
                f"Jurisdiction {jur_upper} mandates an annual independent custody audit."
            ))

        if rule.requires_insurance and not setup.has_insurance_coverage:
            violations.append(CustodyViolation(
                jur_upper, "MISSING_INSURANCE",
                f"Jurisdiction {jur_upper} requires insurance coverage for custodied assets."
            ))

        is_compliant = len(violations) == 0
        status = "CUSTODY_COMPLIANT" if is_compliant else "CUSTODY_VIOLATION"

        notes = (
            f"REGULATORY CUSTODY AUDIT [{status}] ({jur_upper}): "
            f"Custodian = '{setup.custodian_name}', Violations = {len(violations)}."
        )

        if not is_compliant:
            logger.warning(notes)
        else:
            logger.info(notes)

        return JurisdictionalCustodyAuditReport(
            jurisdiction=jur_upper,
            is_compliant=is_compliant,
            violations=violations,
            status=status,
            audit_notes=notes
        )
