---
name: meta-strategy-signal-arbitration
description: >-
  Multi-strategy signal arbitration and internal order netting engine, resolving conflicting sub-strategy signals, enforcing risk-off vetoes, and eliminating redundant execution spread costs.
domain: Portfolio Multi Strategy
subdomain: Signal Arbitration & Internal Order Netting
tags: ["meta-strategy", "signal-arbitration", "internal-order-netting", "conflict-resolution", "risk-veto", "deadband-filter", "spread-savings"]
brokers_frameworks: ["Multi-Strategy Arbitrator", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when managing multi-strategy portfolios running concurrent independent algorithms (e.g. Trend Following, Mean Reversion, Statistical Arbitrage, Sentiment/NLP) on shared asset universes. Sub-strategies frequently generate opposing trading signals on identical symbols (e.g. Strategy A BUY $+\$100,000$ vs Strategy B SELL $-\$60,000$). Routing two opposing orders to market incurs double bid-ask spreads and exchange fees. This module implements **Meta-Strategy Signal Arbitration** and **Internal Order Netting**, evaluating priority risk-off vetoes, calculating weighted consensus signals, and executing only the net difference ($+\$40,000$) to save transaction costs.

## Prerequisites

- Sub-strategy signal payload (`strategy_id`, `symbol`, `raw_signal`: $[-1.0, +1.0]$, `conviction_score`: $[0.0, 1.0]$, `target_notional_usd`, `is_risk_veto`: bool).
- Strategy allocation weights (`strategy_id`, `weight`, `priority_rank`).

## Workflow

1. **Priority Risk-Off Veto Audit**:
   - Audit `is_risk_veto` across all sub-strategies.
   - If any strategy emits `is_risk_veto == True` $\implies$ Enforce absolute risk-off override (`ARBITRATION_VETO_RISK_OFF`).
2. **Weighted Consensus Signal Calculation**:
   - Calculate weighted net consensus signal:
     $$S_{\text{consensus}} = \frac{\sum_k w_k \times S_{k, i} \times C_{k, i}}{\sum_k w_k}$$
   - Calculate Gross Target Notional vs Net Target Notional.
3. **Internal Order Netting & Transaction Savings**:
   - Compute internal order netting savings:
     $$\text{Savings}_{\text{usd}} = (\text{Gross Notional} - |\text{Net Notional}|) \times \frac{\text{spread\_bps} + \text{fee\_bps}}{10,000.0}$$
4. **Deadband Filter Audit**:
   - If $|S_{\text{consensus}} - S_{\text{current}}| < \epsilon_{\text{deadband}} \implies$ Suppress unnecessary rebalancing (`DEADBAND_REBALANCING_SUPPRESSED`).
5. **Audit Report Generation**: Output structured `MetaStrategyArbitrationReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Routing Opposing Market Orders to Exchange**: Sending simultaneous BUY and SELL orders for the same stock from different internal sub-strategies, wasting spread costs.
- **Overriding Risk-Off Signals with Alpha Signals**: Allowing positive alpha signals from a momentum strategy to override a high-priority risk-off stop loss.
- **Over-Rebalancing on Micro Signal Churn**: Rebalancing portfolio positions for tiny 1% consensus signal fluctuations without deadband filtering.

## Verification

- Instantiate `MetaStrategySignalArbitratorEngine`. Audit AAPL with 2 strategies (Strategy 1 BUY $+\$100,000$, Strategy 2 SELL $-\$60,000$). Verify engine nets order to $+\$40,000$, calculates internal netting savings ($\$40.00$ saved at $10\text{ bps}$ cost), and approves `ARBITRATION_NETTED_ORDER_GENERATED`. Audit Risk Veto $\implies$ verify `ARBITRATION_VETO_RISK_OFF`.
- Run `python scripts/test_meta_strategy_signal_arbitration.py`.

## Related Skills

- `multi-order-netting-before-routing`
- `portfolio-level-stop-loss-independent-of-strategy-stops`
---
