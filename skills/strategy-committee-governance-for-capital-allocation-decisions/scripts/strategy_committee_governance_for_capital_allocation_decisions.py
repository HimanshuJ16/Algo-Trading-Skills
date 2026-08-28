"""
strategy-committee-governance-for-capital-allocation-decisions: records and
adjudicates a strategy committee's vote on a capital allocation proposal.

Purpose
-------
Turn "we all agreed to give that book more money" into a reproducible record:
who was present, how each member voted, whether the proposal breached the fund's
own concentration mandate, whether the risk officer exercised a veto, and what
thresholds were in force at the time.

The decision ladder, in precedence order:

    REJECTED_RISK_BREACH   an *increase* would take the strategy above the fund's
                           single-strategy concentration cap
    REJECTED_QUORUM_FAIL   too few committee members participated
    REJECTED_CRO_VETO      a veto-holding member vetoed
    REJECTED_VOTES         FOR did not exceed AGAINST, or FOR fell below the
                           configured affirmative-vote floor
    APPROVED               none of the above

Every reason that fired is listed in ``rejection_reasons``; ``decision_status``
names only the first, so a proposal that both breaches the mandate and was vetoed
does not lose the veto from its record.

What this engine actually does
------------------------------
**It records a vote. It does not verify anything and it does not move capital.**

- ``APPROVED`` transfers no money, sets no position limit and blocks no order. It
  is the governance record that a decision was taken. The controls that actually
  bound a live strategy are separate -- see ``multi-strategy-capital-allocation-limits``,
  ``correlation-aware-exposure-limits`` and ``kill-switch-and-drawdown-circuit-breakers``.
- The concentration check sees **one strategy at a time**. It cannot detect that
  five strategies at 19% each sum to 95% of the fund, nor that two of them hold
  the same underlying risk. Aggregate and correlation-aware limits live in the
  skills named above.
- Identity is not authenticated. ``MemberVote.member_id`` is whatever the caller
  supplied; the engine checks it against the committee roster and rejects ballots
  from unknown ids, but it cannot tell who actually typed the vote.
- ``fund_total_aum_usd`` is a caller-supplied number, not a reconciled NAV. A
  stale or optimistic AUM makes every percentage in the record wrong in the
  permissive direction.

Threshold provenance
--------------------
The defaults are **house heuristics, not standards**. No regulator prescribes a
committee quorum, a voting rule, or a maximum share of fund AUM per strategy:

- Quorum and voting rules come from the fund's own governing documents -- the LPA,
  the committee charter, the IPS -- not from a rulebook. The common charter
  convention is a *majority* of members; this engine's shipped default of
  ``>= 50%`` is one member weaker than that on an even-sized committee.
- The 20% single-strategy cap is a house number. The EU concentration limits that
  are frequently misremembered as its source -- UCITS Directive 2009/65/EC Art. 52,
  the "5/10/40" rule -- limit exposure **per issuing body**, not the share of a
  fund allocated to a trading strategy.
- No instrument grants a Chief Risk Officer a veto. What regulation requires is
  *independence* of the risk function -- AIFMD Directive 2011/61/EU Art. 15(1)
  requires risk management be functionally and hierarchically separated from
  portfolio management -- and *escalation*: Commission Delegated Regulation (EU)
  231/2013 Art. 39 requires the risk management function to report actual or
  foreseeable breaches of the Art. 44 risk limits to senior management. A veto is
  a charter choice a firm may make on top of that. See ``references/standards.md``.

Because the numbers are the firm's own, every decision embeds ``policy_applied``
and names any relaxation in ``policy_weakened``. A stored verdict without its
policy snapshot proves nothing: a committee of one with a 0% quorum emits the
same ``APPROVED`` string as a full board.

Limitations (documented, deliberate)
------------------------------------
- **Stateless.** No proposal history, no re-vote tracking, no notion of a proposal
  resubmitted until it passes. Persist the decisions yourself.
- **Every roster member counts toward the quorum denominator.** There is no
  observer or non-voting seat; a recused member must be removed from the roster
  passed to the engine, and that removal is itself a governance act to record.
- **Abstentions count toward quorum but not toward the majority.** Presence-based
  quorum with a majority of votes cast is the common charter convention; if yours
  differs, set ``min_votes_for`` and say so in the charter.
- **A veto is not overridable here.** There is no override flag and no escalation
  path, deliberately. An override is a charter-level decision that belongs in the
  minutes, not in a function argument.
"""
import logging
import math
from collections.abc import Sequence as _SequenceABC
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Set

