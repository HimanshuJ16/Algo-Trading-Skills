import logging
import datetime
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class KeyFormatType(Enum):
    BIP39_MNEMONIC = "BIP39_MNEMONIC"             # Open standard 12/24 word seed phrase
    SLIP39_SHAMIR = "SLIP39_SHAMIR"               # Open standard Shamir's Secret Sharing (SLIP-0039)
    BIP32_HD_PATH = "BIP32_HD_PATH"               # Standard Hierarchical Deterministic derivation path
    WIF_PRIVATE_KEY = "WIF_PRIVATE_KEY"           # Wallet Import Format (raw private key)
    PROPRIETARY_MPC_SHARE = "PROPRIETARY_MPC_SHARE"# Closed/vendor-specific MPC key share
    PROPRIETARY_HSM_BLOB = "PROPRIETARY_HSM_BLOB"  # Enclave-bound non-exportable HSM blob


class LockInRiskLevel(Enum):
    LOW = "LOW"             # Standard open formats, open-source recovery tool available
    MEDIUM = "MEDIUM"       # Standard formats with vendor dependency for export
    HIGH = "HIGH"           # Proprietary formats, proprietary recovery software mandatory
    CRITICAL = "CRITICAL"   # Zero key export capability, complete vendor lock-in


class CustodyArchitecture(Enum):
    SINGLE_SIG = "SINGLE_SIG"
    MULTISIG_ON_CHAIN = "MULTISIG_ON_CHAIN"  # E.g. Safe (Gnosis), Native Bitcoin Multisig
    MPC_THRESHOLD = "MPC_THRESHOLD"          # E.g. 2-of-3 MPC Threshold Signatures
    PROPRIETARY_VAULT = "PROPRIETARY_VAULT"  # Closed proprietary hardware vault


class CustodyAnalyzerError(Exception):
    """Base exception for Custody Lock-In Analyzer errors."""
    pass


@dataclass
class CustodyProviderProfile:
    provider_id: str
    provider_name: str
    architecture: CustodyArchitecture
    supported_key_formats: List[KeyFormatType]
    open_source_recovery_tool_available: bool
    requires_vendor_active_api_for_exit: bool
    flat_export_fee_usd: float = 0.0
    estimated_exit_days: int = 7


@dataclass
class AssetPortfolio:
    total_value_usd: float
    num_wallets: int
    num_blockchain_networks: int
    avg_network_gas_fee_per_tx_usd: float = 5.0


@dataclass
class CustodyLockInAssessment:
    provider_id: str
    provider_name: str
    lock_in_risk_level: LockInRiskLevel
    open_standard_compliance_ratio: float  # Percentage of supported key formats using open standards
    portability_score: float                # 0.0 to 100.0
    estimated_migration_cost_usd: float
    estimated_migration_days: int
    risk_factors: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)


