"""
Unit tests for the strategy committee governance engine.

Run from this directory:
    python test_strategy_committee_governance_for_capital_allocation_decisions.py

The tests are grouped by what they protect:

* ``TestDecisionOutcomes``      -- the five terminal verdicts.
* ``TestBallotIntegrity``       -- fail-open regressions in the tally. Every test
                                   here approved a proposal it should have blocked,
                                   or discarded a vote, before this was fixed.
* ``TestConcentrationMandate``  -- the cap, its boundary, and the de-risking carve-out.
* ``TestQuorum``                -- quorum arithmetic and roster integrity.
* ``TestAuditRecordFidelity``   -- the record must not state things that are untrue.
* ``TestPolicyAuditability``    -- the rules actually applied are in the record.
* ``TestInputTypeSafety``       -- corrupt input raises instead of returning a verdict.
"""
import unittest
from datetime import datetime

from strategy_committee_governance_for_capital_allocation_decisions import (
    DEFAULT_MAX_SINGLE_STRATEGY_AUM_PCT,
    DEFAULT_MIN_VOTES_FOR,
    DEFAULT_QUORUM_PCT,
    FLAG_ABOVE_CAP_AFTER_DECREASE,
    FLAG_INVALID_VETO_ATTEMPT,
    FLAG_PROPOSAL_TYPE_MISMATCH,
    FLAG_VETO_RATIONALE_MISSING,
    AllocationProposal,
    CommitteeGovernancePolicy,
    CommitteeMember,
    CommitteeRole,
    MemberVote,
    ProposalType,
    StrategyCommitteeGovernanceEngine,
    VoteType,
)

FUND_AUM = 100_000_000.0


def roster():
    """Four seats, one of which (the CRO) holds the charter's veto."""
    return [
        CommitteeMember("M1", "Alice (CIO)",
                        CommitteeRole.CHIEF_INVESTMENT_OFFICER, has_veto_power=False),
        CommitteeMember("M2", "Bob (CRO)",
                        CommitteeRole.CHIEF_RISK_OFFICER, has_veto_power=True),
        CommitteeMember("M3", "Charlie (Head Quant)",
                        CommitteeRole.HEAD_OF_RESEARCH, has_veto_power=False),
        CommitteeMember("M4", "Diana (PM)",
                        CommitteeRole.PORTFOLIO_MANAGER, has_veto_power=False),
    ]


def proposal(**overrides) -> AllocationProposal:
    """A 10% -> 15% increase in a $100M fund: inside the 20% cap by default."""
    fields = {
        "proposal_id": "PROP_001",
        "strategy_id": "STAT_ARB_01",
        "proposal_type": ProposalType.ALLOCATION_INCREASE,
        "current_allocation_usd": 10_000_000.0,
        "proposed_allocation_usd": 15_000_000.0,
        "fund_total_aum_usd": FUND_AUM,
        "max_single_strategy_aum_pct": 20.0,
    }
    fields.update(overrides)
    return AllocationProposal(**fields)


