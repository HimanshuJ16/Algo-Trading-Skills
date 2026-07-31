import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

@dataclass
class OnChainTxPayload:
    tx_hash: str
    from_address: str
    to_address: str
    value_usd: float
    gas_price_gwei: float
    method_signature: str                # e.g., 'transfer(address,uint256)', 'approve(address,uint256)'
    block_number: int
    timestamp_utc: float

@dataclass
class OnChainRiskPolicy:
    max_transfer_usd: float = 50000.0
    max_gas_gwei: float = 200.0
    sanctioned_addresses: Set[str] = field(default_factory=set)
    whitelisted_methods: Set[str] = field(default_factory=lambda: {
        "transfer(address,uint256)", "transferFrom(address,address,uint256)", "swap(uint256,uint256,address,bytes)"
    })

@dataclass
class RiskVectorFlag:
    vector_name: str                     # 'BLACKLIST_INTERACTION', 'HIGH_VALUE_SPIKE', 'GAS_SPIKE_MEV', 'UNAPPROVED_METHOD_CALL'
    score_penalty: int
    severity: str                        # 'CRITICAL', 'HIGH', 'MEDIUM'
    details: str

@dataclass
class OnChainMonitoringReport:
    tx_hash: str
    risk_score: int                      # 0 to 100
    risk_flags: List[RiskVectorFlag]
    is_blocked: bool
    status: str                          # 'TRANSACTION_SAFE', 'ANOMALY_SUSPECTED', 'HIGH_RISK_BLOCK'
    audit_notes: str

class OnChainAnomalyMonitorEngine:
    """
    On-chain transaction anomaly monitor evaluating sanctions/OFAC blacklists, high-value transfer spikes,
    abnormal gas/priority fee spikes, and unapproved contract interactions for crypto custody security.
    """
    def __init__(self, policy: Optional[OnChainRiskPolicy] = None):
        self.policy = policy or OnChainRiskPolicy()

    def audit_transaction(self, tx: OnChainTxPayload) -> OnChainMonitoringReport:
        """
        Audits single on-chain transaction payload against 4 risk vectors and computes composite risk score.
        """
        flags: List[RiskVectorFlag] = []
        score = 0

        from_addr_clean = tx.from_address.lower()
        to_addr_clean = tx.to_address.lower()
        sanctions_clean = {a.lower() for a in self.policy.sanctioned_addresses}

        # Vector 1: Sanctions / OFAC Blacklist Check (+80 points)
        if from_addr_clean in sanctions_clean or to_addr_clean in sanctions_clean:
            flags.append(RiskVectorFlag(
                vector_name="BLACKLIST_INTERACTION",
                score_penalty=80,
                severity="CRITICAL",
                details=f"Transaction interacts with sanctioned/blacklisted address: from='{tx.from_address}', to='{tx.to_address}'."
            ))
            score += 80

        # Vector 2: High Value Transfer Spike (+40 points)
        if tx.value_usd > self.policy.max_transfer_usd:
            flags.append(RiskVectorFlag(
                vector_name="HIGH_VALUE_SPIKE",
                score_penalty=40,
                severity="HIGH",
                details=f"Transfer value ${tx.value_usd:,.2f} exceeds threshold limit ${self.policy.max_transfer_usd:,.2f}."
            ))
            score += 40

        # Vector 3: Abnormal Gas Price Spike (+20 points)
        if tx.gas_price_gwei > self.policy.max_gas_gwei:
            flags.append(RiskVectorFlag(
                vector_name="GAS_SPIKE_MEV",
                score_penalty=20,
                severity="MEDIUM",
                details=f"Gas price {tx.gas_price_gwei:.1f} Gwei exceeds max threshold {self.policy.max_gas_gwei:.1f} Gwei."
            ))
            score += 20

        # Vector 4: Unapproved Smart Contract Function (+30 points)
        if tx.method_signature and tx.method_signature not in self.policy.whitelisted_methods:
            flags.append(RiskVectorFlag(
                vector_name="UNAPPROVED_METHOD_CALL",
                score_penalty=30,
                severity="HIGH",
                details=f"Method signature '{tx.method_signature}' is not in whitelisted methods."
            ))
            score += 30

        final_score = min(100, score)

        if final_score >= 70:
            status = "HIGH_RISK_BLOCK"
            is_blocked = True
        elif final_score >= 30:
            status = "ANOMALY_SUSPECTED"
            is_blocked = False
        else:
            status = "TRANSACTION_SAFE"
            is_blocked = False

        notes = (
            f"ON-CHAIN TX AUDIT [{tx.tx_hash[:10]}... - {status}]: Composite Risk Score = {final_score}/100. "
            f"Flags Triggered = {len(flags)}."
        )
        if is_blocked:
            logger.error(f"HIGH RISK ON-CHAIN ANOMALY DETECTED: {notes}")
        elif status == "ANOMALY_SUSPECTED":
            logger.warning(notes)
        else:
            logger.info(notes)

        return OnChainMonitoringReport(
            tx_hash=tx.tx_hash,
            risk_score=final_score,
            risk_flags=flags,
            is_blocked=is_blocked,
            status=status,
            audit_notes=notes
        )
