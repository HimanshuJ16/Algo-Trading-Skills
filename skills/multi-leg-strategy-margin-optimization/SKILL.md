---
name: multi-leg-strategy-margin-optimization
description: >-
  Multi-leg options strategy margin optimization engine evaluating Reg T and Portfolio Margin requirements, combining vertical spreads and Iron Condors to minimize capital tie-up.
domain: Risk Management & Margin Optimization
subdomain: Multi-Leg Derivatives & Options Margin Efficiency
tags: ["options-margin", "multi-leg-strategy", "reg-t", "portfolio-margin", "vertical-spread", "iron-condor", "margin-optimization"]
brokers_frameworks: ["OCC TIMS Margin Rules", "Reg T Margin Rules", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when executing complex multi-leg options strategies (e.g., Bull Call Spreads, Bear Put Spreads, Iron Condors, Iron Butterflies). Submitting options legs as uncombined single orders forces the broker to calculate naked short option margin (e.g., $20\%$ of underlying value per short leg), trapping excessive buying power. Combining multi-leg orders into recognized exchange spread definitions or Portfolio Margin structures caps required margin to the **Defined Maximum Risk** (Spread Width - Net Credit), freeing up $70\% - 90\%$ of capital.

## Prerequisites

- Multi-leg strategy payload (`symbol`, `underlying_price`, `legs`: list of `OptionLeg`).
- Option leg parameters (`option_type`, `action`, `strike`, `quantity`, `premium`).

## Workflow

1. **Uncombined Naked Margin Calculation**:
   - Compute independent margin for each leg:
     - Long options: $\text{Margin} = \text{Premium Paid} \times 100 \times Q$.
     - Short Naked Call: $\text{Margin} = \max(0.20 S - \text{OTM} + P, 0.10 S + P) \times 100 \times Q$.
     - Short Naked Put: $\text{Margin} = \max(0.20 S - \text{OTM} + P, 0.10 K + P) \times 100 \times Q$.
   - Sum uncombined leg margins: $M_{\text{uncombined}} = \sum M_{\text{leg}, i}$.
2. **Recognized Multi-Leg Combo Margin Calculation**:
   - Classify multi-leg strategy type:
     - **Vertical Spread**: $M_{\text{combo}} = |K_{\text{short}} - K_{\text{long}}| \times 100 \times Q - \text{Net Credit}$.
     - **Iron Condor**: $M_{\text{combo}} = \max(\text{Width}_{\text{call}}, \text{Width}_{\text{put}}) \times 100 \times Q - \text{Net Credit}$.
3. **Margin Optimization Savings Audit**:
   - Calculate capital savings: $\Delta M = M_{\text{uncombined}} - M_{\text{combo}}$.
   - Compute percentage margin reduction: $\text{Reduction} = (\Delta M / M_{\text{uncombined}}) \times 100\%$.
4. **Audit Report Generation**: Output structured `MarginOptimizationReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Unlinked Execution Legging Risk**: Submitting legs as individual orders instead of multi-leg combo orders, temporarily triggering naked margin calls.
- **Mismatched Leg Quantities**: Failing to match long and short leg quantities ($Q_{\text{long}} \neq Q_{\text{short}}$), leaving residual unhedged naked short contracts.
- **Ignoring Early Exercise Assignment**: Failing to monitor near-the-money short legs prior to expiration.

## Verification

- Instantiate `MultiLegStrategyMarginOptimizerEngine`. Audit Iron Condor on AAPL ($S = \$150$, short 145/155 strikes, long 140/160 strikes) $\implies$ verify uncombined margin ($\sim \$6,000$) vs recognized combo margin ($\$500$), demonstrating $> 90\%$ margin reduction.
- Run `python scripts/test_multi_leg_strategy_margin_optimization.py`.

## Related Skills

- `margin-utilization-circuit-breaker`
- `leverage-limit-enforcement-across-instruments`
---