logger = logging.getLogger(__name__)

#: House default: share of the committee that must participate for the vote to
#: count. Charters commonly require a *majority*; 50.0 with an inclusive
#: comparison lets an even-sized committee transact at exactly half. Not a standard.
DEFAULT_QUORUM_PCT = 50.0

#: House default: maximum share of fund AUM one strategy may hold after an
#: increase. Not UCITS, not AIFMD, not any regulator's number -- see the module
#: docstring and ``references/standards.md``.
DEFAULT_MAX_SINGLE_STRATEGY_AUM_PCT = 20.0

#: House default: affirmative votes required in addition to FOR > AGAINST. One is
#: the floor already implied by the majority rule; raise it if your charter
#: requires a minimum number of supporters rather than merely a plurality.
DEFAULT_MIN_VOTES_FOR = 1

#: House default: whether an AGAINST vote from a veto-holding member is treated as
#: a veto. True is the conservative (more blocking) reading and preserves this
#: engine's historical behaviour; False lets the risk officer dissent without
#: blocking, and reserves the veto for a deliberate VETO ballot. A charter choice.
DEFAULT_VETO_HOLDER_AGAINST_COUNTS_AS_VETO = True

STATUS_APPROVED = "APPROVED"
STATUS_REJECTED_RISK_BREACH = "REJECTED_RISK_BREACH"
STATUS_REJECTED_QUORUM_FAIL = "REJECTED_QUORUM_FAIL"
STATUS_REJECTED_CRO_VETO = "REJECTED_CRO_VETO"
STATUS_REJECTED_VOTES = "REJECTED_VOTES"

#: Risk flags recorded alongside a decision. None of these change the verdict on
#: their own; they exist so the minute-taker sees what the tally alone hides.
FLAG_ABOVE_CAP_AFTER_DECREASE = "POST_DECISION_ALLOCATION_STILL_ABOVE_CAP"
FLAG_PROPOSAL_TYPE_MISMATCH = "PROPOSAL_TYPE_CONTRADICTS_AMOUNTS"
FLAG_VETO_RATIONALE_MISSING = "VETO_RECORDED_WITHOUT_RATIONALE"
FLAG_INVALID_VETO_ATTEMPT = "VETO_CAST_BY_MEMBER_WITHOUT_VETO_AUTHORITY"


def _require_non_empty_str(value: Any, name: str) -> str:
    """A governance record keyed by a blank identifier is not a record."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string, got {value!r}")
    return value


def _require_finite(value: Any, name: str) -> float:
    """
    Reject NaN and Inf on any field a threshold is compared against.

    Every comparison against ``NaN`` is False, so a corrupt
    ``proposed_allocation_usd`` used to sail through the concentration cap and be
    reported as a clean ``APPROVED``. That is the worst possible failure for a
    risk mandate check: silent, and in the permissive direction.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a real number, got {value!r}")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(
            f"{name} must be finite, got {value!r}. A non-finite input is corrupt "
            f"upstream data, not a proposal that failed a limit."
        )
    return numeric


def _require_non_negative(value: Any, name: str) -> float:
    """Allocations and AUM are non-negative by construction."""
    numeric = _require_finite(value, name)
    if numeric < 0.0:
        raise ValueError(f"{name} must be >= 0, got {value!r}")
    return numeric


