import unittest
from strategy_committee_governance_for_capital_allocation_decisions import (
    Config, Engine,
    StrategyCommitteeGovernanceEngine, CommitteeMember, CommitteeRole,
    AllocationProposal, ProposalType, MemberVote, VoteType, CommitteeGovernanceDecision
)

class TestStrategyCommitteeGovernanceLegacy(unittest.TestCase):
    def test_init(self):
        engine = Engine(Config())
        self.assertTrue(engine.config.enabled)

    def test_run(self):
        engine = Engine(Config())
        self.assertTrue(engine.run())

class TestStrategyCommitteeGovernanceEngineAdvanced(unittest.TestCase):

    def setUp(self):
        self.members = [
            CommitteeMember("M1", "Alice (CIO)", CommitteeRole.CHIEF_INVESTMENT_OFFICER, has_veto_power=False),
            CommitteeMember("M2", "Bob (CRO)", CommitteeRole.CHIEF_RISK_OFFICER, has_veto_power=True),
            CommitteeMember("M3", "Charlie (Head Quant)", CommitteeRole.HEAD_OF_RESEARCH, has_veto_power=False),
            CommitteeMember("M4", "Diana (PM)", CommitteeRole.PORTFOLIO_MANAGER, has_veto_power=False),
        ]
        self.engine = StrategyCommitteeGovernanceEngine(committee_members=self.members, quorum_percentage=50.0)

    def test_approved_capital_allocation_proposal(self):
        prop = AllocationProposal(
            proposal_id="PROP_001",
            strategy_id="STAT_ARB_01",
            proposal_type=ProposalType.ALLOCATION_INCREASE,
            current_allocation_usd=10_000_000.0,
            proposed_allocation_usd=15_000_000.0,
            fund_total_aum_usd=100_000_000.0, # Proposed 15% AUM <= Max 20% AUM limit
            max_single_strategy_aum_pct=20.0
        )
        votes = [
            MemberVote("M1", VoteType.FOR),
            MemberVote("M2", VoteType.FOR),
            MemberVote("M3", VoteType.FOR),
            MemberVote("M4", VoteType.AGAINST),
        ]
        res = self.engine.evaluate_proposal(prop, votes)
        self.assertTrue(res.is_approved)
        self.assertEqual(res.decision_status, "APPROVED")
        self.assertEqual(res.votes_for, 3)
        self.assertEqual(res.votes_against, 1)

    def test_cro_veto_blocks_proposal(self):
        prop = AllocationProposal(
            proposal_id="PROP_002",
            strategy_id="HIGH_BETA_CRYPTO",
            proposal_type=ProposalType.ALLOCATION_INCREASE,
            current_allocation_usd=5_000_000.0,
            proposed_allocation_usd=10_000_000.0,
            fund_total_aum_usd=100_000_000.0,
            max_single_strategy_aum_pct=20.0
        )
        votes = [
            MemberVote("M1", VoteType.FOR),
            MemberVote("M2", VoteType.VETO, rationale="Excessive crypto volatility risk"), # CRO VETO!
            MemberVote("M3", VoteType.FOR),
        ]
        res = self.engine.evaluate_proposal(prop, votes)
        self.assertFalse(res.is_approved)
        self.assertEqual(res.decision_status, "REJECTED_CRO_VETO")
        self.assertEqual(res.veto_triggered_by, "Bob (CRO)")

    def test_risk_mandate_limit_breach(self):
        prop = AllocationProposal(
            proposal_id="PROP_003",
            strategy_id="CONCENTRATED_LONG",
            proposal_type=ProposalType.ALLOCATION_INCREASE,
            current_allocation_usd=15_000_000.0,
            proposed_allocation_usd=30_000_000.0, # 30% of AUM > 20% max cap!
            fund_total_aum_usd=100_000_000.0,
            max_single_strategy_aum_pct=20.0
        )
        votes = [MemberVote("M1", VoteType.FOR), MemberVote("M2", VoteType.FOR)]
        res = self.engine.evaluate_proposal(prop, votes)
        self.assertFalse(res.is_approved)
        self.assertEqual(res.decision_status, "REJECTED_RISK_BREACH")

if __name__ == '__main__':
    unittest.main()
