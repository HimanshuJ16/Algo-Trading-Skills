"""
crypto-wallet-key-custody-security: Production-grade key permission auditor,
storage security analyzer, hot/cold ratio boundary validator, and independent
outbound transfer security monitor.
"""
from dataclasses import dataclass
from enum import Enum
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class StorageBackend(str, Enum):
    AWS_KMS = "aws_kms"
    HASHICORP_VAULT = "hashicorp_vault"
    HARDWARE_HSM = "hardware_hsm"
    ENV_VARIABLE = "env_variable"
    PLAINTEXT_FILE = "plaintext_file"


class RiskLevel(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass
class AuditFinding:
    key_name: str
    risk_level: RiskLevel
    issue: str
    remediation: str


class KeyCustodySecurityAuditor:
    """
    Production security auditor enforcing API key scoping, IP whitelisting,
    storage backend security, hot/cold capital balance limits, and transfer monitoring.
    """

    def __init__(self, max_hot_ratio: float = 0.15, alert_fn: Optional[Callable[[str], None]] = None):
        self.max_hot_ratio = max_hot_ratio
        self.alert_fn = alert_fn or (lambda msg: logger.warning(msg))
        self.findings: List[AuditFinding] = []

    def audit_key_config(self, key_config: Dict[str, Any]) -> List[AuditFinding]:
        """
        Audits single API key config dict:
        {"name": str, "used_by": str, "permissions": List[str], "ip_whitelisted": bool, "storage_backend": str}
        """
        name = key_config.get("name", "unnamed_key")
        used_by = key_config.get("used_by", "unknown")
        permissions = set(p.lower() for p in key_config.get("permissions", []))
        ip_whitelisted = key_config.get("ip_whitelisted", False)
        storage_backend = key_config.get("storage_backend", StorageBackend.PLAINTEXT_FILE)

        findings = []

        # 1. Scope Permission Check
        if "withdraw" in permissions and used_by == "trading_bot":
            finding = AuditFinding(
                key_name=name,
                risk_level=RiskLevel.CRITICAL,
                issue="Trading bot key possesses WITHDRAW permission",
                remediation="Split into trade-only key for bot and human-gated withdraw key.",
            )
            findings.append(finding)

        # 2. IP Whitelisting Check
        if not ip_whitelisted:
            finding = AuditFinding(
                key_name=name,
                risk_level=RiskLevel.HIGH,
                issue="API key missing IP address whitelisting restriction",
                remediation="Configure static IP allowlist on exchange API management panel.",
            )
            findings.append(finding)

        # 3. Storage Security Backend Check
        insecure_backends = [StorageBackend.PLAINTEXT_FILE, StorageBackend.ENV_VARIABLE, "env_file", "plaintext"]
        if storage_backend in insecure_backends:
            finding = AuditFinding(
                key_name=name,
                risk_level=RiskLevel.HIGH,
                issue=f"Secret stored in insecure backend ({storage_backend})",
                remediation="Migrate API key secret to Cloud KMS, HashiCorp Vault, or hardware HSM.",
            )
            findings.append(finding)

        self.findings.extend(findings)
        return findings

    def evaluate_hot_cold_allocation(self, hot_balance: float, total_balance: float) -> Tuple[bool, float, Optional[AuditFinding]]:
        """Evaluates operational hot balance vs cold storage allocation."""
        ratio = hot_balance / total_balance if total_balance > 0 else 0.0
        is_safe = ratio <= self.max_hot_ratio
        finding = None

        if not is_safe:
            finding = AuditFinding(
                key_name="hot_wallet_balance",
                risk_level=RiskLevel.HIGH,
                issue=f"Hot balance ratio ({ratio*100:.1f}%) exceeds maximum limit ({self.max_hot_ratio*100:.1f}%)",
                remediation="Sweep excess operational capital to offline cold storage.",
            )
            self.findings.append(finding)
            self.alert_fn(f"SECURITY ALERT: {finding.issue}")

        return is_safe, ratio, finding

    def audit_outbound_transfer(self, destination_address: str, amount: float, approved_whitelist: Set[str]) -> bool:
        """Inspects outbound transfer events against pre-approved address whitelist."""
        if destination_address not in approved_whitelist:
            msg = f"CRITICAL SECURITY ALERT: Outbound transfer of {amount} to UNAPPROVED address {destination_address} detected!"
            logger.critical(msg)
            self.alert_fn(msg)
            return False
        return True


# Backward compatibility functions
def audit_key_permissions(key_configs):
    """key_configs: [{"name": str, "used_by": str, "permissions": [str]}]"""
    auditor = KeyCustodySecurityAuditor()
    findings = []
    for cfg in key_configs:
        key_findings = auditor.audit_key_config(cfg)
        for f in key_findings:
            if f.risk_level == RiskLevel.CRITICAL:
                findings.append(
                    f"Key '{cfg.get('name')}' used by trading_bot has withdraw permission -- split into a trade-only key and a separately-controlled withdraw key."
                )
    return findings


def check_hot_balance_ratio(hot_balance, total_balance, max_hot_ratio=0.15):
    ratio = hot_balance / total_balance if total_balance else 0
    return ratio <= max_hot_ratio, ratio