def _require_positive(value: Any, name: str) -> float:
    """
    Strictly positive.

    Applied to ``fund_total_aum_usd`` because the concentration cap is a ratio.
    The previous implementation guarded the division with ``if aum > 0`` and
    skipped the entire risk check when it was not -- a zero or negative AUM
    disabled the fund's concentration mandate silently.
    """
    numeric = _require_finite(value, name)
    if numeric <= 0.0:
        raise ValueError(
            f"{name} must be > 0, got {value!r}. A non-positive AUM makes the "
            f"concentration ratio undefined; it must not silently skip the cap."
        )
    return numeric


def _require_percentage(value: Any, name: str, *, allow_zero: bool = True) -> float:
    """A percentage threshold outside (0, 100] is a configuration error."""
    numeric = _require_finite(value, name)
    lower_ok = numeric >= 0.0 if allow_zero else numeric > 0.0
    if not lower_ok or numeric > 100.0:
        bound = "0 <= x <= 100" if allow_zero else "0 < x <= 100"
        raise ValueError(f"{name} must satisfy {bound}, got {value!r}")
    return numeric


def _require_non_negative_int(value: Any, name: str) -> int:
    """``bool`` is a subclass of ``int``; ``True`` must not become 1 vote."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an int, got {value!r}")
    if value < 0:
        raise ValueError(f"{name} must be >= 0, got {value!r}")
    return value


def _require_bool(value: Any, name: str) -> bool:
    """
    Require a real ``bool`` -- never coerce by truthiness.

    ``has_veto_power="no"`` is truthy. A roster assembled from CSV, JSON or by an
    LLM agent can hand veto authority to every member, or -- worse, since it fails
    open -- to none of them if the flag arrives as an empty string.
    """
    if not isinstance(value, bool):
        raise ValueError(
            f"{name} must be a bool, got {value!r}. Governance flags are never "
            f"coerced by truthiness -- the string 'no' is truthy."
        )
    return value


def _require_enum(value: Any, enum_cls: type, name: str) -> Any:
    """
    Accept a member of ``enum_cls`` or a string that names one.

    The vote enums subclass ``str``, so an arbitrary string would otherwise
    compare unequal to every member and be counted as nothing at all.
    """
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(value)
    except (ValueError, KeyError):
        valid = ", ".join(m.value for m in enum_cls)
        raise ValueError(
            f"{name} must be a {enum_cls.__name__} ({valid}), got {value!r}")


def _require_optional_iso_timestamp(value: Any, name: str) -> str:
    """
    Validate ``timestamp_iso`` when supplied. Empty means "not recorded".

    An unparseable timestamp in an audit record is worse than an absent one: it
    looks like evidence until someone tries to order the ballots by it.
    """
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO-8601 string, got {value!r}")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(
            f"{name} must be an ISO-8601 timestamp, got {value!r}")
    return value


class VoteType(str, Enum):
    FOR = "FOR"
    AGAINST = "AGAINST"
    ABSTAIN = "ABSTAIN"
    VETO = "VETO"


class CommitteeRole(str, Enum):
    CHIEF_INVESTMENT_OFFICER = "CIO"
    CHIEF_RISK_OFFICER = "CRO"
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
    """
    One seat on the committee. Every member on the roster counts toward the quorum
    denominator -- there is no observer seat. Remove a recused member from the
    roster and minute the recusal.

    ``has_veto_power`` is the charter's grant, not the role: the engine never
    infers veto authority from ``CommitteeRole.CHIEF_RISK_OFFICER``, because which
    seat holds a veto is a governing-document decision and some committees grant
    none at all.
    """
    member_id: str
    name: str
    role: CommitteeRole
    has_veto_power: bool = False

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        self.member_id = _require_non_empty_str(self.member_id, "member_id")
        self.name = _require_non_empty_str(self.name, "name")
        self.role = _require_enum(self.role, CommitteeRole, "role")
        self.has_veto_power = _require_bool(self.has_veto_power, "has_veto_power")


@dataclass
class MemberVote:
    """
    One ballot. ``rationale`` is free text; ``timestamp_iso`` is optional but is
    validated as ISO-8601 when present.
    """
    member_id: str
    vote: VoteType
    rationale: str = ""
    timestamp_iso: str = ""

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        self.member_id = _require_non_empty_str(self.member_id, "member_id")
        self.vote = _require_enum(self.vote, VoteType, "vote")
        if not isinstance(self.rationale, str):
            raise ValueError(f"rationale must be a string, got {self.rationale!r}")
        self.timestamp_iso = _require_optional_iso_timestamp(
            self.timestamp_iso, "timestamp_iso")


@dataclass
class AllocationProposal:
    """
    The capital allocation being put to the committee.

    ``proposed_allocation_usd`` is the allocation the strategy would hold **after**
    the decision, not the delta.
    """
    proposal_id: str
    strategy_id: str
    proposal_type: ProposalType
    current_allocation_usd: float
    proposed_allocation_usd: float
    fund_total_aum_usd: float
    max_single_strategy_aum_pct: float = DEFAULT_MAX_SINGLE_STRATEGY_AUM_PCT
    submitted_by: str = ""

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Validate in place; re-run by the engine, since fields are reassignable."""
        self.proposal_id = _require_non_empty_str(self.proposal_id, "proposal_id")
        self.strategy_id = _require_non_empty_str(self.strategy_id, "strategy_id")
        self.proposal_type = _require_enum(
            self.proposal_type, ProposalType, "proposal_type")
        self.current_allocation_usd = _require_non_negative(
            self.current_allocation_usd, "current_allocation_usd")
        self.proposed_allocation_usd = _require_non_negative(
            self.proposed_allocation_usd, "proposed_allocation_usd")
        self.fund_total_aum_usd = _require_positive(
            self.fund_total_aum_usd, "fund_total_aum_usd")
        self.max_single_strategy_aum_pct = _require_percentage(
            self.max_single_strategy_aum_pct, "max_single_strategy_aum_pct",
            allow_zero=False)
        if not isinstance(self.submitted_by, str):
            raise ValueError(f"submitted_by must be a string, got {self.submitted_by!r}")

    def proposed_pct_of_aum(self) -> float:
        """Share of fund AUM the strategy would hold if the proposal carried."""
        return (self.proposed_allocation_usd / self.fund_total_aum_usd) * 100.0

    def current_pct_of_aum(self) -> float:
        return (self.current_allocation_usd / self.fund_total_aum_usd) * 100.0

    def is_increase(self) -> bool:
        """
        True when the proposal raises the strategy's capital.

        Decided by the **amounts**, never by ``proposal_type``: the label is
        metadata a human typed, and the concentration cap must not be applied or
        skipped on the strength of it.
        """
        return self.proposed_allocation_usd > self.current_allocation_usd

    def type_contradicts_amounts(self) -> bool:
        """
        True when the declared ``proposal_type`` disagrees with the numbers.

        Flagged, not rejected -- a mislabelled proposal is a minute-taking problem,
        and the engine already decides on the amounts. But an audit record whose
        label says DECREASE while the money goes up is worth someone's attention.
        """
        proposed, current = self.proposed_allocation_usd, self.current_allocation_usd
        if self.proposal_type is ProposalType.ALLOCATION_INCREASE:
            return proposed <= current
        if self.proposal_type is ProposalType.ALLOCATION_DECREASE:
            return proposed >= current
        if self.proposal_type is ProposalType.STRATEGY_ONBOARDING:
            return current != 0.0 or proposed <= 0.0
        if self.proposal_type is ProposalType.STRATEGY_DECOMMISSION:
            return proposed != 0.0
        return False