class TestDecisionOutcomes(unittest.TestCase):

    def setUp(self):
        self.engine = StrategyCommitteeGovernanceEngine(roster())

    def test_majority_within_mandate_is_approved(self):
        res = self.engine.evaluate_proposal(proposal(), [
            MemberVote("M1", VoteType.FOR),
            MemberVote("M2", VoteType.FOR),
            MemberVote("M3", VoteType.FOR),
            MemberVote("M4", VoteType.AGAINST),
        ])
        self.assertTrue(res.is_approved)
        self.assertEqual(res.decision_status, "APPROVED")
        self.assertEqual(res.votes_for, 3)
        self.assertEqual(res.votes_against, 1)
        self.assertEqual(res.rejection_reasons, [])
        self.assertEqual(res.risk_flags, [])

    def test_veto_holder_blocks_with_veto_ballot(self):
        res = self.engine.evaluate_proposal(proposal(), [
            MemberVote("M1", VoteType.FOR),
            MemberVote("M2", VoteType.VETO, rationale="Excessive crypto volatility"),
            MemberVote("M3", VoteType.FOR),
        ])
        self.assertFalse(res.is_approved)
        self.assertEqual(res.decision_status, "REJECTED_CRO_VETO")
        self.assertEqual(res.veto_triggered_by, "Bob (CRO)")
        self.assertEqual(res.veto_triggered_by_id, "M2")
        self.assertEqual(res.votes_veto, 1)

    def test_quorum_failure_blocks_before_the_vote_carries(self):
        # One of four members participating is 25%, below the 50% floor, even
        # though the only ballot cast is FOR.
        res = self.engine.evaluate_proposal(
            proposal(), [MemberVote("M1", VoteType.FOR)])
        self.assertFalse(res.is_approved)
        self.assertEqual(res.decision_status, "REJECTED_QUORUM_FAIL")
        self.assertFalse(res.quorum_met)
        self.assertAlmostEqual(res.quorum_pct, 25.0)

    def test_majority_not_reached_is_rejected_on_votes(self):
        res = self.engine.evaluate_proposal(proposal(), [
            MemberVote("M1", VoteType.FOR),
            MemberVote("M3", VoteType.AGAINST),
            MemberVote("M4", VoteType.AGAINST),
        ])
        self.assertFalse(res.is_approved)
        self.assertEqual(res.decision_status, "REJECTED_VOTES")
        self.assertEqual((res.votes_for, res.votes_against), (1, 2))

    def test_tie_is_not_a_majority(self):
        # Two members, one each way. Quorum is met; the majority rule is strict.
        res = self.engine.evaluate_proposal(proposal(), [
            MemberVote("M1", VoteType.FOR),
            MemberVote("M3", VoteType.AGAINST),
        ])
        self.assertTrue(res.quorum_met)
        self.assertEqual(res.decision_status, "REJECTED_VOTES")
        self.assertEqual((res.votes_for, res.votes_against), (1, 1))

    def test_all_abstentions_reach_quorum_but_not_a_majority(self):
        res = self.engine.evaluate_proposal(proposal(), [
            MemberVote("M1", VoteType.ABSTAIN),
            MemberVote("M3", VoteType.ABSTAIN),
        ])
        self.assertTrue(res.quorum_met)
        self.assertEqual(res.decision_status, "REJECTED_VOTES")
        self.assertEqual(res.votes_abstain, 2)


