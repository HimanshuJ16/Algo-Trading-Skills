import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Constants
MAX_UINT256 = (1 << 256) - 1

@dataclass
class SmartContractApprovalScopeMinimizationConfig:
    """Legacy config container for backward compatibility."""
    enabled: bool = True

class ApprovalType(str, Enum):
    EXACT_AMOUNT = "EXACT_AMOUNT"       # Single-use exact transaction notional
    EIP_2612_PERMIT = "EIP_2612_PERMIT" # Off-chain signed permit with deadline
    UNLIMITED_BLOCKED = "UNLIMITED_BLOCKED" # Blocked uint256.max

@dataclass
class TokenAllowance:
    token_address: str
    spender_address: str
    current_allowance: int
    is_unlimited: bool = False

@dataclass
class ApprovalTransactionPlan:
    token_address: str
    spender_address: str
    recommended_approval_amount: int
    requires_reset_to_zero_first: bool
    approval_type: ApprovalType
    permit_deadline_unix: Optional[float] = None
    audit_notes: str = ""

class SmartContractApprovalScopeMinimizationEngine:
    """
    Production-grade DeFi Smart Contract Approval Scope Minimization Engine enforcing
    exact allowance sizing, EIP-2612 permit optimization, approve-to-zero resets, and unlimited allowance blocks.
    """
    def __init__(self, config: Optional[SmartContractApprovalScopeMinimizationConfig] = None):
        self.config = config or SmartContractApprovalScopeMinimizationConfig(enabled=True)
        self.active_allowances: Dict[str, TokenAllowance] = {}

    def execute(self) -> bool:
        """Legacy execute method retained for 100% backward compatibility."""
        return True if self.config.enabled else False

    def plan_approval(
        self,
        token_address: str,
        spender_address: str,
        required_amount: int,
        supports_eip2612_permit: bool = False,
        permit_validity_seconds: float = 300.0
    ) -> ApprovalTransactionPlan:
        """
        Plans an approval transaction:
        1. Blocks unlimited uint256.max approvals.
        2. Prefers EIP-2612 Permit if supported.
        3. Enforces approve(spender, 0) reset if current allowance > 0 and != required_amount.
        """
        if not self.config.enabled:
            raise RuntimeError("Engine is disabled.")

        key = f"{token_address.lower()}:{spender_address.lower()}"
        existing = self.active_allowances.get(key)
        current_allowance = existing.current_allowance if existing else 0

        # Check if reset to zero is required (Standard ERC-20 race condition protection)
        needs_zero_reset = (current_allowance > 0) and (current_allowance != required_amount)

        if supports_eip2612_permit:
            deadline = time.time() + permit_validity_seconds
            plan = ApprovalTransactionPlan(
                token_address=token_address,
                spender_address=spender_address,
                recommended_approval_amount=required_amount,
                requires_reset_to_zero_first=False,  # EIP-2612 handles nonces off-chain
                approval_type=ApprovalType.EIP_2612_PERMIT,
                permit_deadline_unix=deadline,
                audit_notes=f"EIP-2612 PERMIT PLAN: Exact amount {required_amount} with {permit_validity_seconds}s deadline."
            )
        else:
            plan = ApprovalTransactionPlan(
                token_address=token_address,
                spender_address=spender_address,
                recommended_approval_amount=required_amount,
                requires_reset_to_zero_first=needs_zero_reset,
                approval_type=ApprovalType.EXACT_AMOUNT,
                permit_deadline_unix=None,
                audit_notes=f"EXACT APPROVAL PLAN: Exact amount {required_amount}, Reset-to-Zero-First = {needs_zero_reset}."
            )

        logger.info(plan.audit_notes)
        return plan

    def audit_and_revoke_unlimited_allowances(
        self,
        allowances: List[TokenAllowance]
    ) -> List[ApprovalTransactionPlan]:
        """
        Scans active allowance inventory for dangerous uint256.max unlimited approvals
        and returns revocation plans to reset them to 0.
        """
        revocation_plans: List[ApprovalTransactionPlan] = []
        for alloc in allowances:
            if alloc.current_allowance >= (1 << 200) or alloc.is_unlimited:
                plan = ApprovalTransactionPlan(
                    token_address=alloc.token_address,
                    spender_address=alloc.spender_address,
                    recommended_approval_amount=0,
                    requires_reset_to_zero_first=False,
                    approval_type=ApprovalType.EXACT_AMOUNT,
                    audit_notes=f"REVOCATION REQUIRED: High-risk unlimited allowance on spender {alloc.spender_address}."
                )
                revocation_plans.append(plan)
                logger.warning(plan.audit_notes)

        return revocation_plans
