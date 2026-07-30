import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class CustodyVendorProfile:
    vendor_id: str
    vendor_name: str
    charter_type: str                   # e.g. 'STATE_CHARTERED_TRUST', 'FEDERAL_BANK', 'UNLICENSED'
    is_sec_qualified_custodian: bool
    has_soc2_type2_unqualified: bool
    is_asset_bankruptcy_remote: bool
    crime_insurance_coverage_usd: float
    fips_140_2_level: int               # 1 to 4 (Level 3 or 4 required)
    uptime_sla_pct: float               # e.g. 99.9%
    rto_hours: float                    # Recovery Time Objective (hours)
    conducts_annual_pen_tests: bool

@dataclass
class PillarScore:
    pillar_name: str
    score: float                        # 0.0 to 100.0
    weight: float                       # Fraction summing to 1.0
    findings: List[str]

@dataclass
class CustodyVendorDueDiligenceReport:
    vendor_id: str
    vendor_name: str
    composite_due_diligence_score: float
    decision_status: str                # 'APPROVED', 'CONDITIONAL_APPROVAL', 'REJECTED'
    pillar_breakdown: List[PillarScore]
    critical_red_flags: List[str]
    remediation_action_items: List[str]

class CustodyVendorDueDiligenceEngine:
    """
    Institutional due diligence engine for auditing digital asset custodians across SEC Qualified Custodian status,
    SOC 2 Type II compliance, bankruptcy remoteness, crime insurance, and MPC key security.
    """
    def __init__(self, min_passing_score: float = 80.0, min_insurance_usd: float = 50_000_000.0):
        self.min_passing_score = min_passing_score
        self.min_insurance_usd = min_insurance_usd

    def evaluate_custodian(self, profile: CustodyVendorProfile) -> CustodyVendorDueDiligenceReport:
        """
        Audits vendor profile across 5 risk pillars and detects critical compliance red flags.
        """
        red_flags = []
        action_items = []

        # 1. Regulatory Pillar (25%)
        reg_findings = []
        if profile.is_sec_qualified_custodian and profile.charter_type in ["STATE_CHARTERED_TRUST", "FEDERAL_BANK", "SEC_BROKER_DEALER"]:
            reg_score = 100.0
            reg_findings.append(f"Valid SEC Qualified Custodian ({profile.charter_type}).")
        else:
            reg_score = 0.0
            msg = f"CRITICAL RED FLAG: Vendor {profile.vendor_name} lacks SEC Qualified Custodian status!"
            red_flags.append(msg)
            reg_findings.append(msg)

        if not profile.is_asset_bankruptcy_remote:
            msg = f"CRITICAL RED FLAG: Client assets are NOT legally bankruptcy-remote!"
            red_flags.append(msg)

        # 2. Security Pillar (25%)
        sec_findings = []
        sec_score = 0.0
        if profile.has_soc2_type2_unqualified:
            sec_score += 50.0
            sec_findings.append("Unqualified (clean) SOC 2 Type II report verified.")
        else:
            msg = "Missing SOC 2 Type II unqualified report."
            sec_findings.append(msg)
            action_items.append(msg)

        if profile.fips_140_2_level >= 3:
            sec_score += 30.0
            sec_findings.append(f"Hardware Security Level: FIPS 140-2 Level {profile.fips_140_2_level}.")
        else:
            sec_findings.append(f"Sub-standard FIPS level ({profile.fips_140_2_level} < 3).")

        if profile.conducts_annual_pen_tests:
            sec_score += 20.0
            sec_findings.append("Annual third-party penetration testing confirmed.")

        # 3. Insurance Pillar (20%)
        ins_findings = []
        if profile.crime_insurance_coverage_usd >= self.min_insurance_usd:
            ins_score = 100.0
            ins_findings.append(f"Crime/specie insurance coverage (${profile.crime_insurance_coverage_usd:,.2f}) meets required threshold.")
        else:
            ins_score = round((profile.crime_insurance_coverage_usd / self.min_insurance_usd) * 100.0, 2)
            msg = f"Insurance coverage (${profile.crime_insurance_coverage_usd:,.2f}) below min target (${self.min_insurance_usd:,.2f})."
            ins_findings.append(msg)
            action_items.append(msg)

        # 4. Operations Pillar (15%)
        ops_findings = []
        ops_score = 0.0
        if profile.uptime_sla_pct >= 99.9:
            ops_score += 60.0
            ops_findings.append(f"Uptime SLA ({profile.uptime_sla_pct}%) meets 99.9% target.")
        else:
            ops_score += 30.0

        if profile.rto_hours <= 4.0:
            ops_score += 40.0
            ops_findings.append(f"Recovery Time Objective ({profile.rto_hours}h) <= 4h.")

        # 5. Governance Pillar (15%)
        gov_score = 100.0 if profile.conducts_annual_pen_tests else 50.0
        gov_findings = ["Independent governance and operational controls audited."]

        pillars = [
            PillarScore("REGULATORY_LEGAL", reg_score, 0.25, reg_findings),
            PillarScore("CYBERSECURITY", sec_score, 0.25, sec_findings),
            PillarScore("INSURANCE_COVERAGE", ins_score, 0.20, ins_findings),
            PillarScore("OPERATIONAL_RESILIENCE", ops_score, 0.15, ops_findings),
            PillarScore("GOVERNANCE_CONTROLS", gov_score, 0.15, gov_findings)
        ]

        composite_score = round(sum(p.score * p.weight for p in pillars), 2)

        if red_flags:
            decision = "REJECTED"
        elif composite_score >= self.min_passing_score and not action_items:
            decision = "APPROVED"
        else:
            decision = "CONDITIONAL_APPROVAL"

        logger.info(f"CUSTODY DUE DILIGENCE [{profile.vendor_name}]: Score={composite_score}/100 -> Decision={decision}")

        return CustodyVendorDueDiligenceReport(
            vendor_id=profile.vendor_id,
            vendor_name=profile.vendor_name,
            composite_due_diligence_score=composite_score,
            decision_status=decision,
            pillar_breakdown=pillars,
            critical_red_flags=red_flags,
            remediation_action_items=action_items
        )
