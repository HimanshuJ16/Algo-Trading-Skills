import hashlib
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

@dataclass
class Result:
    success: bool
    message: str

@dataclass
class KeyCompromiseIncident:
    key_id: str
    wallet_address: str
    suspected_leak_time: str             # e.g. '2026-07-31T12:00:00Z'
    affected_systems: List[str]

@dataclass
class KeyAccessLog:
    timestamp: str
    ip_address: str
    action: str                          # e.g. 'SIGN_TRANSACTION', 'EXPORT_KEY'
    status_code: int
    authorized_ips: List[str]

@dataclass
class OnChainTx:
    tx_hash: str
    from_address: str
    to_address: str
    amount_crypto: float
    timestamp: str

@dataclass
class KeyForensicsReport:
    key_id: str
    wallet_address: str
    unauthorized_access_count: int
    unauthorized_ips: List[str]
    unauthorized_tx_count: int
    total_exfiltrated_crypto: float
    evidence_sha256_hash: str
    status: str                          # 'UNAUTHORIZED_ACCESS_CONFIRMED', 'NO_UNAUTHORIZED_ACCESS_FOUND'
    audit_notes: str

class Analyzer:
    """
    Legacy Analyzer retained for backward compatibility.
    """
    def __init__(self, config: dict):
        self.config = config

    def execute(self) -> Result:
        if not self.config:
            return Result(False, "No config provided")
        return Result(True, "Success")

class KeyForensicsAnalyzer:
    """
    Post-incident digital forensics engine analyzing KMS access logs, IP whitelist violations,
    and on-chain transaction traces following a suspected cryptographic key compromise.
    """
    def __init__(self):
        pass

    def run_forensic_analysis(
        self,
        incident: KeyCompromiseIncident,
        access_logs: List[KeyAccessLog],
        on_chain_txs: List[OnChainTx]
    ) -> KeyForensicsReport:
        """
        Analyzes off-chain KMS access logs and on-chain transactions to audit key compromise and generate evidence hashes.
        """
        unauthorized_ips: List[str] = []
        unauthorized_logs: List[KeyAccessLog] = []

        for log in access_logs:
            if log.ip_address not in log.authorized_ips:
                unauthorized_logs.append(log)
                if log.ip_address not in unauthorized_ips:
                    unauthorized_ips.append(log.ip_address)

        # On-chain unauthorized tx trace from wallet_address
        wallet_lower = incident.wallet_address.lower()
        unauthorized_txs = [
            tx for tx in on_chain_txs if tx.from_address.lower() == wallet_lower
        ]
        total_exfiltrated = sum(tx.amount_crypto for tx in unauthorized_txs)

        # Generate Evidence SHA-256 Hash over raw logs & tx hashes
        evidence_raw = (
            f"INCIDENT:{incident.key_id}|LOGS:{len(access_logs)}|"
            f"UNAUTH_LOGS:{len(unauthorized_logs)}|UNAUTH_TXS:{[t.tx_hash for t in unauthorized_txs]}"
        )
        evidence_hash = hashlib.sha256(evidence_raw.encode("utf-8")).hexdigest()

        is_unauthorized = len(unauthorized_logs) > 0 or len(unauthorized_txs) > 0
        status = "UNAUTHORIZED_ACCESS_CONFIRMED" if is_unauthorized else "NO_UNAUTHORIZED_ACCESS_FOUND"

        notes = (
            f"KEY FORENSICS REPORT [{incident.key_id} ({incident.wallet_address}) - {status}]: "
            f"Unauthorized Access Log Hits = {len(unauthorized_logs)} (IPs: {unauthorized_ips}). "
            f"Unauthorized On-Chain Txs = {len(unauthorized_txs)} (Exfiltrated = {total_exfiltrated:,.4f} Crypto). "
            f"Evidence SHA-256 = {evidence_hash[:16]}..."
        )

        if is_unauthorized:
            logger.critical(f"CRITICAL KEY COMPROMISE CONFIRMED: {notes}")
        else:
            logger.info(notes)

        return KeyForensicsReport(
            key_id=incident.key_id,
            wallet_address=incident.wallet_address,
            unauthorized_access_count=len(unauthorized_logs),
            unauthorized_ips=unauthorized_ips,
            unauthorized_tx_count=len(unauthorized_txs),
            total_exfiltrated_crypto=round(total_exfiltrated, 4),
            evidence_sha256_hash=evidence_hash,
            status=status,
            audit_notes=notes
        )
