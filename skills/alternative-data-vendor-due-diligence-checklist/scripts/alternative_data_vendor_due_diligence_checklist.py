"""
alternative-data-vendor-due-diligence-checklist:
Compliance evaluation engine for alternative data vendor onboarding.
"""
import dataclasses
import logging
from typing import List

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class VendorDueDiligenceQuestionnaire:
    vendor_name: str
    dataset_name: str
    
    # Legal & Rights
    has_resell_rights: bool
    
    # Web Scraping Legal Risks
    is_web_scraped: bool
    scrapes_behind_login: bool
    bypasses_captchas: bool
    
    # Privacy Risks (PII)
    contains_pii: bool
    is_gdpr_ccpa_compliant: bool
    has_robust_anonymization: bool
    
    # Insider Trading / MNPI Risks
    is_material_non_public_information: bool


@dataclasses.dataclass
class DueDiligenceReport:
    is_approved: bool
    critical_flags: List[str]
    warnings: List[str]


class VendorDueDiligenceEvaluator:
    """
    Evaluates a vendor's DDQ against strict institutional hedge fund compliance rules.
    """
    
    def evaluate(self, ddq: VendorDueDiligenceQuestionnaire) -> DueDiligenceReport:
        critical_flags = []
        warnings = []
        
        # 1. Legal Rights Check
        if not ddq.has_resell_rights:
            critical_flags.append("Vendor does not possess explicit legal rights to resell this data.")
            
        # 2. MNPI Check
        if ddq.is_material_non_public_information:
            critical_flags.append("Data is flagged as Material Non-Public Information (MNPI). Strict prohibition.")
            
        # 3. Web Scraping & CFAA Risks
        if ddq.is_web_scraped:
            if ddq.scrapes_behind_login:
                critical_flags.append("Vendor scrapes behind authenticated logins. High CFAA/legal risk.")
            if ddq.bypasses_captchas:
                warnings.append("Vendor bypasses CAPTCHAs. Review target site Terms of Service for scraping prohibitions.")
                
        # 4. PII and Privacy Regulation (GDPR / CCPA)
        if ddq.contains_pii:
            if not ddq.is_gdpr_ccpa_compliant:
                critical_flags.append("Data contains PII but vendor lacks GDPR/CCPA compliance.")
            if not ddq.has_robust_anonymization:
                critical_flags.append("Data contains PII without robust anonymization protocols.")
                
        is_approved = len(critical_flags) == 0
        
        if is_approved:
            logger.info(f"Vendor {ddq.vendor_name} ({ddq.dataset_name}) APPROVED for onboarding.")
        else:
            logger.error(f"Vendor {ddq.vendor_name} REJECTED due to critical compliance flags: {critical_flags}")
            
        return DueDiligenceReport(
            is_approved=is_approved,
            critical_flags=critical_flags,
            warnings=warnings
        )
