---
name: cross-account-aggregate-risk-view
description: Quantitative multi-account risk management engine for consolidating positions,
  margin, and PnL across sub-accounts/prime brokers, enforcing firm-wide GMV caps,
  and detecting internal wash trades.
domain: Risk Management & Operations
subdomain: Multi-Account Risk
tags:
- cross-account
- aggregate-risk
- firm-wide-limits
- sub-accounts
- margin-utilization
- wash-trade-detection
brokers_frameworks:
- Generic Risk Engine
- Python Dataclasses
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in multi-account or multi-strategy quantitative fund operations (e.g. trading across multiple sub-accounts at Interactive Brokers, Binance, Coinbase, or CME FCMs). Managing risk at individual account silos fails to detect aggregate firm-wide exposure breaches or internal offsetting friction (e.g. Sub-Account 1 buying AAPL while Sub-Account 2 is selling AAPL). This module aggregates holdings into a unified "single-pane-of-glass" risk hub, enforces firm-wide Gross Market Value (GMV) caps, and flags internal wash trading.

## Prerequisites

- Real-time position, cash, and margin balances per sub-account.
- Market price matrix for all portfolio symbols.

## Workflow

1. **Sub-Account Ingestion**: Ingest sub-account records (`account_id`, `cash_usd`, `margin_used`, `margin_limit`, `positions`).
2. **Firm-Wide Valuation & Consolidation**:
   - Total Firm NAV: $\text{NAV}_{firm} = \sum \text{NAV}_a$.
   - Net Position per Symbol: $Q_{net}(s) = \sum_a Q_a(s)$.
   - Gross Market Value (GMV): $\text{GMV}_{firm} = \sum_s |Q_{net}(s) \cdot P(s)|$.
3. **Internal Wash Trade & Offsetting Audit**:
   - Check if $\exists a_1, a_2$ such that $Q_{a1}(s) > 0$ and $Q_{a2}(s) < 0$.
   - Flag as `INTERNAL_OFFSETTING_FRICTION` to optimize margin.
4. **Pre-Trade Firm-Wide Limit Audit**:
   - For proposed order in Sub-Account $A$: Calculate projected $\text{GMV}_{firm}$.
   - Reject or downsize if $\text{GMV}_{firm} > \text{Max GMV Limit}$.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Siloed Account Limit Checks**: Passing a trade in Sub-Account 3 because Sub-Account 3 has available capital, even though the firm-wide single-name limit in NVDA is already 100% full.
- **Ignoring Internal Offsetting Friction**: Paying double borrow and commission fees to hold long 10,000 shares in Account A and short 10,000 shares in Account B.
- **Uncoordinated Margin Utilization**: Allowing aggregate margin utilization across accounts to exceed 85% without a centralized liquidity buffer.

## Verification

- Instantiate `CrossAccountRiskAggregator`. Register Sub-Account 1 (Long 1,000 AAPL @ $150) and Sub-Account 2 (Short 400 AAPL @ $150). Verify Net Position = +600 AAPL, Gross Market Value = $210,000, and internal offsetting is flagged. Submit order in Sub-Account 3 that breaches firm-wide GMV limit ($1M) and verify pre-trade rejection.
- Run `python scripts/test_cross_account_risk_aggregator.py`.

## Related Skills

- `counterparty-and-broker-concentration-risk`
- `multi-order-netting-before-routing`
---
