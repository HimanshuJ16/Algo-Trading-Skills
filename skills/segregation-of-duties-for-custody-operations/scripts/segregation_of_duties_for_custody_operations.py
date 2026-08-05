import hashlib
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

@dataclass
class SegregationOfDutiesForCustodyOperationsConfig:
    """Legacy config container for backward compatibility."""
    enabled: bool = True

class CustodyRole(str, Enum):
    INITIATOR = "INITIATOR"             # Maker: can propose transfers
    APPROVER = "APPROVER"               # Checker: can approve transfers
    SECURITY_ADMIN = "SECURITY_ADMIN"   # Admin: manages policies
    AUDITOR = "AUDITOR"                 # Read-only audit access

class SoDViolationType(str, Enum):
    SELF_APPROVAL_ATTEMPT = "SELF_APPROVAL_ATTEMPT"
    UNAUTHORIZED_ROLE = "UNAUTHORIZED_ROLE"
    THRESHOLD_NOT_MET = "THRESHOLD_NOT_MET"
    ROLE_CONFLICT_ADMIN_MAKER = "ROLE_CONFLICT_ADMIN_MAKER"

class SoDConflictError(Exception):
    """Raised when a Segregation of Duties policy is breached."""
    pass

@dataclass
class UserIdentity:
    user_id: str
    username: str
    department: str
    roles: Set[CustodyRole]

@dataclass
class ApprovalRecord:
    approver_id: str
    approved_at: float
    signature_hash: str

@dataclass
class CustodyTransferProposal:
    proposal_id: str
    initiator_id: str
    destination_address: str
    asset_symbol: str
    amount_usd: float
    required_approvals: int = 2
    approvals: List[ApprovalRecord] = field(default_factory=list)
    status: str = "PENDING"              # PENDING, APPROVED, REJECTED
    created_at: float = field(default_factory=time.time)

class SegregationOfDutiesForCustodyOperationsEngine:
    """
    Production-grade Segregation of Duties (SoD) engine for institutional crypto custody operations.
    Enforces Maker-Checker dual control, M-of-N approvals, RBAC restrictions, and self-approval blocks.
    """
    def __init__(
        self,
        config: Optional[SegregationOfDutiesForCustodyOperationsConfig] = None,
        large_transfer_threshold_usd: float = 50000.0
    ):
        self.config = config or SegregationOfDutiesForCustodyOperationsConfig(enabled=True)
        self.large_transfer_threshold_usd = large_transfer_threshold_usd
        self.users: Dict[str, UserIdentity] = {}
        self.proposals: Dict[str, CustodyTransferProposal] = {}

    def execute(self) -> bool:
        """Legacy execute method retained for 100% backward compatibility."""
        return True if self.config.enabled else False

    def register_user(self, user: UserIdentity):
        """Registers a user identity with defined RBAC roles."""
        # Enforcement: Admin cannot also hold Initiator role to prevent policy tampering + transfer execution
        if CustodyRole.SECURITY_ADMIN in user.roles and CustodyRole.INITIATOR in user.roles:
            raise SoDConflictError(f"User {user.user_id} cannot combine SECURITY_ADMIN and INITIATOR roles (SoD conflict).")
        self.users[user.user_id] = user

    def propose_transfer(
        self,
        proposal_id: str,
        initiator_id: str,
        destination_address: str,
        asset_symbol: str,
        amount_usd: float
    ) -> CustodyTransferProposal:
        """
        Creates a new custody transfer proposal.
        Requires INITIATOR role. Sets required approvals (2 for large transfers).
        """
        if not self.config.enabled:
            raise SoDConflictError("SoD Engine is disabled.")

        initiator = self.users.get(initiator_id)
        if not initiator or CustodyRole.INITIATOR not in initiator.roles:
            raise SoDConflictError(f"User '{initiator_id}' does not have INITIATOR role.")

        required_approvals = 2 if amount_usd >= self.large_transfer_threshold_usd else 1

        proposal = CustodyTransferProposal(
            proposal_id=proposal_id,
            initiator_id=initiator_id,
            destination_address=destination_address,
            asset_symbol=asset_symbol,
            amount_usd=amount_usd,
            required_approvals=required_approvals
        )
        self.proposals[proposal_id] = proposal

        logger.info(f"PROPOSAL CREATED [{proposal_id}]: Amount = ${amount_usd:,.2f}, Initiator = {initiator_id}, Required Approvals = {required_approvals}.")
        return proposal

    def approve_transfer(
        self,
        proposal_id: str,
        approver_id: str
    ) -> CustodyTransferProposal:
        """
        Approves a custody transfer proposal.
        STRICT SOD ENFORCEMENT: Initiator CANNOT approve their own transfer (Maker-Checker violation).
        """
        proposal = self.proposals.get(proposal_id)
        if not proposal:
            raise ValueError(f"Proposal '{proposal_id}' not found.")

        # 1. Maker-Checker Self-Approval Check
        if approver_id == proposal.initiator_id:
            msg = f"SoD VIOLATION: Initiator '{approver_id}' cannot approve their own transfer proposal '{proposal_id}'."
            logger.error(msg)
            raise SoDConflictError(msg)

        # 2. Approver RBAC Role Check
        approver = self.users.get(approver_id)
        if not approver or CustodyRole.APPROVER not in approver.roles:
            raise SoDConflictError(f"User '{approver_id}' does not have APPROVER role.")

        # 3. Duplicate Approval Check
        if any(a.approver_id == approver_id for a in proposal.approvals):
            raise SoDConflictError(f"Approver '{approver_id}' has already approved proposal '{proposal_id}'.")

        # 4. Generate Cryptographic Signature Hash
        sig_data = f"{proposal_id}:{approver_id}:{time.time()}"
        sig_hash = hashlib.sha256(sig_data.encode()).hexdigest()

        proposal.approvals.append(ApprovalRecord(
            approver_id=approver_id,
            approved_at=time.time(),
            signature_hash=sig_hash
        ))

        # 5. Check if Threshold Met
        if len(proposal.approvals) >= proposal.required_approvals:
            proposal.status = "APPROVED"

        logger.info(f"PROPOSAL APPROVED [{proposal_id}]: Approver = {approver_id}, Approvals = {len(proposal.approvals)}/{proposal.required_approvals}, Status = {proposal.status}.")
        return proposal
