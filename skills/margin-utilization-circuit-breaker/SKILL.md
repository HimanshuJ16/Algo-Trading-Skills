---
name: margin-utilization-circuit-breaker
description: Use when trading on margin to halt new order placement when margin utilization
  crosses a defined threshold, independent of P&L-based circuit breakers, preventing
  margin calls and forced liquidations by the broker.
domain: algorithmic-trading
subdomain: risk-management
tags:
- risk-management
- margin
- circuit-breaker
- leverage
- margin-call-prevention
brokers_frameworks:
- Custom Risk Engine
- IBKR
- Zerodha
- Alpaca
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill whenever a trading bot operates on margin (leveraged positions). P&L-based
drawdown breakers only trigger after losses materialize; margin utilization can spike dangerously
even on profitable trades if position sizes grow unchecked. This breaker monitors:
- **Margin Utilization Ratio** = Used Margin / Account Equity
- Triggers a hard stop on new entries when utilization exceeds a configurable threshold (e.g. 80%).
- Triggers a warning at a lower threshold (e.g. 60%) to allow preemptive de-risking.

## Prerequisites

- Real-time margin balance and account equity from broker API.
- Configurable warning and hard-stop threshold percentages.

## Workflow

1. **Poll Margin State**: Query broker for used margin, available margin, and account equity.
2. **Compute Utilization**: $\text{util} = \text{used\_margin} / \text{equity}$.
3. **Evaluate Thresholds**:
   - If $\text{util} \ge \text{hard\_stop}$: Block all new entries, log critical alert.
   - If $\text{util} \ge \text{warning}$: Log warning, optionally reduce position sizes.
4. **Allow Normal Trading**: If below warning threshold, approve orders normally.

> Full procedure: see `references/workflows.md`.
> Standards: see `references/standards.md`.
> Checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Stale Margin Data**: Using cached margin values during fast-moving markets.
- **Ignoring Maintenance vs Initial Margin**: Some brokers have different thresholds.
- **Weekend/Holiday Margin Changes**: Brokers may increase margin requirements during off-hours.

## Verification

- Simulate margin utilization at 50%, 70%, and 90% and verify correct threshold responses.
- Confirm hard-stop blocks all new entries and logs critical alert.
- Run `python scripts/test_margin_breaker.py` and confirm 100% pass rate.

## Related Skills

- `kill-switch-and-drawdown-circuit-breakers`
- `multi-strategy-capital-allocation-limits`
- `value-at-risk-var-live-monitoring`
---
