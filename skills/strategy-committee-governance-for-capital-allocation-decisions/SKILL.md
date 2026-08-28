---
name: strategy-committee-governance-for-capital-allocation-decisions
description: >-
  Use when a multi-strategy fund puts a capital allocation — onboarding, scaling, cutting, decommissioning — to a strategy committee, and the vote must produce an auditable record: quorum, per-member ballots, the fund's single-strategy concentration mandate, any charter veto, and the thresholds that were in force.
domain: Investment Governance & Capital Allocation
subdomain: Strategy Committee Governance
tags: ["strategy-committee", "capital-allocation", "investment-governance", "cro-veto", "quorum-threshold", "risk-mandate", "audit-trail"]
brokers_frameworks: ["AIFMD Directive 2011/61/EU", "Commission Delegated Regulation (EU) 231/2013", "SEC Advisers Act Rules 206(4)-7 / 204-2", "Investment Policy Statement (IPS)", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a multi-strategy fund's committee votes on a capital allocation — onboarding a strategy, scaling it up, cutting it back, decommissioning it — and the outcome has to survive being read six months later. The engine evaluates one proposal against four things, in this precedence order, and records all of them:

1. **Risk mandate** — would an *increase* take the strategy above the fund's single-strategy concentration cap? → `REJECTED_RISK_BREACH`
2. **Quorum** — did enough of the roster participate? → `REJECTED_QUORUM_FAIL`
3. **Veto** — did a member the charter grants a veto exercise it? → `REJECTED_CRO_VETO`
4. **Majority** — `FOR > AGAINST`, and `FOR >= min_votes_for`? → `REJECTED_VOTES`, else `APPROVED`

`decision_status` names the first reason that fired; `rejection_reasons` lists every one, so a proposal that both breached the mandate and was vetoed keeps both facts.

The value it adds is not the arithmetic. It is that the ballot, the mandate check and **the thresholds actually in force** are captured together, in one record, so a reviewer can reproduce the decision rather than take someone's word for it.

## When NOT to Use

- **As the thing that allocates capital.** `APPROVED` is a minute. It transfers no money, sets no position limit, and blocks no order. The controls that actually bound a live strategy are `multi-strategy-capital-allocation-limits`, `correlation-aware-exposure-limits` and `kill-switch-and-drawdown-circuit-breakers`.
- **As a fund-level concentration check.** The cap sees **one strategy at a time**. Five strategies at 19% each pass individually and sum to 95% of the fund; two inside the cap holding the same underlying risk are one position. Reading a clean record as "the fund is within its mandate" is a misreading.
- **As verification of the proposal.** `fund_total_aum_usd` is a caller-supplied number, not a reconciled NAV. A stale or optimistic AUM makes every percentage in the record wrong in the permissive direction.
- **As identity assurance.** `member_id` is whatever the caller passed. The engine checks it against the roster and rejects unknown ids, but cannot tell who typed the vote.
- **As a compliance claim.** No regulator prescribes a quorum, a voting rule, a CRO veto or a per-strategy AUM cap. Running this engine makes a firm compliant with nothing — see `references/standards.md`.
- **For onboarding readiness.** Whether a strategy is *fit* to receive capital is `new-strategy-onboarding-checklist`; this skill covers whether the committee *decided* to give it any.

## Prerequisites

- Committee roster (`CommitteeMember`: `member_id`, `name`, `role`, `has_veto_power`). Every member counts toward the quorum denominator — there is no observer seat, and a recused member must be removed from the roster and minuted.
- Proposal (`AllocationProposal`: `proposal_id`, `strategy_id`, `proposal_type`, `current_allocation_usd`, `proposed_allocation_usd`, `fund_total_aum_usd`, `max_single_strategy_aum_pct`). `proposed_allocation_usd` is the allocation **after** the decision, not the delta.
- Ballots (`MemberVote`: `member_id`, `vote` ∈ `FOR`/`AGAINST`/`ABSTAIN`/`VETO`, `rationale`, optional ISO-8601 `timestamp_iso`).
- Voting rules (`CommitteeGovernancePolicy`: `quorum_percentage`, `min_votes_for`, `veto_holder_against_counts_as_veto`) you are willing to defend. **The defaults are house heuristics, not standards.** Quorum, voting rules and any per-strategy cap come from your LPA, committee charter or IPS.
- Two caller conventions the engine cannot enforce:
  - `has_veto_power` is read from the **charter**, never inferred from `CommitteeRole.CHIEF_RISK_OFFICER`. Some committees grant no veto at all.
  - A veto is only as independent as its holder. AIFMD Art. 15(1) requires risk management be functionally and hierarchically separated from portfolio management; a veto held by someone who reports to the PM being voted on is decoration.

## Workflow

1. **Validate before anyone votes**:
   - Non-finite or negative amounts, a non-positive `fund_total_aum_usd`, a cap outside $(0, 100]$, blank identifiers, unknown voters and duplicate ballots all raise `ValueError`.
   - **Decision point — corrupt input is a data failure, not a committee outcome.** `REJECTED_RISK_BREACH` sends the risk officer to look at the strategy; "the AUM field was zero" sends someone to look at the NAV feed. Returning the former for the latter routes the wrong team. And the two silent versions of this were the dangerous ones: `NaN > 20.0` is `False`, and the old cap check was guarded by `if fund_total_aum_usd > 0` — either input skipped the fund's mandate entirely and returned `APPROVED`.
2. **Risk mandate pre-check**:
   - Reject an **increase** that would leave the strategy above `max_single_strategy_aum_pct`, decided on the amounts and never on the declared `proposal_type`.
   - **Decision point — never block a reduction.** A committee cutting a strategy from 40% to 30% of AUM under a 20% cap is reducing the breach. Rejecting that proposal leaves the *larger* position in place: the concentration control blocking the only action that reduces concentration. The engine allows it and flags `POST_DECISION_ALLOCATION_STILL_ABOVE_CAP` so the minute shows a further step is owed.
3. **Quorum**:
   - Participants / roster $\ge$ `quorum_percentage`, inclusive at the threshold. Abstentions count toward quorum, not toward the majority.
   - **Decision point — one member, one ballot, and no strangers.** A duplicate `member_id` or an id not on the roster raises rather than being counted twice or dropped in silence. Three FOR ballots from one member used to outvote two genuine AGAINST ballots.
4. **Veto**:
   - A `VETO` from a veto-holder blocks. Under the default charter setting an `AGAINST` from that member also blocks; set `veto_holder_against_counts_as_veto=False` to reserve the veto for a deliberate `VETO` ballot.
   - **Decision point — a `VETO` ballot is never discarded.** Cast by a member without veto authority it counts as `AGAINST` and is flagged. It used to fall through every branch and vanish: two members voting VETO and one voting FOR returned `APPROVED`.
   - **Decision point — a veto with no rationale still blocks.** It is flagged, not rejected. Refusing the ballot would let a missing rationale erase the veto and unblock the proposal. There is no override argument, deliberately.
5. **Majority tally**: `FOR > AGAINST` and `FOR >= min_votes_for`.
6. **Emit and persist `CommitteeGovernanceDecision`**:
   - **Decision point — the verdict is meaningless without `policy_applied`.** A 0% quorum with `min_votes_for=0` emits the identical `APPROVED` string as a full board; `policy_weakened` names any rule set below the shipped defaults. Persist the whole record — participants, percentages, flags, `decided_at_utc` — not the status string.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating `APPROVED` as an allocation.** It is a record that a vote carried. Nothing downstream is limited, funded or unblocked by it.
- **Reading a clean mandate check as fund-level safety.** The cap is per strategy and blind to both aggregation and correlation.
- **Blocking a de-risking proposal on the concentration cap.** The pre-fix engine rejected a 40% → 30% cut with `REJECTED_RISK_BREACH`, leaving the breach in place. If you reimplement this check, carve out reductions explicitly.
- **Inferring veto authority from a job title.** `has_veto_power` is a charter grant. A CRO without it casts an ordinary dissent, and the record must show that.
- **Letting a `ValueError` be cleaned up until the call succeeds.** An unknown voter or a duplicate ballot is a ballot-integrity failure to investigate, not noisy input to strip.
- **Assuming any of the numbers are regulatory.** No instrument sets a quorum, a voting rule, a CRO veto or a per-strategy AUM cap. The UCITS "5/10/40" limits (Directive 2009/65/EC Art. 52) are frequently misremembered as the source of a 20% cap; they bind exposure **per issuing body**, not per strategy.
- **Storing the verdict without the policy.** `APPROVED` from a toothless config is byte-identical to `APPROVED` from a strict one.
- **Coercing governance flags by truthiness.** `has_veto_power="no"` is truthy. Any roster assembled from CSV, JSON or by an LLM agent is a candidate for handing out a veto nobody granted — the engine rejects non-`bool` rather than coercing.

## Verification

- Instantiate `StrategyCommitteeGovernanceEngine(roster)` with four members, one holding the veto. Submit a $15\text{M}$ / $100\text{M}$ AUM increase with 3 FOR and 1 AGAINST $\implies$ `APPROVED`, `votes_for` 3, `rejection_reasons` empty.
- CRO casts `VETO` $\implies$ `REJECTED_CRO_VETO`, `veto_triggered_by == "Bob (CRO)"`, `veto_triggered_by_id == "M2"`.
- $30\text{M}$ / $100\text{M}$ increase $\implies$ `REJECTED_RISK_BREACH`, **and** the record still reports `quorum_met=True` and the four votes that were cast.
- Boundary: exactly $20\text{M}$ / $100\text{M}$ passes; $20{,}000{,}001$ does not. Exactly 50% participation meets the default quorum; 25% does not.
- De-risking carve-out: `current=40M`, `proposed=30M`, cap 20% $\implies$ `APPROVED` with `POST_DECISION_ALLOCATION_STILL_ABOVE_CAP` in `risk_flags`.
- Ballot integrity: two non-veto members voting `VETO` plus one `FOR` $\implies$ `REJECTED_VOTES` with `votes_against == 2` and two `VETO_CAST_BY_MEMBER_WITHOUT_VETO_AUTHORITY` flags — not `APPROVED`. Reordering the ballots must not change any tally.
- Negative checks that must **raise** `ValueError`: a duplicate ballot; a voter not on the roster; a duplicate `member_id` on the roster; an empty roster; `NaN`/$\pm\infty$/negative amounts; `fund_total_aum_usd` of 0 or negative; a cap of 0 or $>100$; `has_veto_power="no"` or `1`; an unrecognised vote value; an unparseable `timestamp_iso`; passing both `quorum_percentage` and `policy`.
- Auditability: `CommitteeGovernancePolicy(quorum_percentage=0.0, min_votes_for=0, veto_holder_against_counts_as_veto=False)` must still return `APPROVED` for a one-vote proposal, with all three relaxations named in `policy_weakened` and recorded in `policy_applied`.
- Run `python test_strategy_committee_governance_for_capital_allocation_decisions.py` from the `scripts/` directory and confirm a 100% pass rate.

## Related Skills

- `multi-strategy-capital-allocation-limits`
- `correlation-aware-exposure-limits`
- `incremental-capital-deployment-for-new-strategies`
- `new-strategy-onboarding-checklist`
- `capital-reallocation-based-on-live-performance`
- `strategy-lifecycle-retirement-criteria`
- `strategy-decommissioning-and-position-unwind-procedure`
- `risk-control-configuration-change-approval-workflow`
- `kill-switch-and-drawdown-circuit-breakers`
