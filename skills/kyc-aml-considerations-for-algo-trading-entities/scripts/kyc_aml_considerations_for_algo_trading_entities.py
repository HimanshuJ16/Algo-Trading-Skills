import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

FATF_BLACK_LIST: Set[str] = {"IRAN", "NORTH_KOREA", "MYANMAR"}

@dataclass
class UboRecord:
    name: str                           # e.g. 'Jane Doe'
    ownership_pct: float                # Ownership percentage (e.g. 35.0%)
    is_pep: bool                        # Politically Exposed Person flag
    is_sanctioned: bool                 # OFAC / UN / EU Sanctions match flag
    is_identity_verified: bool          # Passport / National ID verified flag

@dataclass
class EntityKycAmlPayload:
    entity_name: str                    # e.g. 'Alpha Quant Offshore LP'
    incorporation_country: str          # e.g. 'CAYMAN_ISLANDS', 'USA', 'IRAN'
    banking_country: str                # e.g. 'USA', 'UK'
    ubos: List[UboRecord]
    has_audited_financials: bool = True

@dataclass
class KycAmlAuditReport:
    entity_name: str
    total_ubo_ownership_accounted_pct: float
    unverified_ubos_count: int
    has_sanctions_hit: bool
    has_pep_hit: bool
    is_fatf_blacklisted: bool
    status: str                         # 'KYC_AML_APPROVED', 'REJECTED_SANCTIONS_MATCH', 'REJECTED_UNVERIFIED_UBO', 'REJECTED_FATF_HIGH_RISK'
    audit_notes: str

class KycAmlEntityComplianceEngine:
    """
    Institutional KYC/AML compliance engine for algorithmic trading entities,
    auditing Ultimate Beneficial Ownership (UBO >= 25%), OFAC/PEP sanctions screening, and FATF high-risk jurisdiction compliance.
    """
    def __init__(self, ubo_ownership_threshold_pct: float = 25.0):
        self.ubo_ownership_threshold_pct = ubo_ownership_threshold_pct

    def audit_entity_compliance(self, payload: EntityKycAmlPayload) -> KycAmlAuditReport:
        """
        Audits entity payload against UBO transparency, OFAC/PEP screening, and FATF blacklists.
        """
        inc_country = payload.incorporation_country.strip().upper()
        bank_country = payload.banking_country.strip().upper()

        # 1. Audit FATF Blacklist Jurisdictions
        if inc_country in FATF_BLACK_LIST or bank_country in FATF_BLACK_LIST:
            bad_country = inc_country if inc_country in FATF_BLACK_LIST else bank_country
            notes = f"KYC/AML REJECT [{payload.entity_name}]: FATF Blacklisted Jurisdiction '{bad_country}'!"
            logger.critical(notes)
            return KycAmlAuditReport(
                entity_name=payload.entity_name, total_ubo_ownership_accounted_pct=0.0,
                unverified_ubos_count=0, has_sanctions_hit=False, has_pep_hit=False,
                is_fatf_blacklisted=True, status="REJECTED_FATF_HIGH_RISK", audit_notes=notes
            )

        # 2. Audit OFAC / PEP Sanctions
        has_sanctions = False
        has_pep = False
        unverified_count = 0
        tot_ownership = 0.0

        for ubo in payload.ubos:
            tot_ownership += ubo.ownership_pct
            if ubo.is_sanctioned:
                has_sanctions = True
            if ubo.is_pep:
                has_pep = True
            if ubo.ownership_pct >= self.ubo_ownership_threshold_pct and not ubo.is_identity_verified:
                unverified_count += 1

        if has_sanctions:
            notes = f"KYC/AML REJECT [{payload.entity_name}]: OFAC / UN Sanctions Match on UBO!"
            logger.critical(notes)
            return KycAmlAuditReport(
                entity_name=payload.entity_name, total_ubo_ownership_accounted_pct=tot_ownership,
                unverified_ubos_count=unverified_count, has_sanctions_hit=True, has_pep_hit=has_pep,
                is_fatf_blacklisted=False, status="REJECTED_SANCTIONS_MATCH", audit_notes=notes
            )

        # 3. Audit UBO Transparency Threshold (Unverified UBOs >= 25%)
        if unverified_count > 0:
            notes = (
                f"KYC/AML REJECT [{payload.entity_name}]: Found {unverified_count} UBO(s) holding "
                f">= {self.ubo_ownership_threshold_pct}% ownership without identity verification."
            )
            logger.warning(notes)
            return KycAmlAuditReport(
                entity_name=payload.entity_name, total_ubo_ownership_accounted_pct=tot_ownership,
                unverified_ubos_count=unverified_count, has_sanctions_hit=False, has_pep_hit=has_pep,
                is_fatf_blacklisted=False, status="REJECTED_UNVERIFIED_UBO", audit_notes=notes
            )

        pep_str = " (Enhanced Due Diligence PEP Active)" if has_pep else ""
        notes = (
            f"KYC/AML APPROVED [{payload.entity_name}]: Verified UBO ownership = {tot_ownership:.1f}%. "
            f"Zero sanctions hits. Jurisdiction = {inc_country} / {bank_country}{pep_str}."
        )
        logger.info(notes)

        return KycAmlAuditReport(
            entity_name=payload.entity_name,
            total_ubo_ownership_accounted_pct=tot_ownership,
            unverified_ubos_count=0,
            has_sanctions_hit=False,
            has_pep_hit=has_pep,
            is_fatf_blacklisted=False,
            status="KYC_AML_APPROVED",
            audit_notes=notes
        )
