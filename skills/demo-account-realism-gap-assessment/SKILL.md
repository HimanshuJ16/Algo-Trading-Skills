---
name: demo-account-realism-gap-assessment
description: Use when evaluating trading performance on paper/demo broker accounts
  to systematically compare fill latency, slippage, queue depth, and partial fill
  rates against live production executions, calculating a realism reliability index.
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- demo-account
- paper-trading
- realism-gap
- slippage-analysis
- execution-fidelity
brokers_frameworks:
- Broker Environment Assessor
- Python Analytics
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill prior to deploying a paper-tested trading strategy into live capital. Broker paper/demo accounts systematically inflate performance metrics by granting instant fills, ignoring limit order queue depth, suppressing market impact, and under-reporting slippage. This skill records matched execution logs from demo and live trades to quantify the execution realism gap and apply Sharpe ratio haircut factors.

## Prerequisites

- Execution logs from paper/demo environments (timestamp, arrival price, fill price, latency).
- Sample execution logs from live micro-lot trades for baseline comparison.

## Workflow

1. **Record Execution Logs**:
   - Capture order submission timestamp, fill timestamp, arrival mid-price, fill price, and filled quantity.

2. **Compute Latency & Slippage Discrepancy**:
   - Latency Delta: $\Delta t = \text{Latency}_{\text{live}} - \text{Latency}_{\text{demo}}$.
   - Slippage Delta: $\Delta P = |\text{Slippage}_{\text{live}}| - |\text{Slippage}_{\text{demo}}|$.

3. **Calculate Realism Score ($R \in [0, 1]$)**:
   - Composite index weighing latency parity (30%), slippage match (40%), and fill rate ratio (30%).

4. **Compute Strategy Haircut Factor ($\gamma$)**:
   - Scale down paper/demo backtest return expectations prior to live allocation:
     $$\text{Sharpe}_{\text{adjusted}} = \text{Sharpe}_{\text{demo}} \times R$$

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Assuming Demo Fills Equal Live Fills**: Relying on 100% demo fill rates for illiquid options or micro-cap stocks.
- **Ignoring Time-of-Day Volatility**: Comparing demo fills during quiet market hours against live fills during market open/close.
- **Unmapped Order Types**: Demo accounts executing complex bracket/stop-loss orders differently than live engines.

## Verification

- Input sample demo and live execution logs and verify Realism Score $R$ calculation.
- Verify Sharpe ratio haircut calculation correctly scales down demo metrics.
- Run `python scripts/test_realism_assessor.py` and confirm 100% pass rate.

## Related Skills

- `sandbox-vs-production-endpoint-drift`
- `paper-to-live-promotion-checklist`
- `execution-realistic-simulation`
---