@dataclass
class CommitteeGovernancePolicy:
    """
    The committee's own voting rules. All defaults are house heuristics -- no
    regulator sets any of them. Whatever is used is recorded in every decision's
    ``policy_applied`` so a reviewer can see which bar was actually cleared.
    """
    quorum_percentage: float = DEFAULT_QUORUM_PCT
    min_votes_for: int = DEFAULT_MIN_VOTES_FOR
    veto_holder_against_counts_as_veto: bool = DEFAULT_VETO_HOLDER_AGAINST_COUNTS_AS_VETO

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        self.quorum_percentage = _require_percentage(
            self.quorum_percentage, "quorum_percentage")
        self.min_votes_for = _require_non_negative_int(
            self.min_votes_for, "min_votes_for")
        self.veto_holder_against_counts_as_veto = _require_bool(
            self.veto_holder_against_counts_as_veto,
            "veto_holder_against_counts_as_veto")

    def as_dict(self) -> Dict[str, Any]:
        """Policy snapshot embedded in every decision, so the record is auditable."""
        return {
            "quorum_percentage": self.quorum_percentage,
            "min_votes_for": self.min_votes_for,
            "veto_holder_against_counts_as_veto":
                self.veto_holder_against_counts_as_veto,
        }

    def weakened_thresholds(self) -> List[str]:
        """
        Rules set more permissively than the shipped defaults.

        A quorum of 0% with ``min_votes_for=0`` approves an unattended proposal
        while still emitting the string ``APPROVED``. Naming the relaxations keeps
        a deliberately loosened committee distinguishable from a strict one.
        """
        weakened: List[str] = []
        if self.quorum_percentage < DEFAULT_QUORUM_PCT:
            weakened.append(
                f"quorum_percentage: {self.quorum_percentage} < default "
                f"{DEFAULT_QUORUM_PCT}")
        if self.min_votes_for < DEFAULT_MIN_VOTES_FOR:
            weakened.append(
                f"min_votes_for: {self.min_votes_for} < default "
                f"{DEFAULT_MIN_VOTES_FOR}")
        if not self.veto_holder_against_counts_as_veto:
            weakened.append(
                "veto_holder_against_counts_as_veto: False < default True")
        return weakened


