"""
crypto-wallet-key-custody-security: key permission auditor, storage security
analyzer, hot/cold ratio boundary validator, and independent outbound transfer
security monitor.

Design rule for this module: every check FAILS CLOSED. An unrecognized storage
backend, an unattributed key, an unparseable balance or an unknown permission
name produces a finding rather than a silent pass. A custody auditor that
returns "clean" because it did not understand its input is worse than no
auditor, because it manufactures false assurance about irreversible losses.

Permission vocabulary is matched on the stems 'withdraw' and 'transfer' rather
than an exact literal, because real exchanges do not agree on naming:
  - Binance:  enableWithdrawals, enableInternalTransfer, permitsUniversalTransfer
  - Coinbase: can_transfer  ("deposit/withdrawal permissions")
  - Kraken:   "Withdraw Funds"
See references/standards.md for the cited sources.
"""
from dataclasses import dataclass, field
from enum import Enum
import logging
import math
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)


class StorageBackend(str, Enum):
    AWS_KMS = "aws_kms"
    GCP_KMS = "gcp_kms"
    AZURE_KEY_VAULT = "azure_key_vault"
    HASHICORP_VAULT = "hashicorp_vault"
    HARDWARE_HSM = "hardware_hsm"
    ENV_VARIABLE = "env_variable"
    PLAINTEXT_FILE = "plaintext_file"


