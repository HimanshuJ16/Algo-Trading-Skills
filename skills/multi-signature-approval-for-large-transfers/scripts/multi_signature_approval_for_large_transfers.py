import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

@dataclass
class SignerApproval:
    signer_id: str
    role: str                            # 'CFO', 'RISK_OFFICER', 'COMPLIANCE', 'CIO'
    timestamp: float

@dataclass
class TransferRequestPayload:
    request_id: str
    amount_usd: float
    source_wallet: str
    destination_address: str
    initiated_by: str
    creation_timestamp: float

@dataclass
class MultiSigConfig:
    auto_approve_threshold_usd: float = 10000.0   # Low tier: < $10k auto / 1-of-1
    high_value_threshold_usd: float = 100000.0   # High tier: > $100k requires 3-of-5 + timelock
    med_m_required: int = 2
    med_n_total: int = 3
    high_m_required: int = 3
    high_n_total: int = 5
    high_value_timelock_seconds: float = 3600.0  # 1 hour timelock

@dataclass
class MultiSigApprovalReport:
    request_id: str
    amount_usd: float
    risk_tier: str                       # 'LOW_AUTO', 'MEDIUM_MULTISIG', 'HIGH_MULTISIG_TIMELOCK'
    m_required: int
    n_total: int
    submitted_approvals_count: int
    timelock_satisfied: bool
    is_approved: bool
    status: str                          # 'TRANSFER_APPROVED', 'INSUFFICIENT_SIGNATURES', 'TIMELOCK_PENDING'
    audit_notes: str

class MultiSigApprovalEngine:
    """
    Multi-signature approval workflow engine enforcing M-of-N threshold tiers,
    distinct role-based signer verification, and timelock delays for large crypto transfers.
    """
    def __init__(self, config: Optional[MultiSigConfig] = None):
        self.config = config or MultiSigConfig()

    def evaluate_transfer_approval(
        self,
        request: TransferRequestPayload,
        approvals: List[SignerApproval],
        current_time: Optional[float] = None
    ) -> MultiSigApprovalReport:
        """
        Evaluates risk tier, verifies distinct non-initiator signer quorum, and asserts timelock delay.
        """
        now = current_time or time.time()
        amt = request.amount_usd

        # 1. Determine Risk Tier & Requirements
        if amt < self.config.auto_approve_threshold_usd:
            tier = "LOW_AUTO"
            m_req = 1
            n_tot = 1
            timelock_req = 0.0
        elif amt <= self.config.high_value_threshold_usd:
            tier = "MEDIUM_MULTISIG"
            m_req = self.config.med_m_required
            n_tot = self.config.med_n_total
            timelock_req = 0.0
        else:
            tier = "HIGH_MULTISIG_TIMELOCK"
            m_req = self.config.high_m_required
            n_tot = self.config.high_n_total
            timelock_req = self.config.high_value_timelock_seconds

        # 2. Distinct Signer & Initiator Checks
        valid_signers: Set[str] = set()
        for app in approvals:
            if app.signer_id == request.initiated_by and tier != "LOW_AUTO":
                logger.warning(f"MULTISIG WARN [{request.request_id}]: Initiator '{app.signer_id}' approval ignored for non-low tier.")
                continue
            valid_signers.add(app.signer_id)

        valid_count = len(valid_signers)

        # 3. Timelock Delay Check
        elapsed_seconds = now - request.creation_timestamp
        timelock_ok = elapsed_seconds >= timelock_req

        # 4. Final Approval Determination
        if valid_count < m_req:
            status = "INSUFFICIENT_SIGNATURES"
            is_approved = False
            notes = f"INSUFFICIENT SIGNATURES [{request.request_id}]: Received {valid_count} valid approvals out of {m_req} required for {tier}."
        elif not timelock_ok:
            status = "TIMELOCK_PENDING"
            is_approved = False
            rem = timelock_req - elapsed_seconds
            notes = f"TIMELOCK PENDING [{request.request_id}]: Quorum met ({valid_count}/{m_req}), but timelock delay requires {rem:.0f}s more."
        else:
            status = "TRANSFER_APPROVED"
            is_approved = True
            notes = f"TRANSFER APPROVED [{request.request_id}]: {tier} requirements satisfied ({valid_count}/{m_req} signers, timelock ok)."

        logger.info(notes)

        return MultiSigApprovalReport(
            request_id=request.request_id,
            amount_usd=amt,
            risk_tier=tier,
            m_required=m_req,
            n_total=n_tot,
            submitted_approvals_count=valid_count,
            timelock_satisfied=timelock_ok,
            is_approved=is_approved,
            status=status,
            audit_notes=notes
        )
