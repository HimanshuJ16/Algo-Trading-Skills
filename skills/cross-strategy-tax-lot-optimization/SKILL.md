---
name: cross-strategy-tax-lot-optimization
description: >-
  Quantitative tax optimization engine for cross-strategy tax-lot selection (HIFO, Specific ID, LTCG optimization), internal order netting, and wash-sale rule interception.
domain: Tax Accounting & Optimization
subdomain: Tax-Lot Accounting
tags: ["tax-lot", "hifo", "tax-loss-harvesting", "wash-sale", "internal-netting", "capital-gains", "cross-strategy"]
brokers_frameworks: ["IRS Form 8949", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in multi-strategy funds, wealth management engines, and unified tax-managed accounts where multiple sub-strategies trade the same asset universe under a single tax entity. Selling shares without specific tax-lot selection defaults to FIFO, causing unnecessary capital gains tax drag. This module applies **Highest-In-First-Out (HIFO)** and **Long-Term Capital Gain (LTCG)** optimization, performs cross-strategy order netting, and intercepts 30-day wash-sale rule violations.

## Prerequisites

- Active tax-lot inventory with attributes: `acquisition_date`, `cost_basis_price`, `quantity`, `holding_days`.
- Current market price for target security ($P_{\text{mkt}}$).

## Workflow

1. **Tax Lot Selection (HIFO / Specific ID)**:
   - Rank open tax lots for sell order:
     - For Tax Loss Harvesting / Gain Minimization: Rank by highest cost basis price ($\max P_{\text{cost}}$).
     - Separate Long-Term ($> 365$ days) vs Short-Term ($\le 365$ days) lots.
2. **Cross-Strategy Order Netting**:
   - Aggregate buy orders and sell orders across sub-strategies for symbol $S$.
   - Compute $\text{Net Order} = \sum Q_{\text{buy}} - \sum Q_{\text{sell}}$.
3. **Wash Sale Interception**:
   - Check if any sub-strategy purchased symbol $S$ within 30 days prior to or after a loss-realizing sell order.
   - If wash sale triggered $\implies$ Disallow loss deduction and defer cost basis adjustment.
4. **Audit Report Generation**: Generate structured `TaxLotOptimizationReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Defaulting to Broker FIFO**: Accepting default broker FIFO accounting, realizing short-term capital gains first instead of high-cost lots.
- **Cross-Strategy Wash Sale Triggers**: Pod A selling AAPL at a loss while Pod B buys AAPL on the same day, triggering an IRS Wash Sale violation.
- **Un-netted Execution Drag**: Sending Pod A's sell 1000 shares and Pod B's buy 1000 shares to the market separately, incurring double commissions and bid-ask spread costs.

## Verification

- Instantiate `CrossStrategyTaxLotOptimizer`. Add 3 tax lots for AAPL: Lot A (\$150, 400 days old), Lot B (\$200, 100 days old), Lot C (\$100, 50 days old). Market price = \$180. Submit a sell order for 100 shares using `HIFO_MIN_TAX`. Verify optimizer selects Lot B (\$200 cost basis), realizing a \$20/share capital loss. Verify wash-sale check flags clean execution.
- Run `python scripts/test_cross_strategy_tax_lot_optimization.py`.

## Related Skills

- `wash-sale-rule-tracking-us`
- `fifo-vs-specific-lot-tax-accounting-methods`
---