class RiskLevel(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


#: Backends accepted as secure. This is an ALLOWLIST on purpose: a denylist of
#: insecure backends silently passes every value it does not recognise
#: ("", "dotenv", ".env", a typo), which is the exact failure mode a custody
#: audit must not have.
SECURE_BACKENDS: Set[str] = {
    StorageBackend.AWS_KMS.value,
    StorageBackend.GCP_KMS.value,
    StorageBackend.AZURE_KEY_VAULT.value,
    StorageBackend.HASHICORP_VAULT.value,
    StorageBackend.HARDWARE_HSM.value,
}

#: Normalized substrings that indicate a permission can move funds OUT of the
#: account. Deliberately over-inclusive: a false positive costs a review, a
#: false negative costs the balance.
FUNDS_MOVING_STEMS: Tuple[str, ...] = ("withdraw", "transfer")

#: Identifiers that mark a key as belonging to an automated process. Matched as
#: normalized substrings so 'trading-bot', 'Trading_Bot', 'strategy_engine' and
#: 'execution_service' all attribute correctly.
AUTOMATED_ACTOR_STEMS: Tuple[str, ...] = (
    "bot", "strategy", "algo", "execution", "trader", "trading", "automation", "service", "worker",
)

# The '0x' prefix is a notation marker, not address data, so its case is irrelevant.
_EVM_ADDRESS_RE = re.compile(r"^0[xX][0-9a-fA-F]{40}$")
# Bech32 data part excludes '1', 'b', 'i' and 'o'. Matched case-insensitively because
# BIP-173 permits an all-uppercase encoding (used in QR codes) alongside the canonical
# all-lowercase form; mixed case is rejected separately in normalize_address.
_BECH32_RE = re.compile(r"^[A-Za-z0-9]{1,83}1[02-9ac-hj-np-z]{6,}$", re.IGNORECASE)
_BECH32_HRP_RE = re.compile(r"^(bc|tb|bcrt|ltc|tltc)1", re.IGNORECASE)


@dataclass
class AuditFinding:
    key_name: str
    risk_level: RiskLevel
    issue: str
    remediation: str


@dataclass
class AuditSummary:
    """Aggregate verdict over everything audited since construction or reset()."""
    total_findings: int
    critical: int
    high: int
    medium: int
    low: int
    passed: bool           # True only when no CRITICAL and no HIGH findings exist
    findings: List[AuditFinding] = field(default_factory=list)


def normalize_permission(permission: Any) -> str:
    """
    Reduce an exchange permission label to a comparable token.

    'enableWithdrawals' -> 'enablewithdrawals'; 'Withdraw Funds' -> 'withdrawfunds';
    'can_transfer' -> 'cantransfer'. Non-string input raises rather than being skipped.
    """
    if not isinstance(permission, str):
        raise TypeError(
            f"Permission entries must be strings; got {type(permission).__name__}. "
            "A non-string permission cannot be audited and must not be ignored."
        )
    return re.sub(r"[^a-z0-9]", "", permission.strip().lower())


def is_funds_moving_permission(permission: Any) -> bool:
    """True when a permission label indicates the key can move funds out of the account."""
    token = normalize_permission(permission)
    return any(stem in token for stem in FUNDS_MOVING_STEMS)


def normalize_address(address: str) -> str:
    """
    Normalize a destination address for whitelist comparison, by format.

    - EVM (0x + 40 hex): case-insensitive. EIP-55 mixed case is a CHECKSUM layered
      on a case-insensitive hex address, so the lowercase and checksummed forms are
      the same address and must compare equal.
    - Bech32 (bc1.../tb1...): BIP-173 requires all-lowercase output and forbids
      decoding mixed case, so fold to lowercase and reject mixed case.
    - Everything else (Base58Check legacy BTC, and unknown formats): compared
      EXACTLY. Base58 is a case-sensitive alphabet -- folding case there would let
      two distinct addresses collide, turning the whitelist into a fail-open.
    """
    if not isinstance(address, str):
        raise TypeError(f"Address must be a string; got {type(address).__name__}.")
    stripped = address.strip()
    if not stripped:
        raise ValueError("Address must not be empty.")

    if _EVM_ADDRESS_RE.match(stripped):
        return stripped.lower()

    if _BECH32_HRP_RE.match(stripped) and _BECH32_RE.match(stripped):
        has_lower = any(c.islower() for c in stripped)
        has_upper = any(c.isupper() for c in stripped)
        if has_lower and has_upper:
            raise ValueError(
                f"Mixed-case bech32 address is invalid per BIP-173 and cannot be "
                f"normalized safely: {stripped!r}"
            )
        return stripped.lower()

    # Base58Check and unknown formats: case is significant, compare verbatim.
    return stripped


class KeyCustodySecurityAuditor:
    """
    Security auditor enforcing API key scoping, IP whitelisting, storage backend
    security, hot/cold capital balance limits, and outbound transfer monitoring.

    Args:
        max_hot_ratio: maximum share of total portfolio value permitted in hot,
            bot-reachable balance. The 0.15 default is a POLICY default, not an
            industry standard -- no regulator or standards body sets this number.
        alert_fn: out-of-band alert channel. Delivery failures are caught and
            recorded as findings; they never abort an audit.
        multisig_threshold: transfer amount at or above which independent
            multi-signature approval is required. None disables the check.
        required_approvals: number of independent approvals a transfer at or
            above the threshold must carry.
    """

    def __init__(
        self,
        max_hot_ratio: float = 0.15,
        alert_fn: Optional[Callable[[str], None]] = None,
        multisig_threshold: Optional[float] = None,
        required_approvals: int = 2,
    ) -> None:
        if not isinstance(max_hot_ratio, (int, float)) or isinstance(max_hot_ratio, bool):
            raise TypeError("max_hot_ratio must be a number.")
        if not math.isfinite(max_hot_ratio) or not 0.0 <= max_hot_ratio <= 1.0:
            raise ValueError("max_hot_ratio must be a finite fraction in [0.0, 1.0].")
        if multisig_threshold is not None:
            if not math.isfinite(multisig_threshold) or multisig_threshold < 0:
                raise ValueError("multisig_threshold must be a non-negative finite number or None.")
        if required_approvals < 1:
            raise ValueError("required_approvals must be at least 1.")

        self.max_hot_ratio = float(max_hot_ratio)
        self.alert_fn = alert_fn or (lambda msg: logger.warning(msg))
        self.multisig_threshold = multisig_threshold
        self.required_approvals = int(required_approvals)
        self.findings: List[AuditFinding] = []

    # ---------------------------------------------------------------- internals

    def _emit_alert(self, message: str, key_name: str) -> None:
        """
        Deliver an out-of-band alert. A failing alert channel is itself a finding:
        the monitor must not die on the exact event it exists to report.
        """
        try:
            self.alert_fn(message)
        except Exception as exc:  # noqa: BLE001 - any channel failure must be contained
            logger.exception("Alert channel failed while reporting: %s", message)
            self.findings.append(
                AuditFinding(
                    key_name=key_name,
                    risk_level=RiskLevel.HIGH,
                    issue=f"Alert channel failed to deliver a security alert ({type(exc).__name__}: {exc})",
                    remediation="Repair or fail over the out-of-band alert channel; an undelivered "
                                "custody alert is equivalent to no monitoring at all.",
                )
            )

    # ------------------------------------------------------------------ public

    def reset(self) -> None:
        """Clear accumulated findings so an auditor instance can be reused per audit run."""
        self.findings = []

    def summary(self) -> AuditSummary:
        """Aggregate verdict. `passed` is True only if no CRITICAL or HIGH findings exist."""
        counts = {level: 0 for level in RiskLevel}
        for finding in self.findings:
            counts[finding.risk_level] += 1
        return AuditSummary(
            total_findings=len(self.findings),
            critical=counts[RiskLevel.CRITICAL],
            high=counts[RiskLevel.HIGH],
            medium=counts[RiskLevel.MEDIUM],
            low=counts[RiskLevel.LOW],
            passed=counts[RiskLevel.CRITICAL] == 0 and counts[RiskLevel.HIGH] == 0,
            findings=list(self.findings),
        )

    def audit_key_config(self, key_config: Dict[str, Any]) -> List[AuditFinding]:
        """
        Audits a single API key config dict:
        {"name": str, "used_by": str, "permissions": List[str],
         "ip_whitelisted": bool, "storage_backend": str}

        Fails closed throughout. A key carrying a funds-moving permission is always
        reported: CRITICAL when the key is attributable to an automated process or
        its owner is unstated, HIGH when it is attributed to a human-gated process.
        """
        if not isinstance(key_config, dict):
            raise TypeError(f"key_config must be a dict; got {type(key_config).__name__}.")

        name = str(key_config.get("name", "unnamed_key"))
        used_by_raw = key_config.get("used_by")
        used_by = "" if used_by_raw is None else str(used_by_raw)
        used_by_token = re.sub(r"[^a-z0-9]", "", used_by.strip().lower())

        raw_permissions = key_config.get("permissions", [])
        if isinstance(raw_permissions, str):
            raise TypeError(
                "permissions must be a sequence of strings, not a bare string "
                f"({raw_permissions!r}) -- a string iterates as characters and would "
                "silently match nothing."
            )
        if raw_permissions is None:
            raise TypeError("permissions must be a sequence of strings, not None.")
        if not isinstance(raw_permissions, Sequence):
            raise TypeError(f"permissions must be a sequence; got {type(raw_permissions).__name__}.")

        findings: List[AuditFinding] = []

        # 1. Funds-moving permission scoping.
        funds_moving = sorted({str(p) for p in raw_permissions if is_funds_moving_permission(p)})
        if funds_moving:
            is_automated = any(stem in used_by_token for stem in AUTOMATED_ACTOR_STEMS)
            is_unattributed = used_by_token in ("", "unknown", "unspecified")
            if is_automated or is_unattributed:
                reason = (
                    "an automated process" if is_automated
                    else "an unattributed process (no 'used_by' recorded)"
                )
                findings.append(AuditFinding(
                    key_name=name,
                    risk_level=RiskLevel.CRITICAL,
                    issue=f"Key held by {reason} carries funds-moving permission(s): "
                          f"{', '.join(funds_moving)}",
                    remediation="Split into a trade-only key for the bot and a separately held, "
                                "human-gated withdrawal key. Record 'used_by' for every key so "
                                "attribution is never inferred.",
                ))
            else:
                findings.append(AuditFinding(
                    key_name=name,
                    risk_level=RiskLevel.HIGH,
                    issue=f"Key attributed to '{used_by}' carries funds-moving permission(s): "
                          f"{', '.join(funds_moving)}",
                    remediation="Confirm this key is human-gated, IP-restricted and limited to "
                                "whitelisted destination addresses; it can move funds irreversibly.",
                ))

        # 2. IP whitelisting.
        ip_whitelisted = key_config.get("ip_whitelisted", False)
        if ip_whitelisted is not True:
            findings.append(AuditFinding(
                key_name=name,
                risk_level=RiskLevel.HIGH,
                issue="API key missing IP address whitelisting restriction",
                remediation="Configure a static IP allowlist on the exchange API management panel. "
                            "Binance.US additionally resets keys to read-only after 90 days unused "
                            "when not secured by IP whitelisting.",
            ))

        # 3. Storage backend, checked against an allowlist.
        backend_raw = key_config.get("storage_backend")
        backend_token = normalize_permission(
            backend_raw.value if isinstance(backend_raw, StorageBackend)
            else ("" if backend_raw is None else str(backend_raw))
        )
        secure_tokens = {normalize_permission(b) for b in SECURE_BACKENDS}
        if backend_token not in secure_tokens:
            known_insecure = {
                normalize_permission(StorageBackend.PLAINTEXT_FILE.value),
                normalize_permission(StorageBackend.ENV_VARIABLE.value),
            }
            if backend_token in known_insecure:
                issue = f"Secret stored in insecure backend ({backend_raw})"
            elif not backend_token:
                issue = "No storage backend declared for this key's secret"
            else:
                issue = (f"Unrecognized storage backend ({backend_raw!r}) - treated as insecure "
                         "because it cannot be confirmed to provide managed key protection")
            findings.append(AuditFinding(
                key_name=name,
                risk_level=RiskLevel.HIGH,
                issue=issue,
                remediation="Migrate the secret to Cloud KMS (AWS/GCP/Azure), HashiCorp Vault, "
                            "or a hardware HSM, and declare the backend explicitly.",
            ))

        self.findings.extend(findings)
        return findings

    def evaluate_hot_cold_allocation(
        self,
        hot_balance: float,
        total_balance: float,
    ) -> Tuple[bool, float, Optional[AuditFinding]]:
        """
        Evaluates operational hot balance against total holdings.

        Never reports "safe" for a ratio it could not determine. A zero, negative or
        non-finite total with a non-zero hot balance is incoherent input, not a pass.
        """
        for label, value in (("hot_balance", hot_balance), ("total_balance", total_balance)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{label} must be a number; got {type(value).__name__}.")
            if not math.isfinite(value):
                raise ValueError(f"{label} must be finite; got {value!r}.")
            if value < 0:
                raise ValueError(f"{label} must be non-negative; got {value!r}.")

        if total_balance == 0:
            if hot_balance == 0:
                return True, 0.0, None
            finding = AuditFinding(
                key_name="hot_wallet_balance",
                risk_level=RiskLevel.HIGH,
                issue=f"Hot balance of {hot_balance} reported against a total balance of 0 - "
                      "the hot/cold ratio is undeterminable and cannot be treated as safe",
                remediation="Correct the balance feed; a hot balance cannot exceed total holdings.",
            )
            self.findings.append(finding)
            self._emit_alert(f"SECURITY ALERT: {finding.issue}", finding.key_name)
            return False, float("inf"), finding

        ratio = hot_balance / total_balance
        if ratio <= self.max_hot_ratio:
            return True, ratio, None

        finding = AuditFinding(
            key_name="hot_wallet_balance",
            risk_level=RiskLevel.HIGH,
            issue=f"Hot balance ratio ({ratio*100:.1f}%) exceeds maximum limit "
                  f"({self.max_hot_ratio*100:.1f}%)",
            remediation="Sweep excess operational capital to offline cold storage.",
        )
        self.findings.append(finding)
        self._emit_alert(f"SECURITY ALERT: {finding.issue}", finding.key_name)
        return False, ratio, finding

    def audit_outbound_transfer(
        self,
        destination_address: str,
        amount: float,
        approved_whitelist: Set[str],
    ) -> bool:
        """
        Inspects an outbound transfer against the pre-approved address whitelist.

        Addresses are normalized per format before comparison (see normalize_address):
        an EIP-55 checksummed EVM address matches its lowercase whitelist entry,
        while Base58 addresses are compared exactly. Returns True only for an
        approved destination with a valid positive amount.
        """
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            raise TypeError(f"amount must be a number; got {type(amount).__name__}.")
        if not math.isfinite(amount):
            raise ValueError(f"amount must be finite; got {amount!r}.")
        if amount <= 0:
            raise ValueError(f"amount must be positive; got {amount!r}.")

        try:
            normalized_destination = normalize_address(destination_address)
        except (TypeError, ValueError) as exc:
            msg = (f"CRITICAL SECURITY ALERT: Outbound transfer of {amount} to an "
                   f"unparseable destination address ({exc}) - treated as UNAPPROVED")
            logger.critical(msg)
            self.findings.append(AuditFinding(
                key_name="outbound_transfer",
                risk_level=RiskLevel.CRITICAL,
                issue=f"Unparseable destination address on an outbound transfer of {amount}",
                remediation="Reject the transfer and investigate the source of the malformed address.",
            ))
            self._emit_alert(msg, "outbound_transfer")
            return False

        normalized_whitelist = set()
        for entry in approved_whitelist:
            try:
                normalized_whitelist.add(normalize_address(entry))
            except (TypeError, ValueError):
                logger.error("Skipping unparseable whitelist entry: %r", entry)
                self.findings.append(AuditFinding(
                    key_name="outbound_transfer",
                    risk_level=RiskLevel.HIGH,
                    issue=f"Unparseable entry in the approved address whitelist: {entry!r}",
                    remediation="Repair the whitelist; an unparseable entry silently shrinks the "
                                "set of destinations that can be approved.",
                ))

        if normalized_destination not in normalized_whitelist:
            msg = (f"CRITICAL SECURITY ALERT: Outbound transfer of {amount} to UNAPPROVED "
                   f"address {destination_address} detected!")
            logger.critical(msg)
            self.findings.append(AuditFinding(
                key_name="outbound_transfer",
                risk_level=RiskLevel.CRITICAL,
                issue=f"Outbound transfer of {amount} to unapproved address {destination_address}",
                remediation="Block the transfer, rotate the credential that initiated it, and "
                            "verify the withdrawal address whitelist on the exchange.",
            ))
            self._emit_alert(msg, "outbound_transfer")
            return False
        return True

    def evaluate_transfer_approval(
        self,
        amount: float,
        approvals_present: int,
    ) -> Tuple[bool, Optional[AuditFinding]]:
        """
        Enforces the multi-signature approval threshold described in the skill workflow.

        Transfers at or above `multisig_threshold` require `required_approvals`
        independent approvals. Boundary is inclusive: an amount exactly equal to the
        threshold requires approval. Returns (is_approved, finding).
        """
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            raise TypeError(f"amount must be a number; got {type(amount).__name__}.")
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError(f"amount must be a positive finite number; got {amount!r}.")
        if not isinstance(approvals_present, int) or isinstance(approvals_present, bool):
            raise TypeError("approvals_present must be an int.")
        if approvals_present < 0:
            raise ValueError("approvals_present must be non-negative.")

        if self.multisig_threshold is None or amount < self.multisig_threshold:
            return True, None

        if approvals_present >= self.required_approvals:
            return True, None

        finding = AuditFinding(
            key_name="outbound_transfer",
            risk_level=RiskLevel.CRITICAL,
            issue=f"Transfer of {amount} at or above the multi-signature threshold "
                  f"({self.multisig_threshold}) carries {approvals_present} of "
                  f"{self.required_approvals} required independent approvals",
            remediation="Hold the transfer until the required independent approvals are "
                        "collected outside the trading system's own control path.",
        )
        self.findings.append(finding)
        self._emit_alert(f"SECURITY ALERT: {finding.issue}", finding.key_name)
        return False, finding


# Backward compatibility functions
def audit_key_permissions(key_configs: Sequence[Dict[str, Any]]) -> List[str]:
    """
    key_configs: [{"name": str, "used_by": str, "permissions": [str]}]

    Returns human-readable messages for CRITICAL findings only. Note that configs
    omitting 'ip_whitelisted'/'storage_backend' also generate HIGH findings, which
    this legacy shape does not surface -- use KeyCustodySecurityAuditor.summary()
    for the complete verdict.
    """
    auditor = KeyCustodySecurityAuditor()
    messages: List[str] = []
    for cfg in key_configs:
        for finding in auditor.audit_key_config(cfg):
            if finding.risk_level == RiskLevel.CRITICAL:
                messages.append(
                    f"Key '{cfg.get('name')}' used by '{cfg.get('used_by', 'unknown')}' has "
                    f"withdraw permission -- split into a trade-only key and a "
                    f"separately-controlled withdraw key. ({finding.issue})"
                )
    return messages


def check_hot_balance_ratio(
    hot_balance: float,
    total_balance: float,
    max_hot_ratio: float = 0.15,
) -> Tuple[bool, float]:
    """Legacy helper. Delegates to the auditor so it shares the fail-closed semantics."""
    auditor = KeyCustodySecurityAuditor(max_hot_ratio=max_hot_ratio)
    is_safe, ratio, _ = auditor.evaluate_hot_cold_allocation(hot_balance, total_balance)
    return is_safe, ratio
