import dataclasses
from dataclasses import dataclass, field
import logging
import time
from enum import Enum
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

@dataclasses.dataclass
class Config:
    """Legacy Config container for backward compatibility."""
    name: str = "smart-contract-audit-requirements-before-defi-integration"

class Engine:
    """Legacy Engine class for backward compatibility."""
    def __init__(self, config: Config):
        self.config = config

    def run(self) -> bool:
        return True

class AuditFirmTier(str, Enum):
    TIER_1_TOP_REPUTATION = "TIER_1_TOP_REPUTATION" # Trail of Bits, OpenZeppelin, Consensys, Spearbit
    TIER_2_REPUTABLE = "TIER_2_REPUTABLE"           # CertiK, Hacken, PeckShield
    UNVERIFIED_INDIVIDUAL = "UNVERIFIED_INDIVIDUAL"

@dataclass
class AuditReport:
    firm_name: str
    firm_tier: AuditFirmTier
    audit_date_iso: str
    critical_findings_count: int
    high_findings_count: int
    all_critical_high_remediated: bool
    fix_verification_confirmed: bool

@dataclass
class DeFiProtocolSpec:
    protocol_name: str
    contract_address: str
    tvl_usd: float
    mainnet_days_active: int
    audits: List[AuditReport]
    has_active_bug_bounty: bool
    bug_bounty_max_payout_usd: float
    admin_timelock_delay_hours: float       # Mandatory >= 48 hours for upgrades
    admin_multisig_signers_count: int       # Total signers
    admin_multisig_threshold_required: int  # e.g., 3 out of 5
    has_emergency_pause_circuit_breaker: bool

@dataclass
class DeFiIntegrationGateReport:
    protocol_name: str
    is_approved: bool
    safety_score_pct: float                 # 0.0 to 100.0%
    blocking_violations: List[str]
    audit_notes: str

class SmartContractAuditRequirementsBeforeDeFiIntegrationEngine:
    """
    Production-grade DeFi Smart Contract Audit & Governance Due Diligence Engine evaluating
    independent audit quality, unaddressed vulnerabilities, timelocks, multisig thresholds, and TVL bug bounties before capital deployment.
    """
    def __init__(
        self,
        min_tier1_audits_required: int = 2,
        min_mainnet_days: int = 90,
        min_timelock_hours: float = 48.0,
        min_bug_bounty_usd: float = 100000.0
    ):
        self.min_tier1_audits_required = min_tier1_audits_required
        self.min_mainnet_days = min_mainnet_days
        self.min_timelock_hours = min_timelock_hours
        self.min_bug_bounty_usd = min_bug_bounty_usd

    def evaluate_protocol(self, protocol: DeFiProtocolSpec) -> DeFiIntegrationGateReport:
        """
        Evaluates a DeFi protocol against institutional audit and governance requirements.
        """
        violations: List[str] = []
        score_components = []

        # 1. Audit Count & Firm Quality Check
        t1_audits = [a for a in protocol.audits if a.firm_tier == AuditFirmTier.TIER_1_TOP_REPUTATION]
        if len(t1_audits) < self.min_tier1_audits_required:
            violations.append(f"INSUFFICIENT_AUDITS: Found {len(t1_audits)} Tier-1 audits (Required >= {self.min_tier1_audits_required}).")
            score_components.append(0.0)
        else:
            score_components.append(1.0)

        # 2. Unaddressed Critical / High Vulnerability Check
        unresolved = [a for a in protocol.audits if not (a.all_critical_high_remediated and a.fix_verification_confirmed)]
        if unresolved:
            failing_firms = [a.firm_name for a in unresolved]
            violations.append(f"UNRESOLVED_VULNERABILITIES: Audits by {failing_firms} have unverified or un-remediated Critical/High findings.")
            score_components.append(0.0)
        else:
            score_components.append(1.0)

        # 3. Mainnet Battle-Testing Duration Check
        if protocol.mainnet_days_active < self.min_mainnet_days:
            violations.append(f"UNTESTED_CODEBASE: Protocol active for {protocol.mainnet_days_active} days on mainnet (Required >= {self.min_mainnet_days} days).")
            score_components.append(0.0)
        else:
            score_components.append(1.0)

        # 4. Admin Timelock & Upgradeability Check
        if protocol.admin_timelock_delay_hours < self.min_timelock_hours:
            violations.append(f"DANGEROUS_TIMELOCK: Admin upgrade timelock delay is {protocol.admin_timelock_delay_hours}h (Required >= {self.min_timelock_hours}h).")
            score_components.append(0.0)
        else:
            score_components.append(1.0)

        # 5. Multisig Threshold Check
        if protocol.admin_multisig_threshold_required < 3:
            violations.append(f"WEAK_MULTISIG: Admin multisig threshold {protocol.admin_multisig_threshold_required} is less than required 3 signers.")
            score_components.append(0.0)
        else:
            score_components.append(1.0)

        # 6. Bug Bounty Program Check
        if not protocol.has_active_bug_bounty or protocol.bug_bounty_max_payout_usd < self.min_bug_bounty_usd:
            violations.append(f"INADEQUATE_BUG_BOUNTY: Active bug bounty payout ${protocol.bug_bounty_max_payout_usd:,.2f} < ${self.min_bug_bounty_usd:,.2f}.")
            score_components.append(0.0)
        else:
            score_components.append(1.0)

        safety_score = round((sum(score_components) / len(score_components)) * 100.0, 2)
        is_approved = len(violations) == 0

        status_str = "APPROVED_FOR_INTEGRATION" if is_approved else "BLOCKED_HIGH_RISK"
        notes = (
            f"DEFI DUE DILIGENCE [{status_str}] ({protocol.protocol_name}): Safety Score = {safety_score}%, "
            f"Violations = {violations}."
        )

        if not is_approved:
            logger.warning(notes)
        else:
            logger.info(notes)

        return DeFiIntegrationGateReport(
            protocol_name=protocol.protocol_name,
            is_approved=is_approved,
            safety_score_pct=safety_score,
            blocking_violations=violations,
            audit_notes=notes
        )