class TestBallotIntegrity(unittest.TestCase):
    """
    Every test in this class describes a way the pre-fix engine could be made to
    approve a proposal the committee had not approved.
    """

    def setUp(self):
        self.engine = StrategyCommitteeGovernanceEngine(roster())

    def test_duplicate_ballot_from_one_member_is_rejected(self):
        # Regression: three FOR ballots from M1 used to outvote two genuine
        # AGAINST ballots and return APPROVED with votes_for=3.
        with self.assertRaises(ValueError) as ctx:
            self.engine.evaluate_proposal(proposal(), [
                MemberVote("M1", VoteType.FOR),
                MemberVote("M1", VoteType.FOR),
                MemberVote("M1", VoteType.FOR),
                MemberVote("M3", VoteType.AGAINST),
                MemberVote("M4", VoteType.AGAINST),
            ])
        self.assertIn("duplicate ballot", str(ctx.exception))

    def test_ballot_from_someone_not_on_the_roster_is_rejected(self):
        # Regression: an unknown member_id used to be dropped in silence, so the
        # record showed a tally that did not match the ballots submitted.
        with self.assertRaises(ValueError) as ctx:
            self.engine.evaluate_proposal(proposal(), [
                MemberVote("GHOST", VoteType.FOR),
                MemberVote("M1", VoteType.FOR),
                MemberVote("M3", VoteType.FOR),
            ])
        self.assertIn("not on the committee roster", str(ctx.exception))

    def test_veto_ballot_without_authority_counts_as_against(self):
        # Regression: a VETO from a member without veto power fell through every
        # branch and vanished. Two members voting VETO plus one FOR returned
        # APPROVED with votes_for=1, votes_against=0.
        res = self.engine.evaluate_proposal(proposal(), [
            MemberVote("M1", VoteType.FOR),
            MemberVote("M3", VoteType.VETO, rationale="capacity concerns"),
            MemberVote("M4", VoteType.VETO, rationale="capacity concerns"),
        ])
        self.assertFalse(res.is_approved)
        self.assertEqual(res.decision_status, "REJECTED_VOTES")
        self.assertEqual((res.votes_for, res.votes_against), (1, 2))
        self.assertIsNone(res.veto_triggered_by)
        self.assertEqual(
            sum(1 for f in res.risk_flags if f.startswith(FLAG_INVALID_VETO_ATTEMPT)), 2)

    def test_veto_tally_is_complete_and_order_independent(self):
        # Regression: the veto branch returned immediately, so ballots listed
        # after the veto were never counted and the veto itself was recorded as
        # a single AGAINST regardless of what else was cast.
        votes = [
            MemberVote("M2", VoteType.VETO, rationale="limit breach"),
            MemberVote("M1", VoteType.FOR),
            MemberVote("M3", VoteType.FOR),
            MemberVote("M4", VoteType.ABSTAIN),
        ]
        first = self.engine.evaluate_proposal(proposal(), votes)
        last = self.engine.evaluate_proposal(proposal(), votes[1:] + votes[:1])

        self.assertEqual(first.decision_status, "REJECTED_CRO_VETO")
        self.assertEqual(last.decision_status, "REJECTED_CRO_VETO")
        for res in (first, last):
            self.assertEqual((res.votes_for, res.votes_against, res.votes_abstain),
                             (2, 1, 1))
            self.assertEqual(res.committee_size, 4)
            self.assertEqual(len(res.participating_member_ids), 4)

    def test_veto_without_rationale_still_blocks_and_is_flagged(self):
        # Rejecting the ballot instead would let a missing rationale erase the
        # veto and unblock the proposal.
        res = self.engine.evaluate_proposal(proposal(), [
            MemberVote("M2", VoteType.VETO),
            MemberVote("M1", VoteType.FOR),
        ])
        self.assertEqual(res.decision_status, "REJECTED_CRO_VETO")
        self.assertTrue(
            any(f.startswith(FLAG_VETO_RATIONALE_MISSING) for f in res.risk_flags))

    def test_veto_power_is_read_from_the_charter_not_the_role(self):
        members = [
            CommitteeMember("M1", "Alice", CommitteeRole.CHIEF_INVESTMENT_OFFICER),
            CommitteeMember("M2", "Bob", CommitteeRole.CHIEF_RISK_OFFICER,
                            has_veto_power=False),
            CommitteeMember("M3", "Charlie", CommitteeRole.HEAD_OF_RESEARCH),
        ]
        engine = StrategyCommitteeGovernanceEngine(members)
        res = engine.evaluate_proposal(proposal(), [
            MemberVote("M1", VoteType.FOR),
            MemberVote("M3", VoteType.FOR),
            MemberVote("M2", VoteType.VETO, rationale="not my charter power"),
        ])
        # A CRO with no charter veto casts an ordinary dissent: 2 FOR beats 1.
        self.assertEqual(res.decision_status, "APPROVED")
        self.assertEqual((res.votes_for, res.votes_against), (2, 1))
        self.assertIsNone(res.veto_triggered_by)


