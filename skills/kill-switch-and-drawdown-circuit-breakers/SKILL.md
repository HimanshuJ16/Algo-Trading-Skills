---
name: kill-switch-and-drawdown-circuit-breakers
description: Use when a live trading bot needs hard, strategy-independent limits that
  force-flatten positions and halt new orders, so that a strategy bug or unexpected
  market condition cannot cause unbounded loss
domain: algorithmic-trading
subdomain: risk-management
tags:
- risk-management
brokers_frameworks: []
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this for any bot that will place live orders with real capital, regardless of how well-validated the strategy logic is — this skill exists specifically because strategy logic, no matter how well-tested, can behave unexpectedly on data patterns not seen in backtesting, and because the code implementing risk limits must be structurally independent from the code implementing strategy signals so that a bug in the strategy cannot also disable the safeguard.

## Prerequisites

- Explicit, pre-agreed numeric limits: max position size per instrument, max aggregate position size, max daily loss (absolute and/or percentage of capital), max drawdown from peak equity
- A reliable, low-latency source of current position and P&L state independent of the strategy's own internal bookkeeping (ideally reconciled against the broker's actual position/margin data, not just the bot's internal model of its positions)

## Workflow

1. Implement risk checks as a separate module/process from strategy signal generation, with the risk module having the authority to veto or override any order the strategy logic attempts to place — never implement risk limits as "just another condition" inside the strategy's own decision function, since a bug there can silently bypass the check.
2. Define at minimum these circuit breakers, each independently triggerable:
   - **Position size limit:** reject any order that would push a single instrument's position beyond a defined max size.
   - **Aggregate exposure limit:** reject any order that would push total notional exposure across all positions beyond a defined max (this interacts with `correlation-aware-exposure-limits` for concentration-specific limits).
   - **Daily loss limit:** once realized + unrealized P&L for the trading day breaches a defined negative threshold, halt all new order placement and, depending on policy, force-flatten open positions.
   - **Max drawdown from peak equity:** track a running peak-equity high-water mark; if current equity falls a defined percentage below that peak, trigger the same halt/flatten response as the daily loss limit — this catches slower-bleeding losses that a single-day check might not.
3. On any circuit breaker triggering, the response must be deterministic and pre-defined, not decided in the moment: typically (a) immediately reject/cancel any pending new orders, (b) force-flatten existing positions via market or aggressive limit orders (not passive resting orders that may not fill in time), and (c) require explicit human re-enable before the bot resumes placing new orders — do not auto-resume after a cooldown period without human review, since the condition that triggered the breaker may still be present.
4. Reconcile the risk module's view of positions/P&L against the broker's actual account state on a tight polling interval (or via webhook/push updates if the broker supports it) — a risk module relying solely on the bot's internal record of "what I think I've traded" can be wrong if an order's fill status was misread, defeating the entire purpose of an independent safeguard.
5. Make circuit breaker triggers loud: alert via a channel independent of the bot's normal logging (SMS, push notification, a dedicated alert channel) so a human is aware immediately, not only discoverable by checking logs later.
6. Test each circuit breaker's trigger condition and response in a paper/sandbox environment by deliberately engineering the trigger condition (e.g., simulate a large adverse price move) before ever relying on it in live trading — an untested safety mechanism is not a safety mechanism.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Implementing risk limits as conditional logic inside the same function/class that generates trading signals, so a bug in signal logic can bypass the check entirely.
- Auto-resuming trading after a circuit breaker cooldown period without human review — if the underlying condition (bad data, a broker issue, an actual bad market regime) is still present, auto-resume just delays the same breach.
- Relying on the bot's internal position bookkeeping alone for the risk module's P&L calculation, without reconciling against actual broker account state, so a fill-tracking bug can mask a real breach.
- Using passive/resting orders to force-flatten during a breach, which may not fill promptly during exactly the volatile conditions that likely caused the breach.
- Never having actually tested the circuit breaker trigger path end-to-end before going live, so the first real test occurs during an actual loss event.

## Verification

- In a paper/sandbox environment, engineer each defined trigger condition (breach position limit, breach daily loss limit, breach drawdown limit) individually and confirm the bot halts new orders and force-flattens as designed, with the alert firing through the independent channel.
- Confirm the risk module's position/P&L view, when deliberately desynced from a simulated broker-side fill discrepancy, is caught by the reconciliation check rather than silently trusting stale internal state.
- Confirm that after a triggered breach in a test environment, the bot does not resume placing orders without an explicit manual re-enable action.

## Related Skills

- `correlation-aware-exposure-limits`
- `order-placement-idempotency`
- `paper-to-live-promotion-checklist`
- `model-staleness-detection`
