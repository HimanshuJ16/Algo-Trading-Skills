"""
smart-contract-approval-scope-minimization: policy engine that decides *what* ERC-20
allowance an automated trading wallet should grant a DeFi spender, and which existing
allowances must be revoked.

The engine is a planner, not a signer and not an RPC client. It emits
``ApprovalTransactionPlan`` objects describing the calls a caller should make; it never
builds calldata, never signs, and never broadcasts. Everything it decides is derived
from arguments the caller supplies.

Three rules drive every decision:

1. **Unlimited approvals are refused.** ``type(uint256).max`` and Permit2's
   ``type(uint160).max`` sentinel are rejected at plan time by raising
   ``UnlimitedApprovalBlocked``. Failing closed is deliberate: a caller that ignored a
   returned status field would grant an infinite allowance, which is the exact failure
   this skill exists to prevent.
2. **Exact sizing.** The recommended allowance is the transaction notional the caller
   asked for, in the token's base units - never a rounded-up or "convenient" figure.
3. **Zero-reset before re-approval.** EIP-20 warns that changing a non-zero allowance
   to another non-zero value lets the spender front-run the change and spend both
   amounts, and advises clients to set the allowance to 0 first. Some widely used
   tokens - USDT being the canonical example - enforce this at the contract level and
   revert otherwise.

EIP-2612 permits (ERC-2612):

    permit(address owner, address spender, uint256 value, uint256 deadline,
           uint8 v, bytes32 r, bytes32 s)

``deadline`` is a **uint256 count of seconds** compared against ``block.timestamp``, so
``permit_deadline_unix`` is emitted as an ``int``. A permit overwrites the allowance in
a single signed call, so no zero-reset step applies. ERC-2612 prescribes no maximum
deadline; the cap enforced here is this library's own policy
(``max_permit_validity_seconds``), not a standard.

Limitations (documented, deliberate):

- **No chain access.** ``current_allowance`` must be read from the chain by the caller
  and passed in. A plan built from a stale allowance can emit the wrong zero-reset
  decision; re-read immediately before submitting.
- **No signing and no EIP-712 domain construction.** To use an ``EIP_2612_PERMIT`` plan
  the caller must still fetch ``nonces(owner)`` from the token and assemble the
  ``DOMAIN_SEPARATOR`` (name, version, chainId, verifying contract) itself. Chain ID is
  not modelled here, so this engine cannot protect against cross-chain replay.
- **Address format only.** Addresses are checked against ``0x`` + 40 hex digits. EIP-55
  checksum verification needs keccak-256, which the standard library does not provide
  (``hashlib.sha3_256`` is NIST SHA-3, a different padding), and no dependency is added
  for it. A correctly formatted but wrong address will pass.
- **"Unlimited" detection is heuristic above the known sentinels.** Beyond the exact
  ``uint256``/``uint160`` maxima, an allowance is flagged when it reaches
  ``unlimited_allowance_threshold``. Token decimals are not modelled, so that threshold
  is an absolute integer and should be reviewed per deployment.
- **Staleness is caller-supplied.** The engine has no view of transfer history; it
  compares ``TokenAllowance.last_used_unix`` against a caller-chosen window.
"""
import logging
import math
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

#: type(uint256).max - the canonical "infinite" ERC-20 allowance.
MAX_UINT256 = (1 << 256) - 1

#: type(uint160).max - Uniswap Permit2 treats this exact value as an unlimited
#: allowance and skips decrementing it on transfer (Uniswap/permit2,
#: AllowanceTransfer.sol). All Permit2 amounts are uint160, so uint256.max never
#: appears there and a uint256-only check misses those approvals entirely.
MAX_UINT160 = (1 << 160) - 1

#: Exact values that mean "unlimited" rather than "a very large amount".
UNLIMITED_SENTINELS = frozenset({MAX_UINT256, MAX_UINT160})

#: Absolute allowance at or above which an approval is treated as effectively
#: unlimited. A house heuristic, not a standard: 2**200 base units exceeds the total
#: supply of every mainstream ERC-20 by many orders of magnitude at any plausible
#: decimals.
DEFAULT_UNLIMITED_THRESHOLD = 1 << 200