class TestConcentrationMandate(unittest.TestCase):

    def setUp(self):
        self.engine = StrategyCommitteeGovernanceEngine(roster())
        self.unanimous = [MemberVote(m.member_id, VoteType.FOR) for m in roster()]

    def test_increase_beyond_the_cap_is_rejected(self):
        res = self.engine.evaluate_proposal(
            proposal(proposed_allocation_usd=30_000_000.0), self.unanimous)
        self.assertFalse(res.is_approved)
        self.assertEqual(res.decision_status, "REJECTED_RISK_BREACH")
        self.assertAlmostEqual(res.proposed_pct_of_aum, 30.0)

    def test_allocation_exactly_at_the_cap_passes(self):
        res = self.engine.evaluate_proposal(
            proposal(proposed_allocation_usd=20_000_000.0), self.unanimous)
        self.assertEqual(res.decision_status, "APPROVED")

    def test_one_dollar_over_the_cap_is_rejected(self):
        res = self.engine.evaluate_proposal(
            proposal(proposed_allocation_usd=20_000_001.0), self.unanimous)
        self.assertEqual(res.decision_status, "REJECTED_RISK_BREACH")

    def test_reduction_that_remains_above_the_cap_is_allowed_and_flagged(self):
        # Regression, and the most dangerous of the pre-fix defects: a committee
        # cutting an over-limit strategy from 40% to 30% was refused with
        # REJECTED_RISK_BREACH -- the concentration control blocked the only
        # action that reduced the concentration.
        res = self.engine.evaluate_proposal(proposal(
            proposal_type=ProposalType.ALLOCATION_DECREASE,
            current_allocation_usd=40_000_000.0,
            proposed_allocation_usd=30_000_000.0,
        ), self.unanimous)
        self.assertTrue(res.is_approved)
        self.assertEqual(res.decision_status, "APPROVED")
        self.assertTrue(
            any(f.startswith(FLAG_ABOVE_CAP_AFTER_DECREASE) for f in res.risk_flags))

    def test_unchanged_allocation_above_the_cap_is_ratifiable_and_flagged(self):
        # Re-affirming an existing over-limit position adds no risk, so it is not
        # a breach -- but the record must not read as if the fund were inside its
        # mandate.
        res = self.engine.evaluate_proposal(proposal(
            current_allocation_usd=40_000_000.0,
            proposed_allocation_usd=40_000_000.0,
        ), self.unanimous)
        self.assertEqual(res.decision_status, "APPROVED")
        self.assertTrue(
            any(f.startswith(FLAG_ABOVE_CAP_AFTER_DECREASE) for f in res.risk_flags))

    def test_decommission_of_an_over_limit_strategy_is_allowed(self):
        res = self.engine.evaluate_proposal(proposal(
            proposal_type=ProposalType.STRATEGY_DECOMMISSION,
            current_allocation_usd=40_000_000.0,
            proposed_allocation_usd=0.0,
        ), self.unanimous)
        self.assertEqual(res.decision_status, "APPROVED")
        self.assertEqual(res.risk_flags, [])

    def test_cap_applies_to_the_amounts_not_the_declared_type(self):
        # Labelled a decrease, but the money goes up and through the cap.
        res = self.engine.evaluate_proposal(proposal(
            proposal_type=ProposalType.ALLOCATION_DECREASE,
            current_allocation_usd=10_000_000.0,
            proposed_allocation_usd=30_000_000.0,
        ), self.unanimous)
        self.assertEqual(res.decision_status, "REJECTED_RISK_BREACH")
        self.assertTrue(
            any(f.startswith(FLAG_PROPOSAL_TYPE_MISMATCH) for f in res.risk_flags))

    def test_nan_allocation_raises_instead_of_clearing_the_cap(self):
        # Regression: NaN > 20.0 is False, so a corrupt allocation used to pass
        # the concentration check and be reported as APPROVED.
        with self.assertRaises(ValueError):
            proposal(proposed_allocation_usd=float("nan"))

    def test_infinite_allocation_raises(self):
        with self.assertRaises(ValueError):
            proposal(proposed_allocation_usd=float("inf"))

    def test_zero_fund_aum_raises_instead_of_skipping_the_cap(self):
        # Regression: the cap was guarded by `if fund_total_aum_usd > 0`, so a
        # zero AUM disabled the fund's concentration mandate silently.
        with self.assertRaises(ValueError) as ctx:
            proposal(fund_total_aum_usd=0.0, proposed_allocation_usd=9_000_000_000.0)
        self.assertIn("must be > 0", str(ctx.exception))

    def test_negative_fund_aum_raises(self):
        with self.assertRaises(ValueError):
            proposal(fund_total_aum_usd=-1.0)

    def test_negative_allocation_raises(self):
        with self.assertRaises(ValueError):
            proposal(proposed_allocation_usd=-1.0)

    def test_cap_outside_zero_to_one_hundred_percent_raises(self):
        with self.assertRaises(ValueError):
            proposal(max_single_strategy_aum_pct=0.0)
        with self.assertRaises(ValueError):
            proposal(max_single_strategy_aum_pct=101.0)

    def test_field_reassigned_after_construction_is_revalidated_by_the_engine(self):
        prop = proposal()
        prop.proposed_allocation_usd = float("nan")
        with self.assertRaises(ValueError):
            self.engine.evaluate_proposal(prop, self.unanimous)


