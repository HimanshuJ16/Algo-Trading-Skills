# Pre-Flight Checklist — Strategy Committee Capital Allocation

## The roster

- [ ] Does the roster passed to the engine match the committee the **charter**
      defines? Every name on it counts toward the quorum denominator.
- [ ] Is `has_veto_power` set from the charter rather than from job title? The
      engine never infers a veto from `CommitteeRole.CHIEF_RISK_OFFICER`.
- [ ] Has anyone with a conflict been recused — removed from the roster **and**
      minuted? Recusal lowers the quorum denominator; that is deliberate, and it is
      a governance act in its own right.
- [ ] Does the person holding the veto sit outside the reporting line of the
      portfolio manager whose allocation is being voted on? (AIFMD Art. 15(1) /
      Delegated Reg. 231/2013 Art. 42 — a veto held by a subordinate is decoration.)

## The proposal

- [ ] Is `proposed_allocation_usd` the allocation **after** the decision, not the
      change?
- [ ] Is `fund_total_aum_usd` a current, reconciled NAV? A stale or optimistic AUM
      makes every percentage in the record wrong in the permissive direction.
- [ ] Is `max_single_strategy_aum_pct` **your fund's** mandate number, or the 20%
      shipped default? The default is a house heuristic with no regulatory parent.
- [ ] Does the declared `proposal_type` agree with the amounts? A mismatch is
      flagged, not rejected.

## The concentration mandate

- [ ] Has anyone checked the **aggregate**? The cap sees one strategy at a time —
      five strategies at 19% each all pass and sum to 95% of the fund.
- [ ] Has anyone checked **correlation**? Two strategies inside the cap can be one
      position. See `correlation-aware-exposure-limits`.
- [ ] If the decision carries `POST_DECISION_ALLOCATION_STILL_ABOVE_CAP`: is the
      further reduction scheduled, with a date, in the minutes?

## The vote

- [ ] Was quorum met by **participation**, not by a headcount in the room? A member
      who attended and cast no ballot is not in `participating_member_ids`.
- [ ] Does every ballot carry a rationale — and does the veto, if one was cast?
      `VETO_RECORDED_WITHOUT_RATIONALE` means the veto stands but the reason was
      never recorded.
- [ ] Did anyone re-run the evaluation after a `ValueError`? An unknown voter or a
      duplicate ballot is a ballot-integrity failure to investigate, not an input
      to clean up until the call succeeds.
- [ ] If any `VETO_CAST_BY_MEMBER_WITHOUT_VETO_AUTHORITY` flag is present: does the
      member believe they hold a veto the charter does not grant them?
- [ ] At the shipped defaults, two participants and one FOR vote carry a proposal on
      a four-seat committee. Is that the bar your charter intends?

## The record

- [ ] Is the **whole decision** persisted, not just `decision_status`?
- [ ] Is `policy_applied` stored with it? A verdict without its policy snapshot
      proves nothing — a 0% quorum emits the same `APPROVED` string as a full board.
- [ ] Is `policy_weakened` empty? If not, is every relaxation deliberate and
      justified in writing?
- [ ] Were **all** of `rejection_reasons` read, not just `decision_status`? The
      status names the first reason in the ladder; the list names every one.
- [ ] Is the retention period satisfied? For SEC-registered advisers, 17 CFR
      275.204-2(e)(1) requires five years, the first two in an appropriate office.

## After an approval

- [ ] Is everyone clear that `APPROVED` moved no capital, set no limit and blocked
      no order?
- [ ] Are the actual position and exposure limits set —
      `multi-strategy-capital-allocation-limits`,
      `correlation-aware-exposure-limits`?
- [ ] Is the deployment staged rather than funded to target in one step —
      `incremental-capital-deployment-for-new-strategies`?
- [ ] Does the kill switch cover the new size —
      `kill-switch-and-drawdown-circuit-breakers`?
- [ ] Is it defined, **before** the first order, what brings this allocation back to
      committee?