@dataclass
class CommitteeGovernanceDecision:
    """
    The minute. ``decision_status`` names the first reason that fired;
    ``rejection_reasons`` lists every reason, so nothing is hidden by precedence.
    """
    proposal_id: str
    strategy_id: str
    is_approved: bool
    decision_status: str
    quorum_met: bool
    votes_for: int
    votes_against: int
    votes_abstain: int
    veto_triggered_by: Optional[str]
    audit_notes: str
    votes_veto: int = 0
    veto_triggered_by_id: Optional[str] = None
    quorum_pct: float = 0.0
    quorum_required_pct: float = 0.0
    participating_member_ids: List[str] = field(default_factory=list)
    committee_size: int = 0
    proposed_pct_of_aum: float = 0.0
    max_single_strategy_aum_pct: float = 0.0
    rejection_reasons: List[str] = field(default_factory=list)
    risk_flags: List[str] = field(default_factory=list)
    policy_applied: Dict[str, Any] = field(default_factory=dict)
    policy_weakened: List[str] = field(default_factory=list)
    decided_at_utc: str = ""


@dataclass
class _Ballot:
    """Internal tally result. Not part of the public API."""
    participating_ids: List[str]
    votes_for: int
    votes_against: int
    votes_abstain: int
    votes_veto: int
    veto_member: Optional[CommitteeMember]
    veto_rationale: str
    risk_flags: List[str]