class TestQuorum(unittest.TestCase):

    def setUp(self):
        self.engine = StrategyCommitteeGovernanceEngine(roster())

    def test_exactly_at_the_quorum_threshold_counts(self):
        res = self.engine.evaluate_proposal(proposal(), [
            MemberVote("M1", VoteType.FOR),
            MemberVote("M3", VoteType.FOR),
        ])
        self.assertTrue(res.quorum_met)
        self.assertAlmostEqual(res.quorum_pct, 50.0)
        self.assertEqual(res.decision_status, "APPROVED")

    def test_abstentions_count_toward_quorum(self):
        res = self.engine.evaluate_proposal(proposal(), [
            MemberVote("M1", VoteType.FOR),
            MemberVote("M3", VoteType.ABSTAIN),
        ])
        self.assertTrue(res.quorum_met)
        self.assertEqual(res.votes_abstain, 1)

    def test_duplicate_seat_on_the_roster_is_rejected(self):
        # Regression: duplicate member_ids collapsed into one dict entry, so a
        # four-name roster became a committee of one and a single ballot met
        # quorum.
        duplicated = [
            CommitteeMember("M1", "Alice", CommitteeRole.PORTFOLIO_MANAGER),
            CommitteeMember("M1", "Alice again", CommitteeRole.PORTFOLIO_MANAGER),
        ]
        with self.assertRaises(ValueError) as ctx:
            StrategyCommitteeGovernanceEngine(duplicated)
        self.assertIn("duplicate member_id", str(ctx.exception))

    def test_empty_roster_is_rejected(self):
        with self.assertRaises(ValueError):
            StrategyCommitteeGovernanceEngine([])

    def test_engine_exposes_the_quorum_threshold_in_force(self):
        engine = StrategyCommitteeGovernanceEngine(roster(), quorum_percentage=75.0)
        self.assertEqual(engine.quorum_percentage, 75.0)
        res = engine.evaluate_proposal(proposal(), [
            MemberVote("M1", VoteType.FOR),
            MemberVote("M3", VoteType.FOR),
        ])
        self.assertEqual(res.decision_status, "REJECTED_QUORUM_FAIL")


class TestAuditRecordFidelity(unittest.TestCase):
    """The minute must not assert things that did not happen."""

    def setUp(self):
        self.engine = StrategyCommitteeGovernanceEngine(roster())

    def test_risk_breach_record_still_reports_the_ballot_that_was_cast(self):
        # Regression: a mandate breach returned quorum_met=False and 0/0/0 votes
        # even when the whole committee had voted -- a false statement in a
        # record whose only purpose is to be true later.
        res = self.engine.evaluate_proposal(
            proposal(proposed_allocation_usd=30_000_000.0),
            [MemberVote(m.member_id, VoteType.FOR) for m in roster()])
        self.assertEqual(res.decision_status, "REJECTED_RISK_BREACH")
        self.assertTrue(res.quorum_met)
        self.assertEqual(res.votes_for, 4)
        self.assertEqual(res.committee_size, 4)

    def test_every_rejection_reason_is_recorded_not_just_the_first(self):
        res = self.engine.evaluate_proposal(
            proposal(proposed_allocation_usd=30_000_000.0),
            [MemberVote("M2", VoteType.VETO, rationale="over the mandate")])
        self.assertEqual(res.decision_status, "REJECTED_RISK_BREACH")
        joined = " | ".join(res.rejection_reasons)
        self.assertIn("REJECTED_RISK_BREACH", joined)
        self.assertIn("REJECTED_QUORUM_FAIL", joined)
        self.assertIn("REJECTED_CRO_VETO", joined)
        self.assertIn("REJECTED_VOTES", joined)

    def test_decision_carries_a_timezone_aware_utc_timestamp(self):
        res = self.engine.evaluate_proposal(proposal(), [
            MemberVote("M1", VoteType.FOR),
            MemberVote("M3", VoteType.FOR),
        ])
        parsed = datetime.fromisoformat(res.decided_at_utc)
        self.assertIsNotNone(parsed.tzinfo)
        self.assertEqual(parsed.utcoffset().total_seconds(), 0.0)

    def test_participants_and_percentages_are_recorded(self):
        res = self.engine.evaluate_proposal(proposal(), [
            MemberVote("M1", VoteType.FOR),
            MemberVote("M3", VoteType.FOR),
        ])
        self.assertEqual(res.participating_member_ids, ["M1", "M3"])
        self.assertAlmostEqual(res.proposed_pct_of_aum, 15.0)
        self.assertAlmostEqual(res.max_single_strategy_aum_pct, 20.0)
        self.assertAlmostEqual(res.quorum_required_pct, DEFAULT_QUORUM_PCT)


