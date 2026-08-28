# Workflows for Strategy Committee Governance for Capital Allocation Decisions

## 0. Before the meeting — the roster is a governance artifact, not a config list

Every `CommitteeMember` you pass counts toward the quorum denominator. There is no
observer seat and no non-voting attendee.

| Decision | Where it belongs | Why the engine cannot make it |
|---|---|---|
| Who sits on the committee | Committee charter / LPA / IPS | The roster you pass *is* the committee, as far as any quorum arithmetic goes |
| Who holds a veto | Charter | `has_veto_power` is read from the roster and never inferred from `CommitteeRole.CHIEF_RISK_OFFICER`; some committees grant no veto at all |
| Whether a dissent from the veto-holder blocks | Charter | Exposed as `veto_holder_against_counts_as_veto`; both readings are defensible and the record must say which one was used |
| Recusal | Minutes, and the roster you pass | Removing a conflicted member lowers the quorum denominator. That is correct, and it is itself a governance act — minute it |

A roster with duplicate `member_id` values raises `ValueError`. Silently collapsing
two seats into one used to shrink the denominator and lower the bar the committee
had to clear.

## 1. Validate the proposal before anyone votes

Malformed input raises `ValueError`. It does not produce a verdict, because
`REJECTED_RISK_BREACH` and "the AUM field was corrupt" call for different people.

- `fund_total_aum_usd` must be **strictly positive**. The concentration check is a
  ratio; the previous implementation guarded the division with `if aum > 0` and
  skipped the entire mandate check when it was not. A zero AUM disabled the cap.
- Amounts must be finite and non-negative. `NaN > 20.0` is `False`, so a corrupt
  `proposed_allocation_usd` used to clear the cap and be recorded as `APPROVED`.
- `max_single_strategy_aum_pct` must lie in $(0, 100]$.
- `proposal_id` and `strategy_id` must be non-blank. A minute keyed by an empty
  identifier is not a record.

`proposed_allocation_usd` is the allocation the strategy would hold **after** the
decision, not the delta. Getting this wrong understates every percentage in the
record.

## 2. Risk mandate pre-check — and the carve-out that matters

The cap rejects an **increase** that would leave the strategy above
`max_single_strategy_aum_pct`. It is decided on the amounts, never on the declared
`proposal_type`, because the label is metadata a human typed.

**A reduction is never blocked by the cap.** A committee cutting a strategy from 40%
to 30% of AUM in a fund with a 20% cap is reducing the breach; refusing that proposal
would leave the larger position in place — the concentration control blocking the only
action that reduces concentration. The engine allows it and records
`POST_DECISION_ALLOCATION_STILL_ABOVE_CAP` so the minute shows the strategy is not yet
back inside the mandate and a further step is owed.

If the declared type contradicts the amounts — `ALLOCATION_DECREASE` while the money
goes up — the decision carries `PROPOSAL_TYPE_CONTRADICTS_AMOUNTS`. The amounts still
govern the verdict.

**What the cap cannot see**: one strategy at a time. Five strategies at 19% each pass
individually and sum to 95% of the fund; two strategies at 15% each holding the same
underlying risk pass individually and are one position. Aggregate and correlated
exposure belong to `multi-strategy-capital-allocation-limits` and
`correlation-aware-exposure-limits`, and a committee that reads a clean
`REJECTED_RISK_BREACH`-free record as "the fund is within its risk mandate" has
misread it.

## 3. Quorum

`len(participating members) / len(roster) * 100 >= quorum_percentage`, inclusive at
the threshold.

- Abstentions count toward quorum. Presence-based quorum with a majority of votes
  *cast* is the common charter convention; if yours counts differently, set
  `min_votes_for` and write the rule into the charter, not into a comment.
- Ballots from ids not on the roster raise `ValueError` rather than being dropped.
  An unrecognised ballot changed the tally silently before; now it stops the audit.
- One member, one ballot. A repeated `member_id` raises. Three FOR ballots from one
  member used to outvote two genuine AGAINST ballots.
- The engine authenticates nobody. `member_id` is whatever the caller supplied.

## 4. Veto

- A `VETO` ballot from a member the roster grants `has_veto_power` blocks the
  proposal.
- Under the default charter setting, an `AGAINST` from that member also blocks. Set
  `veto_holder_against_counts_as_veto=False` if your charter reserves the veto for a
  deliberate `VETO` ballot and lets the risk officer dissent without blocking. The
  setting appears in `policy_applied` on every decision either way.
- A `VETO` from a member **without** veto authority is counted as `AGAINST` and
  flagged `VETO_CAST_BY_MEMBER_WITHOUT_VETO_AUTHORITY`. It used to fall through every
  branch and disappear from the tally entirely — two members voting VETO and one
  voting FOR returned `APPROVED`.
- A veto with an empty `rationale` still blocks, and is flagged
  `VETO_RECORDED_WITHOUT_RATIONALE`. Rejecting the ballot instead would let a missing
  rationale erase the veto and unblock the proposal. Chase the rationale for the
  minutes; do not chase it by re-running without the veto.
- There is no override argument, deliberately. Overriding a veto is a charter-level
  act that belongs in the minutes with a named authoriser.

## 5. Majority

`votes_for > votes_against` **and** `votes_for >= min_votes_for`. Abstentions are
excluded from the comparison but counted in the record.

At the shipped defaults on a four-seat committee, two participants and a single FOR
vote carry a proposal. If that is not the bar your charter intends, raise
`min_votes_for` or `quorum_percentage`; both are recorded, and any relaxation below
the shipped defaults is named in `policy_weakened`.

## 6. The record

`decision_status` names the **first** reason in the ladder
(`RISK_BREACH` → `QUORUM_FAIL` → `CRO_VETO` → `VOTES`); `rejection_reasons` lists
**every** reason that fired, so a proposal that both breached the mandate and was
vetoed does not lose the veto from its history.

Persist the whole decision, not the status string:

- `policy_applied` — the quorum, majority floor and veto rule actually in force. A
  stored verdict without it proves nothing.
- `policy_weakened` — any rule set below the shipped default. Should be empty, or
  justified in writing.
- `participating_member_ids`, `committee_size`, `quorum_pct` — who was there.
- `proposed_pct_of_aum`, `max_single_strategy_aum_pct` — the mandate arithmetic as of
  the vote, which a later AUM revision will not reproduce.
- `risk_flags` — everything the tally alone hides.
- `decided_at_utc` — timezone-aware UTC, set by the engine at decision time.
  `MemberVote.timestamp_iso` is optional, validated as ISO-8601 when supplied, and is
  the caller's to populate per ballot.

The engine writes nothing to disk. For SEC-registered advisers, 17 CFR
275.204-2(e)(1) sets a five-year retention floor for required records, the first two
years in an appropriate office — see `references/standards.md`.

## 7. After an approval

`APPROVED` is a minute, not a transfer. It moves no capital, sets no position limit,
and blocks no order. Before anything trades on it:

1. Set the actual position and exposure limits — `multi-strategy-capital-allocation-limits`,
   `correlation-aware-exposure-limits`.
2. Stage the deployment rather than funding to target in one step —
   `incremental-capital-deployment-for-new-strategies`.
3. Confirm the kill switch covers the new size —
   `kill-switch-and-drawdown-circuit-breakers`.
4. Define, before the first order, what would bring the allocation back to committee.
