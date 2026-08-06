---
name: strategy-committee-governance-for-capital-allocation-decisions
description: >-
  Production-grade Strategy Committee Governance Engine enforcing committee quorum requirements, Chief Risk Officer (CRO) veto power, fund risk mandate concentration caps, and voting audit trails for multi-strategy capital allocation decisions.
domain: Investment Governance & Capital Allocation
subdomain: Strategy Committee Governance
tags: ["strategy-committee", "capital-allocation", "investment-governance", "cro-veto", "quorum-threshold", "risk-mandate"]
brokers_frameworks: ["Investment Policy Statement (IPS)", "Multi-Strategy Fund Governance", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when managing institutional capital allocation decisions (onboarding new strategies, scaling allocation, reducing allocation, decommissioning) across a multi-strategy fund. Capital allocation requires formal governance to prevent rogue trading, unapproved risk concentration, or personal biases. This engine enforces committee quorum thresholds ($\ge 50\%$), pre-checks single strategy concentration caps ($\le 20\%$ AUM), honors Chief Risk Officer (CRO) absolute veto power, and logs complete voting audit trails.

## Prerequisites

- Committee composition (`CommitteeMember`: `member_id`, `name`, `role`, `has_veto_power`).
- Capital allocation proposal (`AllocationProposal`: `proposal_id`, `strategy_id`, `proposal_type`, `current_allocation_usd`, `proposed_allocation_usd`, `fund_total_aum_usd`, `max_single_strategy_aum_pct`).
- Member votes (`MemberVote`: `member_id`, `vote`: `FOR`, `AGAINST`, `ABSTAIN`, `VETO`, `rationale`).

## Workflow

1. **Risk Mandate Pre-Check**:
   - Verify proposed allocation $\le \text{max\_single\_strategy\_aum\_pct}$ (e.g. 20% AUM). If breached, issue `REJECTED_RISK_BREACH`.
2. **Quorum Verification**:
   - Verify participating voting members / total committee members $\ge 50\%$. If failed, issue `REJECTED_QUORUM_FAIL`.
3. **CRO Veto Power Check**:
   - Check if any veto-authorized member (CRO) submitted a `VETO` or `AGAINST` vote. If triggered, issue `REJECTED_CRO_VETO`.
4. **Majority Vote Tally**:
   - Tally `FOR` vs `AGAINST` votes. If `FOR > AGAINST`, issue `APPROVED`; otherwise `REJECTED_VOTES`.
5. **Execution Output**: Output structured `CommitteeGovernanceDecision`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Bypassing Committee Governance**: Reallocating capital to strategies informally without recording formal quorum and votes.
- **Overriding Risk Veto Power**: Proceeding with allocation increases despite a formal veto from the Chief Risk Officer.
- **Unchecked Concentration Limits**: Allocating $> 20\%$ of fund AUM into a single high-beta strategy, creating extreme fund-level concentration risk.

## Verification

- Instantiate `StrategyCommitteeGovernanceEngine`. Submit valid proposal ($15\text{M}$ out of $100\text{M}$ AUM, 3 FOR votes) $\implies$ verify `decision_status = "APPROVED"`. Submit proposal where CRO votes VETO $\implies$ verify `decision_status = "REJECTED_CRO_VETO"`. Submit proposal exceeding 20% max cap ($30\text{M}$ out of $100\text{M}$) $\implies$ verify `decision_status = "REJECTED_RISK_BREACH"`.
- Run `python scripts/test_strategy_committee_governance_for_capital_allocation_decisions.py`.

## Related Skills

- `capital-reallocation-based-on-live-performance`
- `strategy-lifecycle-retirement-criteria`
---
