import logging
from dataclasses import dataclass
from typing import List, Dict, Set

logger = logging.getLogger(__name__)

@dataclass
class WalletAddressRecord:
    chain_id: str                      # e.g. 'Ethereum', 'Arbitrum', 'Solana', 'Bitcoin'
    address: str
    public_key: str
    is_kyc_linked: bool                # True if linked to KYC exchange deposit/withdrawal
    wallet_label: str                  # e.g. 'Arbitrage_Bot_01'

@dataclass
class PrivacyRiskReport:
    address: str
    reused_chains_count: int
    chains_list: List[str]
    is_kyc_contaminated: bool
    privacy_risk_score: float           # 0.0 (Minimal) to 100.0 (Extreme Deanonymization Risk)
    risk_level: str                    # 'LOW', 'MEDIUM', 'HIGH'
    remediation_actions: List[str]

class CrossChainAddressPrivacyAuditor:
    """
    Crypto custody & security engine for auditing cross-chain wallet address reuse,
    detecting deanonymization linkages, and computing privacy risk scores.
    """
    def __init__(self, high_risk_threshold: float = 70.0, total_tracked_chains: int = 5):
        self.high_risk_threshold = high_risk_threshold
        self.total_tracked_chains = max(1, total_tracked_chains)
        self.wallets: List[WalletAddressRecord] = []

    def register_wallet(self, wallet: WalletAddressRecord):
        self.wallets.append(wallet)

    def audit_address_privacy(self, target_address: str) -> PrivacyRiskReport:
        """
        Audits a target wallet address across all registered chains and computes a Privacy Risk Score.
        """
        matching_records = [w for w in self.wallets if w.address.lower() == target_address.lower()]
        
        if not matching_records:
            return PrivacyRiskReport(
                address=target_address, reused_chains_count=0, chains_list=[],
                is_kyc_contaminated=False, privacy_risk_score=0.0, risk_level="LOW",
                remediation_actions=["Address not found in active tracking registry."]
            )

        chains = sorted(list(set(w.chain_id for w in matching_records)))
        reused_count = len(chains)
        is_kyc = any(w.is_kyc_linked for w in matching_records)

        # Risk Score Calculation:
        # 1. Chain reuse weight (up to 50 pts): (reused_count / total_tracked_chains) * 50
        # 2. KYC contamination penalty (50 pts if linked)
        reuse_score = min(50.0, (reused_count / float(self.total_tracked_chains)) * 50.0)
        kyc_penalty = 50.0 if is_kyc else 0.0
        
        risk_score = round(min(100.0, reuse_score + kyc_penalty), 2)

        remediations = []
        if reused_count > 1:
            remediations.append(
                f"ADDRESS REUSE DETECTED across {reused_count} chains ({', '.join(chains)}). Enforce BIP-44 path derivation (coin_type isolation)."
            )
        if is_kyc:
            remediations.append(
                "KYC CONTAMINATION: Wallet is linked to centralized exchange deposits. Discontinue usage for proprietary strategies."
            )

        if risk_score >= self.high_risk_threshold:
            risk_level = "HIGH"
            logger.critical(f"HIGH PRIVACY RISK [{target_address}]: Score {risk_score}/100. {'; '.join(remediations)}")
        elif risk_score >= 40.0:
            risk_level = "MEDIUM"
            logger.warning(f"MEDIUM PRIVACY RISK [{target_address}]: Score {risk_score}/100.")
        else:
            risk_level = "LOW"
            remediations.append("Wallet address exhibits strong cross-chain privacy isolation.")

        return PrivacyRiskReport(
            address=target_address,
            reused_chains_count=reused_count,
            chains_list=chains,
            is_kyc_contaminated=is_kyc,
            privacy_risk_score=risk_score,
            risk_level=risk_level,
            remediation_actions=remediations
        )