class CustodyLockInAnalyzer:
    """
    Institutional Custody Vendor Lock-In Risk & Portability Analyzer Engine.
    
    Evaluates key export formats (BIP-39, SLIP-0039, WIF vs Proprietary MPC/HSM),
    computes Open Standard Compliance Ratios, assesses disaster recovery self-sovereignty,
    estimates migration friction/costs, and assigns Lock-In Risk Levels for institutional crypto custody.
    """

    OPEN_STANDARDS = {
        KeyFormatType.BIP39_MNEMONIC,
        KeyFormatType.SLIP39_SHAMIR,
        KeyFormatType.BIP32_HD_PATH,
        KeyFormatType.WIF_PRIVATE_KEY,
    }

    def __init__(self):
        logger.info("Initialized Custody Vendor Lock-In Risk Analyzer Engine")

    def calculate_open_standard_ratio(self, key_formats: List[KeyFormatType]) -> float:
        """Calculates percentage of key formats conforming to open industry standards."""
        if not key_formats:
            return 0.0

        open_count = sum(1 for kf in key_formats if kf in self.OPEN_STANDARDS)
        return (open_count / len(key_formats)) * 100.0

    def evaluate_custody_provider(
        self, provider: CustodyProviderProfile, portfolio: AssetPortfolio
    ) -> CustodyLockInAssessment:
        """
        Evaluates vendor lock-in risk, portability scores, and estimated exit costs for a custodian.
        """
        if portfolio.total_value_usd < 0:
            raise CustodyAnalyzerError("Portfolio total value cannot be negative.")

        open_ratio = self.calculate_open_standard_ratio(provider.supported_key_formats)
        risk_factors = []
        recommendations = []

        # 1. Evaluate Portability Score (0 - 100)
        base_score = open_ratio * 0.5

        if provider.open_source_recovery_tool_available:
            base_score += 30.0
        else:
            risk_factors.append("No open-source key recovery tool available; dependent on vendor software.")
            recommendations.append("Mandate vendor provision of offline open-source key reconstruction tools.")

        if not provider.requires_vendor_active_api_for_exit:
            base_score += 20.0
        else:
            risk_factors.append("Asset exit requires active vendor API availability (insolvency vulnerability).")
            recommendations.append("Require cold backup key share extraction into self-hosted HSM or vault.")

        portability_score = min(100.0, max(0.0, base_score))

        # 2. Determine Lock-In Risk Level
        if portability_score >= 85.0 and open_ratio >= 75.0:
            risk_level = LockInRiskLevel.LOW
        elif portability_score >= 60.0:
            risk_level = LockInRiskLevel.MEDIUM
        elif portability_score >= 35.0:
            risk_level = LockInRiskLevel.HIGH
        else:
            risk_level = LockInRiskLevel.CRITICAL
            risk_factors.append("CRITICAL LOCK-IN: Proprietary closed custody format prevents independent asset recovery.")

        # 3. Estimate Migration Costs & Timelines
        # Total Migration Cost = Vendor Export Fee + (Wallets * Networks * Gas Fee)
        on_chain_tx_count = portfolio.num_wallets * portfolio.num_blockchain_networks
        estimated_gas_cost = on_chain_tx_count * portfolio.avg_network_gas_fee_per_tx_usd
        total_migration_cost = provider.flat_export_fee_usd + estimated_gas_cost

        # Timelines increase if open tools are missing
        extra_days = 0 if provider.open_source_recovery_tool_available else 14
        total_migration_days = provider.estimated_exit_days + extra_days

        logger.info(
            f"Custody Lock-In Assessment [{provider.provider_name}]: Risk={risk_level.value}, "
            f"PortabilityScore={portability_score:.1f}, ExitCost=${total_migration_cost:,.2f}"
        )

        return CustodyLockInAssessment(
            provider_id=provider.provider_id,
            provider_name=provider.provider_name,
            lock_in_risk_level=risk_level,
            open_standard_compliance_ratio=open_ratio,
            portability_score=portability_score,
            estimated_migration_cost_usd=total_migration_cost,
            estimated_migration_days=total_migration_days,
            risk_factors=risk_factors,
            recommendations=recommendations,
        )

    def simulate_disaster_recovery_drill(
        self, provider: CustodyProviderProfile, is_vendor_responsive: bool = True
    ) -> Tuple[bool, str]:
        """
        Simulates an emergency self-sovereign key recovery drill without vendor API assistance.
        """
        has_open_format = any(kf in self.OPEN_STANDARDS for kf in provider.supported_key_formats)

        if is_vendor_responsive:
            return True, f"Vendor API active. Key recovery drill SUCCESSFUL for provider '{provider.provider_name}'."

        # Vendor Outage / Insolvency Scenario: Requires open standards or open-source tool
        if has_open_format and provider.open_source_recovery_tool_available:
            return (
                True,
                f"Vendor Offline. Emergency Key Recovery SUCCESSFUL using open-source offline tool for '{provider.provider_name}'.",
            )

        logger.error(
            f"DISASTER RECOVERY FAILED [{provider.provider_name}]: Vendor offline and no open recovery tool available!"
        )
        return (
            False,
            f"Vendor Offline. Emergency Key Recovery FAILED for '{provider.provider_name}'. Assets LOCKED in proprietary format.",
        )