class StrategyCommitteeGovernanceEngine:
    """
    Strategy committee governance engine: evaluates a capital allocation proposal
    against the fund's concentration mandate, the committee's quorum and voting
    rules, and any veto held under the committee charter, and returns an auditable
    decision record.

    Approving a proposal records that a vote was taken and carried. It allocates
    no capital, verifies no claim in the proposal, and authorises no order.
    """

    def __init__(
        self,
        committee_members: Sequence[CommitteeMember],
        quorum_percentage: Optional[float] = None,
        policy: Optional[CommitteeGovernancePolicy] = None,
    ) -> None:
        """
        Args:
            committee_members: the voting roster. Every member counts toward the
                quorum denominator. Duplicate ``member_id`` values are rejected --
                silently collapsing them used to shrink the denominator and lower
                the bar the committee had to clear.
            quorum_percentage: convenience override for ``policy.quorum_percentage``.
                Mutually exclusive with ``policy``.
            policy: full voting policy. Defaults to the shipped house rules.
        """
        if policy is not None and quorum_percentage is not None:
            raise ValueError(
                "pass either quorum_percentage or policy, not both -- two "
                "quorum thresholds in one engine is a configuration ambiguity")
        if policy is not None and not isinstance(policy, CommitteeGovernancePolicy):
            raise ValueError(
                f"policy must be a CommitteeGovernancePolicy, got {policy!r}")

        if policy is not None:
            self.policy = policy
        elif quorum_percentage is not None:
            self.policy = CommitteeGovernancePolicy(quorum_percentage=quorum_percentage)
        else:
            self.policy = CommitteeGovernancePolicy()
        self.policy.validate()

        if isinstance(committee_members, (str, bytes)) or not isinstance(
                committee_members, _SequenceABC):
            raise ValueError(
                f"committee_members must be a sequence of CommitteeMember, "
                f"got {committee_members!r}")
        if not committee_members:
            raise ValueError(
                "committee_members must not be empty -- an empty committee has no "
                "quorum denominator and cannot take a decision")

        members: Dict[str, CommitteeMember] = {}
        for member in committee_members:
            if not isinstance(member, CommitteeMember):
                raise ValueError(
                    f"committee_members must contain CommitteeMember, got {member!r}")
            member.validate()
            if member.member_id in members:
                raise ValueError(
                    f"duplicate member_id {member.member_id!r} on the committee "
                    f"roster; each seat must be distinct or the quorum denominator "
                    f"is understated")
            members[member.member_id] = member
        self.members: Dict[str, CommitteeMember] = members

        if not any(m.has_veto_power for m in members.values()):
            logger.info(
                "Committee roster grants no member veto power; "
                "%s can never be returned.", STATUS_REJECTED_CRO_VETO)

        weakened = self.policy.weakened_thresholds()
        if weakened:
            logger.warning(
                "Committee governance policy is weaker than the shipped defaults: %s",
                "; ".join(weakened))

    @property
    def quorum_percentage(self) -> float:
        """Quorum threshold in force, for callers that read it off the engine."""
        return self.policy.quorum_percentage

    def evaluate_proposal(
        self,
        proposal: AllocationProposal,
        member_votes: Sequence[MemberVote],
    ) -> CommitteeGovernanceDecision:
        """
        Evaluate a capital allocation proposal and return the governance record.

        The ballot is always tallied in full, even when the proposal is rejected on
        the concentration mandate before the votes could matter: a minute that
        reports ``quorum_met=False`` and zero votes for a meeting where every
        member voted is a false record.

        Raises:
            ValueError: on a malformed proposal, roster or ballot -- an unknown
                voter, a duplicate ballot, a non-finite amount, a non-positive
                fund AUM. Corrupt input is a data failure and must not be reported
                as a committee outcome.
        """
        if not isinstance(proposal, AllocationProposal):
            raise ValueError(
                f"proposal must be an AllocationProposal, got {proposal!r}")
        proposal.validate()
        self.policy.validate()

        ballot = self._tally(member_votes)
        risk_flags = list(ballot.risk_flags)

        proposed_pct = proposal.proposed_pct_of_aum()
        cap_pct = proposal.max_single_strategy_aum_pct
        above_cap = proposed_pct > cap_pct

        if proposal.type_contradicts_amounts():
            risk_flags.append(
                f"{FLAG_PROPOSAL_TYPE_MISMATCH}: declared "
                f"{proposal.proposal_type.value} but allocation moves from "
                f"${proposal.current_allocation_usd:,.2f} to "
                f"${proposal.proposed_allocation_usd:,.2f}")

        # A cap breach blocks an *increase*. It must never block a reduction: a
        # committee unwinding a position that is already over the mandate would
        # otherwise be refused by the very control meant to cap it, leaving the
        # breach in place.
        risk_breach = above_cap and proposal.is_increase()
        if above_cap and not risk_breach:
            risk_flags.append(
                f"{FLAG_ABOVE_CAP_AFTER_DECREASE}: {proposed_pct:.2f}% of AUM "
                f"still exceeds the {cap_pct:.2f}% cap after this decision "
                f"(currently {proposal.current_pct_of_aum():.2f}%); the strategy is "
                f"not back inside the mandate and a further step is owed")

        committee_size = len(self.members)
        quorum_pct = (len(ballot.participating_ids) / committee_size) * 100.0
        quorum_met = quorum_pct >= self.policy.quorum_percentage

        veto_member = ballot.veto_member
        majority_met = (
            ballot.votes_for > ballot.votes_against
            and ballot.votes_for >= self.policy.min_votes_for
        )

        reasons: List[str] = []
        if risk_breach:
            reasons.append(
                f"{STATUS_REJECTED_RISK_BREACH}: proposed allocation "
                f"${proposal.proposed_allocation_usd:,.2f} = {proposed_pct:.2f}% of "
                f"AUM exceeds the {cap_pct:.2f}% single-strategy cap")
        if not quorum_met:
            reasons.append(
                f"{STATUS_REJECTED_QUORUM_FAIL}: {len(ballot.participating_ids)}/"
                f"{committee_size} members participated ({quorum_pct:.2f}%), below "
                f"the required {self.policy.quorum_percentage:.2f}%")
        if veto_member is not None:
            reasons.append(
                f"{STATUS_REJECTED_CRO_VETO}: vetoed by {veto_member.name} "
                f"({veto_member.role.value}). Rationale: {ballot.veto_rationale}")
        if not majority_met:
            reasons.append(
                f"{STATUS_REJECTED_VOTES}: {ballot.votes_for} FOR / "
                f"{ballot.votes_against} AGAINST (min_votes_for="
                f"{self.policy.min_votes_for})")

        if risk_breach:
            status = STATUS_REJECTED_RISK_BREACH
        elif not quorum_met:
            status = STATUS_REJECTED_QUORUM_FAIL
        elif veto_member is not None:
            status = STATUS_REJECTED_CRO_VETO
        elif not majority_met:
            status = STATUS_REJECTED_VOTES
        else:
            status = STATUS_APPROVED
        is_approved = not reasons

        notes = (
            f"COMMITTEE GOVERNANCE [{status}] ({proposal.proposal_id}): "
            f"Strategy = {proposal.strategy_id}, "
            f"Allocation = ${proposal.proposed_allocation_usd:,.2f} "
            f"({proposed_pct:.2f}% of AUM, cap {cap_pct:.2f}%), "
            f"Quorum = {len(ballot.participating_ids)}/{committee_size} "
            f"({quorum_pct:.2f}%, required {self.policy.quorum_percentage:.2f}%), "
            f"Votes = {ballot.votes_for} FOR / {ballot.votes_against} AGAINST / "
            f"{ballot.votes_abstain} ABSTAIN."
        )
        if reasons:
            notes += " Rejection reasons: " + "; ".join(reasons) + "."
        if risk_flags:
            notes += " Flags: " + "; ".join(risk_flags) + "."

        if is_approved:
            logger.info(notes)
        else:
            logger.warning(notes)

        return CommitteeGovernanceDecision(
            proposal_id=proposal.proposal_id,
            strategy_id=proposal.strategy_id,
            is_approved=is_approved,
            decision_status=status,
            quorum_met=quorum_met,
            votes_for=ballot.votes_for,
            votes_against=ballot.votes_against,
            votes_abstain=ballot.votes_abstain,
            veto_triggered_by=veto_member.name if veto_member else None,
            audit_notes=notes,
            votes_veto=ballot.votes_veto,
            veto_triggered_by_id=veto_member.member_id if veto_member else None,
            quorum_pct=quorum_pct,
            quorum_required_pct=self.policy.quorum_percentage,
            participating_member_ids=list(ballot.participating_ids),
            committee_size=committee_size,
            proposed_pct_of_aum=proposed_pct,
            max_single_strategy_aum_pct=cap_pct,
            rejection_reasons=reasons,
            risk_flags=risk_flags,
            policy_applied=self.policy.as_dict(),
            policy_weakened=self.policy.weakened_thresholds(),
            decided_at_utc=datetime.now(timezone.utc).isoformat(),
        )

    def _tally(self, member_votes: Sequence[MemberVote]) -> _Ballot:
        """
        Count the ballot.

        Three rules that the previous implementation got wrong, each of which
        could flip a rejection into an approval:

        - **One ballot per member.** A repeated ``member_id`` raises rather than
          being counted again; the same member voting FOR three times used to
          outvote two genuine AGAINST votes.
        - **No unknown voters.** A ``member_id`` not on the roster raises rather
          than being dropped in silence.
        - **A VETO ballot is never discarded.** From a member without veto
          authority it counts as AGAINST and is flagged; it used to fall through
          every branch and vanish from the tally entirely.
        """
        if isinstance(member_votes, (str, bytes)) or not isinstance(
                member_votes, _SequenceABC):
            raise ValueError(
                f"member_votes must be a sequence of MemberVote, got {member_votes!r}")

        participating: List[str] = []
        seen: Set[str] = set()
        votes_for = votes_against = votes_abstain = votes_veto = 0
        veto_member: Optional[CommitteeMember] = None
        veto_rationale = ""
        flags: List[str] = []

        for vote in member_votes:
            if not isinstance(vote, MemberVote):
                raise ValueError(
                    f"member_votes must contain MemberVote, got {vote!r}")
            vote.validate()

            member = self.members.get(vote.member_id)
            if member is not None:
                # Re-validated here, not only at construction: a roster entry is a
                # mutable dataclass, and ``has_veto_power`` reassigned to a truthy
                # string after the engine was built would hand out a veto the
                # charter never granted.
                member.validate()
            if member is None:
                raise ValueError(
                    f"vote from {vote.member_id!r}, who is not on the committee "
                    f"roster. An unrecognised ballot is a data failure; dropping "
                    f"it silently would change the tally without a trace.")
            if vote.member_id in seen:
                raise ValueError(
                    f"duplicate ballot from {vote.member_id!r}. One member, one "
                    f"vote -- a repeated ballot would inflate the tally.")
            seen.add(vote.member_id)
            participating.append(vote.member_id)

            is_veto_ballot = vote.vote is VoteType.VETO
            counts_as_veto = member.has_veto_power and (
                is_veto_ballot
                or (vote.vote is VoteType.AGAINST
                    and self.policy.veto_holder_against_counts_as_veto)
            )

            if is_veto_ballot:
                votes_veto += 1
                if not member.has_veto_power:
                    flags.append(
                        f"{FLAG_INVALID_VETO_ATTEMPT}: {member.member_id} "
                        f"({member.role.value}) cast VETO without veto authority; "
                        f"counted as AGAINST")

            if counts_as_veto and veto_member is None:
                veto_member = member
                veto_rationale = vote.rationale
                if not vote.rationale.strip():
                    # Recorded, not rejected: refusing the ballot would let a
                    # missing rationale erase the veto and unblock the proposal.
                    flags.append(
                        f"{FLAG_VETO_RATIONALE_MISSING}: {member.member_id} "
                        f"({member.role.value})")

            # A VETO ballot is dissent, and is counted as such in the tally.
            if vote.vote is VoteType.FOR:
                votes_for += 1
            elif vote.vote in (VoteType.AGAINST, VoteType.VETO):
                votes_against += 1
            else:
                votes_abstain += 1

        return _Ballot(
            participating_ids=participating,
            votes_for=votes_for,
            votes_against=votes_against,
            votes_abstain=votes_abstain,
            votes_veto=votes_veto,
            veto_member=veto_member,
            veto_rationale=veto_rationale,
            risk_flags=flags,
        )