#: Longest permit deadline this library will plan. ERC-2612 sets no such limit; this is
#: a policy default chosen so that a leaked signature expires quickly.
DEFAULT_MAX_PERMIT_VALIDITY_SECONDS = 600.0

#: Default permit validity when the caller does not specify one.
DEFAULT_PERMIT_VALIDITY_SECONDS = 300.0

_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_ZERO_ADDRESS = "0x" + "0" * 40


class UnlimitedApprovalBlocked(ValueError):
    """Raised when an unlimited (or effectively unlimited) allowance is requested."""


class ApprovalType(str, Enum):
    EXACT_AMOUNT = "EXACT_AMOUNT"            # Single-use exact transaction notional
    EIP_2612_PERMIT = "EIP_2612_PERMIT"      # Off-chain signed permit with deadline
    UNLIMITED_BLOCKED = "UNLIMITED_BLOCKED"  # Refused: uint256/uint160 max or over threshold


@dataclass
class SmartContractApprovalScopeMinimizationConfig:
    """Engine policy. ``enabled`` is retained from the original API."""
    enabled: bool = True
    max_permit_validity_seconds: float = DEFAULT_MAX_PERMIT_VALIDITY_SECONDS
    unlimited_allowance_threshold: int = DEFAULT_UNLIMITED_THRESHOLD

    def __post_init__(self) -> None:
        # A threshold of 0 would classify every amount - including a revocation to 0 -
        # as unlimited, turning the safety check into a total outage.
        if not isinstance(self.unlimited_allowance_threshold, int) or self.unlimited_allowance_threshold <= 0:
            raise ValueError("unlimited_allowance_threshold must be a positive int")
        if not self.max_permit_validity_seconds > 0:
            raise ValueError("max_permit_validity_seconds must be positive")


@dataclass
class TokenAllowance:
    token_address: str
    spender_address: str
    current_allowance: int
    is_unlimited: bool = False
    #: Unix seconds of the last observed use of this allowance, supplied by the caller
    #: from transfer history. ``None`` means "unknown" and is never treated as stale.
    last_used_unix: Optional[float] = None


@dataclass
class ApprovalTransactionPlan:
    token_address: str
    spender_address: str
    recommended_approval_amount: int
    requires_reset_to_zero_first: bool
    approval_type: ApprovalType
    permit_deadline_unix: Optional[int] = None
    audit_notes: str = ""
    #: False when the on-chain allowance already equals the requested amount, so no
    #: transaction needs to be sent at all.
    approval_transaction_needed: bool = True


def _validate_address(value: str, label: str) -> str:
    """Validates EVM address *format* (0x + 40 hex). Checksum is not verified."""
    if not isinstance(value, str) or not _ADDRESS_RE.match(value):
        raise ValueError(f"{label} must be a 0x-prefixed 40-hex-digit address, got {value!r}")
    if value.lower() == _ZERO_ADDRESS:
        raise ValueError(f"{label} must not be the zero address")
    return value


