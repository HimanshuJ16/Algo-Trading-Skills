import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

@dataclass
class Config:
    """Legacy Config container for backward compatibility."""
    enabled: bool = True

class Engine:
    """Legacy Engine class for backward compatibility."""
    def __init__(self, config: Config):
        self.config = config

    def run(self) -> bool:
        return self.config.enabled

class VoteType(str, Enum):
    FOR = "FOR"
    AGAINST = "AGAINST"
    ABSTAIN = "ABSTAIN"
    VETO = "VETO"

class CommitteeRole(str, Enum):
    CHIEF_INVESTMENT_OFFICER = "CIO"
    CHIEF_RISK_OFFICER = "CRO"             # Holds absolute risk veto power
    HEAD_OF_RESEARCH = "HEAD_OF_RESEARCH"
    PORTFOLIO_MANAGER = "PORTFOLIO_MANAGER"
    COMPLIANCE_OFFICER = "COMPLIANCE_OFFICER"

class ProposalType(str, Enum):
    ALLOCATION_INCREASE = "ALLOCATION_INCREASE"
    ALLOCATION_DECREASE = "ALLOCATION_DECREASE"
    STRATEGY_ONBOARDING = "STRATEGY_ONBOARDING"
    STRATEGY_DECOMMISSION = "STRATEGY_DECOMMISSION"

@dataclass
class CommitteeMember:
    member_id: str
    name: str
    role: CommitteeRole
    has_veto_power: bool = False

@dataclass
class MemberVote:
    member_id: str
    vote: VoteType
    rationale: str = ""
    timestamp_iso: str = ""

@dataclass
class AllocationProposal:
    proposal_id: str
    strategy_id: str
    proposal_type: ProposalType
    current_allocation_usd: float
    proposed_allocation_usd: float
    fund_total_aum_usd: float
    max_single_strategy_aum_pct: float = 20.0  # Max 20% of fund AUM per strategy
    submitted_by: str = ""

@dataclass
class CommitteeGovernanceDecision:
    proposal_id: str
    strategy_id: str
    is_approved: bool
    decision_status: str                       # 'APPROVED', 'REJECTED_CRO_VETO', 'REJECTED_RISK_BREACH', 'REJECTED_QUORUM_FAIL', 'REJECTED_VOTES'
    quorum_met: bool
    votes_for: int
    votes_against: int
    votes_abstain: int
    veto_triggered_by: Optional[str]
    audit_notes: str

class StrategyCommitteeGovernanceEngine:
    """
    Production-grade Strategy Committee Governance Engine enforcing committee quorum requirements,
    CRO veto power, risk mandate limits, and voting audit logging for multi-strategy capital allocation.
    """
    def __init__(
        self,
        committee_members: List[CommitteeMember],
        quorum_percentage: float = 50.0        # At least 50% members present
    ):
        self.members: Dict[str, CommitteeMember] = {m.member_id: m for m in committee_members}
        self.quorum_percentage = quorum_percentage

    def evaluate_proposal(
        self,
        proposal: AllocationProposal,
        member_votes: List[MemberVote]
    ) -> CommitteeGovernanceDecision:
        """
        Evaluates a capital allocation proposal:
        1. Pre-Check Risk Mandate Limits (Single strategy concentration cap).
        2. Quorum Check (Present members / total members >= 50%).
        3. CRO Veto Power Check.
        4. Vote Majority Tally.
        """
        # 1. Risk Mandate Limit Pre-Check
        if proposal.fund_total_aum_usd > 0:
            proposed_pct = (proposal.proposed_allocation_usd / proposal.fund_total_aum_usd) * 100.0
            if proposed_pct > proposal.max_single_strategy_aum_pct:
                notes = f"REJECTED_RISK_BREACH: Proposed allocation (${proposal.proposed_allocation_usd:,.2f} = {proposed_pct:.1f}% AUM) exceeds single strategy max cap of {proposal.max_single_strategy_aum_pct}% AUM."
                logger.warning(notes)
                return CommitteeGovernanceDecision(
                    proposal_id=proposal.proposal_id, strategy_id=proposal.strategy_id,
                    is_approved=False, decision_status="REJECTED_RISK_BREACH",
                    quorum_met=False, votes_for=0, votes_against=0, votes_abstain=0,
                    veto_triggered_by=None, audit_notes=notes
                )

        # 2. Quorum Verification
        participating_ids = {v.member_id for v in member_votes if v.member_id in self.members}
        quorum_pct = (len(participating_ids) / len(self.members)) * 100.0 if self.members else 0.0
        quorum_met = quorum_pct >= self.quorum_percentage

        if not quorum_met:
            notes = f"REJECTED_QUORUM_FAIL: Quorum of {quorum_pct:.1f}% is below required {self.quorum_percentage}% threshold."
            logger.warning(notes)
            return CommitteeGovernanceDecision(
                proposal_id=proposal.proposal_id, strategy_id=proposal.strategy_id,
                is_approved=False, decision_status="REJECTED_QUORUM_FAIL",
                quorum_met=False, votes_for=0, votes_against=0, votes_abstain=0,
                veto_triggered_by=None, audit_notes=notes
            )

        # 3. Vote Tally & Veto Check
        for_count = 0
        against_count = 0
        abstain_count = 0
        veto_member: Optional[str] = None

        for vote in member_votes:
            mem = self.members.get(vote.member_id)
            if not mem:
                continue

            if vote.vote == VoteType.VETO or (mem.has_veto_power and vote.vote == VoteType.AGAINST):
                if mem.has_veto_power:
                    veto_member = mem.name
                    notes = f"REJECTED_CRO_VETO: Proposal vetoed by {mem.name} ({mem.role.value}). Rationale: {vote.rationale}"
                    logger.warning(notes)
                    return CommitteeGovernanceDecision(
                        proposal_id=proposal.proposal_id, strategy_id=proposal.strategy_id,
                        is_approved=False, decision_status="REJECTED_CRO_VETO",
                        quorum_met=True, votes_for=for_count, votes_against=against_count + 1,
                        votes_abstain=abstain_count, veto_triggered_by=veto_member, audit_notes=notes
                    )

            if vote.vote == VoteType.FOR:
                for_count += 1
            elif vote.vote == VoteType.AGAINST:
                against_count += 1
            elif vote.vote == VoteType.ABSTAIN:
                abstain_count += 1

        # 4. Majority Approval Check
        is_approved = for_count > against_count
        status = "APPROVED" if is_approved else "REJECTED_VOTES"

        notes = (
            f"COMMITTEE GOVERNANCE [{status}] ({proposal.proposal_id}): Strategy = {proposal.strategy_id}, "
            f"Votes = {for_count} FOR / {against_count} AGAINST / {abstain_count} ABSTAIN."
        )

        logger.info(notes)

        return CommitteeGovernanceDecision(
            proposal_id=proposal.proposal_id,
            strategy_id=proposal.strategy_id,
            is_approved=is_approved,
            decision_status=status,
            quorum_met=True,
            votes_for=for_count,
            votes_against=against_count,
            votes_abstain=abstain_count,
            veto_triggered_by=None,
            audit_notes=notes
        )