class TestPolicyAuditability(unittest.TestCase):

    def test_policy_snapshot_is_embedded_in_every_decision(self):
        engine = StrategyCommitteeGovernanceEngine(roster())
        res = engine.evaluate_proposal(proposal(), [
            MemberVote("M1", VoteType.FOR),
            MemberVote("M3", VoteType.FOR),
        ])
        self.assertEqual(res.policy_applied, {
            "quorum_percentage": DEFAULT_QUORUM_PCT,
            "min_votes_for": DEFAULT_MIN_VOTES_FOR,
            "veto_holder_against_counts_as_veto": True,
        })
        self.assertEqual(res.policy_weakened, [])

    def test_a_toothless_policy_still_approves_but_names_its_relaxations(self):
        policy = CommitteeGovernancePolicy(
            quorum_percentage=0.0,
            min_votes_for=0,
            veto_holder_against_counts_as_veto=False,
        )
        engine = StrategyCommitteeGovernanceEngine(roster(), policy=policy)
        res = engine.evaluate_proposal(proposal(), [MemberVote("M1", VoteType.FOR)])
        self.assertEqual(res.decision_status, "APPROVED")
        weakened = " | ".join(res.policy_weakened)
        self.assertIn("quorum_percentage", weakened)
        self.assertIn("min_votes_for", weakened)
        self.assertIn("veto_holder_against_counts_as_veto", weakened)

    def test_min_votes_for_floor_rejects_a_thin_majority(self):
        engine = StrategyCommitteeGovernanceEngine(
            roster(), policy=CommitteeGovernancePolicy(min_votes_for=3))
        res = engine.evaluate_proposal(proposal(), [
            MemberVote("M1", VoteType.FOR),
            MemberVote("M3", VoteType.FOR),
        ])
        self.assertEqual(res.decision_status, "REJECTED_VOTES")
        self.assertIn("min_votes_for=3", " ".join(res.rejection_reasons))

    def test_charter_may_let_a_veto_holder_dissent_without_blocking(self):
        engine = StrategyCommitteeGovernanceEngine(
            roster(),
            policy=CommitteeGovernancePolicy(
                veto_holder_against_counts_as_veto=False))
        res = engine.evaluate_proposal(proposal(), [
            MemberVote("M1", VoteType.FOR),
            MemberVote("M3", VoteType.FOR),
            MemberVote("M2", VoteType.AGAINST, rationale="prefer a smaller step"),
        ])
        self.assertEqual(res.decision_status, "APPROVED")
        self.assertIsNone(res.veto_triggered_by)
        # A deliberate VETO ballot still blocks under the same charter.
        blocked = engine.evaluate_proposal(proposal(), [
            MemberVote("M1", VoteType.FOR),
            MemberVote("M3", VoteType.FOR),
            MemberVote("M2", VoteType.VETO, rationale="formal veto"),
        ])
        self.assertEqual(blocked.decision_status, "REJECTED_CRO_VETO")

    def test_default_charter_treats_a_veto_holder_against_vote_as_a_veto(self):
        engine = StrategyCommitteeGovernanceEngine(roster())
        res = engine.evaluate_proposal(proposal(), [
            MemberVote("M1", VoteType.FOR),
            MemberVote("M3", VoteType.FOR),
            MemberVote("M2", VoteType.AGAINST, rationale="prefer a smaller step"),
        ])
        self.assertEqual(res.decision_status, "REJECTED_CRO_VETO")

    def test_two_quorum_sources_is_a_configuration_error(self):
        with self.assertRaises(ValueError):
            StrategyCommitteeGovernanceEngine(
                roster(), quorum_percentage=60.0,
                policy=CommitteeGovernancePolicy(quorum_percentage=50.0))

    def test_invalid_policy_values_raise(self):
        with self.assertRaises(ValueError):
            CommitteeGovernancePolicy(quorum_percentage=120.0)
        with self.assertRaises(ValueError):
            CommitteeGovernancePolicy(quorum_percentage=float("nan"))
        with self.assertRaises(ValueError):
            CommitteeGovernancePolicy(min_votes_for=-1)
        with self.assertRaises(ValueError):
            CommitteeGovernancePolicy(min_votes_for=True)