def _validate_amount(value: int, label: str) -> int:
    """Validates a token amount in base units: a non-negative, uint256-encodable int."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an int in token base units, got {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{label} must be non-negative, got {value}")
    if value > MAX_UINT256:
        raise ValueError(f"{label} exceeds uint256 and cannot be encoded on-chain")
    return value


class SmartContractApprovalScopeMinimizationEngine:
    """
    Plans minimum-scope ERC-20 approvals: exact sizing, EIP-2612 permits where the token
    supports them, EIP-20 zero-reset before re-approval, and revocation of unlimited or
    stale allowances.
    """

    def __init__(
        self,
        config: Optional[SmartContractApprovalScopeMinimizationConfig] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config or SmartContractApprovalScopeMinimizationConfig()
        self._clock = clock
        self.active_allowances: Dict[str, TokenAllowance] = {}

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _key(token_address: str, spender_address: str) -> str:
        return f"{token_address.lower()}:{spender_address.lower()}"

    def execute(self) -> bool:
        """Legacy health check retained for backward compatibility."""
        return bool(self.config.enabled)

    def record_allowance(self, allowance: TokenAllowance) -> None:
        """
        Records an allowance read from the chain so ``plan_approval`` can decide whether
        a zero-reset is required. Without this - or an explicit ``current_allowance``
        argument - the engine assumes the current allowance is 0.
        """
        _validate_address(allowance.token_address, "token_address")
        _validate_address(allowance.spender_address, "spender_address")
        _validate_amount(allowance.current_allowance, "current_allowance")
        key = self._key(allowance.token_address, allowance.spender_address)
        self.active_allowances[key] = allowance

    def is_effectively_unlimited(self, amount: int) -> bool:
        """True for the known unlimited sentinels or any amount at/above the threshold."""
        return amount in UNLIMITED_SENTINELS or amount >= self.config.unlimited_allowance_threshold

    def classify_requested_amount(self, required_amount: int) -> ApprovalType:
        """
        Non-throwing classification of a requested amount. Returns
        ``UNLIMITED_BLOCKED`` for values ``plan_approval`` would refuse.
        """
        _validate_amount(required_amount, "required_amount")
        if self.is_effectively_unlimited(required_amount):
            return ApprovalType.UNLIMITED_BLOCKED
        return ApprovalType.EXACT_AMOUNT

    # ------------------------------------------------------------------ planning

    def plan_approval(
        self,
        token_address: str,
        spender_address: str,
        required_amount: int,
        supports_eip2612_permit: bool = False,
        permit_validity_seconds: float = DEFAULT_PERMIT_VALIDITY_SECONDS,
        current_allowance: Optional[int] = None,
    ) -> ApprovalTransactionPlan:
        """
        Plans one approval.

        Raises ``UnlimitedApprovalBlocked`` for unlimited requests, ``ValueError`` or
        ``TypeError`` for malformed input, and ``RuntimeError`` if the engine is
        disabled.

        ``current_allowance`` overrides any value from ``record_allowance``; pass the
        value read from the chain immediately before submitting.
        """
        if not self.config.enabled:
            raise RuntimeError("Engine is disabled.")

        _validate_address(token_address, "token_address")
        _validate_address(spender_address, "spender_address")
        _validate_amount(required_amount, "required_amount")

        if self.is_effectively_unlimited(required_amount):
            msg = (
                f"BLOCKED unlimited approval of {required_amount} for spender "
                f"{spender_address} on token {token_address}: size the allowance to the "
                f"exact transaction notional."
            )
            logger.error(msg)
            raise UnlimitedApprovalBlocked(msg)

        if current_allowance is None:
            existing = self.active_allowances.get(self._key(token_address, spender_address))
            current_allowance = existing.current_allowance if existing else 0
        _validate_amount(current_allowance, "current_allowance")

        if supports_eip2612_permit:
            if not permit_validity_seconds > 0:
                raise ValueError("permit_validity_seconds must be positive")
            if permit_validity_seconds > self.config.max_permit_validity_seconds:
                raise ValueError(
                    f"permit_validity_seconds {permit_validity_seconds} exceeds the policy cap "
                    f"of {self.config.max_permit_validity_seconds}s"
                )
            # ERC-2612 deadline is a uint256 second count compared to block.timestamp.
            # Rounded up so the deadline is never shorter than the requested window;
            # a sub-second validity would otherwise truncate to an already-expired one.
            deadline = int(self._clock()) + math.ceil(permit_validity_seconds)
            plan = ApprovalTransactionPlan(
                token_address=token_address,
                spender_address=spender_address,
                recommended_approval_amount=required_amount,
                # A permit overwrites the allowance in one signed call, so the EIP-20
                # front-running window never opens and no zero-reset applies.
                requires_reset_to_zero_first=False,
                approval_type=ApprovalType.EIP_2612_PERMIT,
                permit_deadline_unix=deadline,
                audit_notes=(
                    f"EIP-2612 PERMIT PLAN: exact amount {required_amount}, deadline "
                    f"{deadline} ({permit_validity_seconds}s). Caller must fetch "
                    f"nonces(owner) and build the EIP-712 domain before signing."
                ),
            )
            logger.info(plan.audit_notes)
            return plan

        # Standard approve() path. EIP-20 advises setting a non-zero allowance to 0
        # before changing it to another non-zero value; tokens such as USDT enforce it.
        needs_zero_reset = (
            current_allowance > 0
            and required_amount > 0
            and current_allowance != required_amount
        )
        already_correct = current_allowance == required_amount

        plan = ApprovalTransactionPlan(
            token_address=token_address,
            spender_address=spender_address,
            recommended_approval_amount=required_amount,
            requires_reset_to_zero_first=needs_zero_reset,
            approval_type=ApprovalType.EXACT_AMOUNT,
            permit_deadline_unix=None,
            audit_notes=(
                f"EXACT APPROVAL PLAN: amount {required_amount}, current {current_allowance}, "
                f"reset-to-zero-first={needs_zero_reset}, "
                f"transaction-needed={not already_correct}."
            ),
            approval_transaction_needed=not already_correct,
        )
        logger.info(plan.audit_notes)
        return plan

    # ------------------------------------------------------------------ auditing

    def audit_allowances(
        self,
        allowances: List[TokenAllowance],
        stale_after_seconds: Optional[float] = None,
    ) -> List[ApprovalTransactionPlan]:
        """
        Returns ``approve(spender, 0)`` revocation plans for every allowance that is
        unlimited - an exact sentinel, at/above the threshold, or flagged
        ``is_unlimited`` - and, when ``stale_after_seconds`` is given, for every
        non-zero allowance whose ``last_used_unix`` is older than that window.

        Allowances already at 0 are never planned. ``last_used_unix is None`` means
        "unknown" and is not treated as stale; a caller who wants unknown-age approvals
        revoked should set the timestamp explicitly.
        """
        if stale_after_seconds is not None and not stale_after_seconds > 0:
            raise ValueError("stale_after_seconds must be positive when provided")

        now = self._clock()
        revocation_plans: List[ApprovalTransactionPlan] = []

        for alloc in allowances:
            # A malformed address in the audit inventory means the chain-read that
            # produced it is broken; a revocation plan built on it is unsubmittable.
            _validate_address(alloc.token_address, "token_address")
            _validate_address(alloc.spender_address, "spender_address")
            _validate_amount(alloc.current_allowance, "current_allowance")
            if alloc.current_allowance == 0:
                continue

            unlimited = alloc.is_unlimited or self.is_effectively_unlimited(alloc.current_allowance)
            stale = (
                stale_after_seconds is not None
                and alloc.last_used_unix is not None
                and (now - alloc.last_used_unix) > stale_after_seconds
            )
            if not (unlimited or stale):
                continue

            if unlimited:
                reason = f"unlimited allowance {alloc.current_allowance}"
            else:
                idle_seconds = now - float(alloc.last_used_unix)
                reason = (
                    f"stale allowance unused for {idle_seconds:.0f}s "
                    f"(limit {stale_after_seconds:.0f}s)"
                )

            plan = ApprovalTransactionPlan(
                token_address=alloc.token_address,
                spender_address=alloc.spender_address,
                recommended_approval_amount=0,
                # Revoking to 0 is a single approve(0); the EIP-20 race-condition advice
                # applies to non-zero -> non-zero changes only.
                requires_reset_to_zero_first=False,
                approval_type=ApprovalType.EXACT_AMOUNT,
                audit_notes=(
                    f"REVOCATION REQUIRED: {reason} on spender {alloc.spender_address} "
                    f"for token {alloc.token_address}."
                ),
            )
            revocation_plans.append(plan)
            logger.warning(plan.audit_notes)

        return revocation_plans

    def audit_and_revoke_unlimited_allowances(
        self,
        allowances: List[TokenAllowance],
    ) -> List[ApprovalTransactionPlan]:
        """Backward-compatible wrapper: unlimited-allowance revocations only."""
        return self.audit_allowances(allowances, stale_after_seconds=None)
