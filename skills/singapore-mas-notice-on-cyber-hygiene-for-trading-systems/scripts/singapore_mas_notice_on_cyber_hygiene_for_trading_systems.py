import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class ComplianceRecord:
    """Legacy ComplianceRecord container for backward compatibility."""
    trade_id: str
    is_compliant: bool
    notes: str

class MASCyberHygieneRequirement(str, Enum):
    ADMIN_ACCOUNT_SECURITY = "ADMIN_ACCOUNT_SECURITY"       # Restrict admin accounts
    MULTI_FACTOR_AUTH = "MULTI_FACTOR_AUTH"                 # Mandatory MFA for admin & customer access
    SECURITY_PATCH_MANAGEMENT = "SECURITY_PATCH_MANAGEMENT" # Critical patches applied <= 30 days
    BASELINE_SECURITY_STANDARDS = "BASELINE_SECURITY_STANDARDS" # System hardening
    NETWORK_PERIMETER_DEFENSE = "NETWORK_PERIMETER_DEFENSE" # Firewalls & DMZ
    ANTI_MALWARE_PROTECTION = "ANTI_MALWARE_PROTECTION"     # Endpoint anti-malware

@dataclass
class TradingSystemAsset:
    system_id: str
    system_name: str
    asset_type: str                                         # e.g., 'ORDER_ROUTER', 'MARKET_DATA_GATEWAY', 'TRADING_DB'
    has_mfa_enabled: bool
    admin_accounts_restricted: bool
    critical_patches_applied_within_30d: bool
    baseline_security_hardened: bool
    network_perimeter_firewalled: bool
    anti_malware_active: bool

@dataclass
class MASCyberHygieneAuditReport:
    system_id: str
    is_compliant: bool
    compliance_score_pct: float                             # 0.0 to 100.0%
    failed_requirements: List[MASCyberHygieneRequirement]
    mandatory_remediations: List[str]
    audit_notes: str

class ComplianceChecker:
    """
    Legacy ComplianceChecker class retained for 100% backward compatibility.
    """
    def __init__(self):
        self.rules = ["rule1", "rule2"]
        self.mas_engine = SingaporeMASCyberHygieneEngine()

    def check_compliance(self, trade_id: str) -> ComplianceRecord:
        return ComplianceRecord(trade_id=trade_id, is_compliant=True, notes="Compliant")

    def batch_check(self, trade_ids: List[str]) -> List[ComplianceRecord]:
        return [self.check_compliance(tid) for tid in trade_ids]

class SingaporeMASCyberHygieneEngine:
    """
    Production-grade MAS Notice on Cyber Hygiene audit engine for trading systems,
    evaluating the 6 mandatory MAS baseline controls (MFA, admin account security, 30-day patch management,
    system hardening, network defenses, and anti-malware protection).
    """
    def audit_trading_asset(self, asset: TradingSystemAsset) -> MASCyberHygieneAuditReport:
        """
        Audits a trading system asset against all 6 mandatory MAS Cyber Hygiene baseline requirements.
        """
        failed_reqs: List[MASCyberHygieneRequirement] = []
        remediations: List[str] = []

        # 1. Admin Account Security
        if not asset.admin_accounts_restricted:
            failed_reqs.append(MASCyberHygieneRequirement.ADMIN_ACCOUNT_SECURITY)
            remediations.append("Restrict administrative account privileges and disable unnecessary root logins.")

        # 2. Multi-Factor Authentication (MFA)
        if not asset.has_mfa_enabled:
            failed_reqs.append(MASCyberHygieneRequirement.MULTI_FACTOR_AUTH)
            remediations.append("Mandate hardware/TOTP Multi-Factor Authentication for all admin and remote access.")

        # 3. Security Patch Management
        if not asset.critical_patches_applied_within_30d:
            failed_reqs.append(MASCyberHygieneRequirement.SECURITY_PATCH_MANAGEMENT)
            remediations.append("Apply missing critical security patches within 30 days or establish compensating firewall controls.")

        # 4. Baseline Security Hardening
        if not asset.baseline_security_hardened:
            failed_reqs.append(MASCyberHygieneRequirement.BASELINE_SECURITY_STANDARDS)
            remediations.append("Enforce MAS baseline security configuration standards (CIS benchmarks).")

        # 5. Network Perimeter Defense
        if not asset.network_perimeter_firewalled:
            failed_reqs.append(MASCyberHygieneRequirement.NETWORK_PERIMETER_DEFENSE)
            remediations.append("Enforce network perimeter firewalls and segment trading infrastructure into isolated DMZs.")

        # 6. Anti-Malware Protection
        if not asset.anti_malware_active:
            failed_reqs.append(MASCyberHygieneRequirement.ANTI_MALWARE_PROTECTION)
            remediations.append("Deploy active EDR / anti-malware protection with daily signature updates.")

        passed_count = 6 - len(failed_reqs)
        score_pct = round((passed_count / 6.0) * 100.0, 2)
        is_compliant = len(failed_reqs) == 0

        status_str = "COMPLIANT" if is_compliant else "NON_COMPLIANT_BREACH"
        notes = (
            f"MAS CYBER HYGIENE [{status_str}] ({asset.system_id}): Score = {score_pct}%, "
            f"Failed Controls = {[f.value for f in failed_reqs]}."
        )

        if not is_compliant:
            logger.warning(notes)
        else:
            logger.info(notes)

        return MASCyberHygieneAuditReport(
            system_id=asset.system_id,
            is_compliant=is_compliant,
            compliance_score_pct=score_pct,
            failed_requirements=failed_reqs,
            mandatory_remediations=remediations,
            audit_notes=notes
        )
