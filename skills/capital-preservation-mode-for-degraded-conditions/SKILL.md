---
name: capital-preservation-mode-for-degraded-conditions
description: Quantitative kill-switch and capital preservation engine that monitors
  order rates, daily drawdowns, and API errors to automatically halt trading during
  degraded conditions.
domain: Risk Management
subdomain: Emergency Controls
tags:
- kill-switch
- capital-preservation
- drawdown
- circuit-breaker
- risk
brokers_frameworks:
- Generic Risk Engineering
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill to implement the "last line of defense" for an algorithmic trading system. The Capital Preservation Engine acts as an independent observer, enforcing hard constraints on drawdown, order frequency (to prevent runaway logic loops), and API error rates. When a threshold is breached, the engine enters a degraded state, halting all new risk-taking and triggering alerts.

## Prerequisites

- A messaging bus or callback mechanism to intercept all order submissions and execution reports.
- Real-time PnL calculation per strategy or portfolio.

## Workflow

1. **Initialization**: Instantiate `CapitalPreservationEngine` with strict limits (e.g., maximum daily loss of $50,000, maximum 100 orders per minute).
2. **Continuous Monitoring**: 
   - Every time a strategy attempts to route an order, call `check_order_allowed()`.
   - On every fill or mark-to-market update, call `update_pnl()`.
   - On every broker error (e.g., FIX reject), call `register_error()`.
3. **Breach Detection**: If any limit is breached, the engine transitions to `State.HALTED`.
4. **Enforcement**: Once HALTED, `check_order_allowed()` strictly returns `False`, physically blocking the strategy from deploying more capital.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **In-Band Risk Checks**: Embedding the kill-switch inside the strategy logic. If the strategy has a bug and enters an infinite loop, the risk check might be bypassed. The kill-switch must sit *between* the strategy and the execution gateway.
- **Ignoring "Runaway" Algos**: Only monitoring PnL, not order frequency. A strategy rapidly canceling and replacing orders out-of-the-money won't trigger PnL stops but will incur massive exchange penalties or rate limits.

## Verification

- Simulate a strategy that rapidly attempts to send 200 orders in 30 seconds. Verify the engine triggers a HALT based on the order rate limit.
- Run `python scripts/test_capital_preservation_engine.py`.

## Related Skills

- `broker-side-order-throttle-detection`
- `black-swan-playbook-for-halted-markets`