class TestInputTypeSafety(unittest.TestCase):

    def setUp(self):
        self.engine = StrategyCommitteeGovernanceEngine(roster())

    def test_veto_power_flag_is_never_coerced_by_truthiness(self):
        # "no" is truthy: coercion would hand a veto to a member the charter
        # denies one, and an empty string would silently remove a real veto.
        with self.assertRaises(ValueError):
            CommitteeMember("M9", "Eve", CommitteeRole.COMPLIANCE_OFFICER,
                            has_veto_power="no")
        with self.assertRaises(ValueError):
            CommitteeMember("M9", "Eve", CommitteeRole.COMPLIANCE_OFFICER,
                            has_veto_power=1)

    def test_roster_flag_corrupted_after_construction_is_caught_at_the_vote(self):
        # A roster entry is a mutable dataclass. Validating only at construction
        # would let has_veto_power be reassigned to a truthy string afterwards and
        # hand out a veto the charter never granted.
        engine = StrategyCommitteeGovernanceEngine(roster())
        engine.members["M3"].has_veto_power = "yes"
        with self.assertRaises(ValueError):
            engine.evaluate_proposal(proposal(), [
                MemberVote("M1", VoteType.FOR),
                MemberVote("M3", VoteType.FOR),
            ])

    def test_blank_identifiers_are_rejected(self):
        with self.assertRaises(ValueError):
            CommitteeMember("  ", "Eve", CommitteeRole.COMPLIANCE_OFFICER)
        with self.assertRaises(ValueError):
            MemberVote("", VoteType.FOR)
        with self.assertRaises(ValueError):
            proposal(proposal_id="")

    def test_unrecognised_vote_value_is_rejected(self):
        with self.assertRaises(ValueError):
            MemberVote("M1", "MAYBE")

    def test_vote_and_role_accept_their_string_values(self):
        self.assertIs(MemberVote("M1", "FOR").vote, VoteType.FOR)
        self.assertIs(
            CommitteeMember("M9", "Eve", "COMPLIANCE_OFFICER").role,
            CommitteeRole.COMPLIANCE_OFFICER)

    def test_unparseable_vote_timestamp_is_rejected(self):
        with self.assertRaises(ValueError):
            MemberVote("M1", VoteType.FOR, timestamp_iso="last Tuesday")
        self.assertEqual(
            MemberVote("M1", VoteType.FOR,
                       timestamp_iso="2026-08-28T09:30:00+00:00").timestamp_iso,
            "2026-08-28T09:30:00+00:00")

    def test_non_dataclass_arguments_are_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate_proposal({"proposal_id": "P"}, [])
        with self.assertRaises(ValueError):
            self.engine.evaluate_proposal(proposal(), [{"member_id": "M1"}])
        with self.assertRaises(ValueError):
            StrategyCommitteeGovernanceEngine(["M1", "M2"])

    def test_shipped_defaults_match_the_numbers_the_docs_publish(self):
        # SKILL.md, references/standards.md and assets/checklist.md all quote
        # these two numbers; a silent change here would make the docs wrong.
        self.assertEqual(DEFAULT_MAX_SINGLE_STRATEGY_AUM_PCT, 20.0)
        self.assertEqual(DEFAULT_QUORUM_PCT, 50.0)
        self.assertEqual(proposal().max_single_strategy_aum_pct,
                         DEFAULT_MAX_SINGLE_STRATEGY_AUM_PCT)


if __name__ == "__main__":
    unittest.main()
