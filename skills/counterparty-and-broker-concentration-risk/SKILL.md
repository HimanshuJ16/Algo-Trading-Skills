---
name: counterparty-and-broker-concentration-risk
description: >-
  Use when trading across multiple brokers or custodians to limit exposure to any
  single counterparty, bounding losses from broker default, insolvency, or operational
  failure rather than just market risk.
domain: algorithmic-trading
subdomain: risk-management
tags: ["risk-management", "counterparty-risk", "broker-risk", "concentration-limits", "custodian-risk"]
brokers_frameworks: ["Custom Risk Engine", "Multi-Broker"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill whenever trading capital is distributed across multiple brokers, custodians,
or exchanges. Even if market risk is well-hedged, counterparty concentration can cause
catastrophic losses if a single broker defaults (e.g., MF Global 2011, FTX 2022). This skill:
- Tracks capital held at each counterparty as a percentage of total AUM.
- Enforces maximum single-counterparty exposure limits (e.g. 40% of AUM).
- Blocks new deposits or position increases at over-concentrated counterparties.

## Prerequisites

- Registry of all brokers/custodians with current capital held at each.
- Maximum single-counterparty exposure limit as percentage of total AUM.
- Total AUM (assets under management) across all counterparties.

## Workflow

1. **Register Counterparties**: List all brokers/custodians with capital held.
2. **Compute Concentration**: For each counterparty, $\text{conc}_c = \text{capital}_c / \text{AUM}$.
3. **Enforce Limits**: Block new capital allocation if $\text{conc}_c \ge \text{limit}$.
4. **Alert on Drift**: Monitor for concentration drift as P&L shifts balances.

> Full procedure: see `references/workflows.md`.
> Standards: see `references/standards.md`.
> Checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Margin Held as Exposure**: Margin posted to a broker is counterparty exposure.
- **FTX-Style Commingling**: Assuming segregated accounts are truly segregated without verification.
- **Not Counting Pending Settlements**: Unsettled trades are counterparty exposure.

## Verification

- Register 3 counterparties and verify concentration calculations.
- Attempt to increase exposure beyond limit and confirm blocking.
- Run `python scripts/test_counterparty_monitor.py` and confirm 100% pass rate.

## Related Skills

- `multi-strategy-capital-allocation-limits`
- `kill-switch-and-drawdown-circuit-breakers`
---
