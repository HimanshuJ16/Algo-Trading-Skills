---
name: execution-algo-behavior-under-halted-instrument
description: >-
  Quantitative execution algorithm engine for orchestrating TWAP/VWAP state machine behavior during instrument trading halts (LULD, news halts), canceling resting child orders, and re-benchmarking upon resumption.
domain: Execution Algorithms
subdomain: Execution Safety & State Machine
tags: ["execution-algo", "trading-halt", "luld", "twap", "vwap", "order-cancellation", "reopening-auction"]
brokers_frameworks: ["CME Globex Halted", "Nasdaq LULD", "Eurex T7 Auction", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in institutional execution algorithms (TWAP, VWAP, POV, Implementation Shortfall), Smart Order Routers, and automated risk engines. When an exchange halts trading in an equity or derivative instrument (e.g. Limit Up-Limit Down LULD volatility halts, news pending halts), execution algorithms MUST transition state (`RUNNING` $\to$ `PAUSED_HALTED`), cancel all resting child limit orders on the book, freeze slice timers, and re-benchmark remaining execution schedules upon reopening.

## Prerequisites

- Parent algo order details (`parent_algo_id`, `symbol`, `algo_type`: `'TWAP'`, `'VWAP'`, `total_qty`, `executed_qty`).
- Active child limit orders (`child_ord_id`, `price`, `qty`).
- Instrument status event (`status`: `'TRADING_CONTINUOUS'`, `'HALTED_LULD'`, `'AUCTION_REOPENING'`).

## Workflow

1. **Trading Halt Detection**:
   - Ingest instrument trading status update.
   - If status starts with `'HALTED'` $\implies$ Trigger `PAUSED_HALTED` state.
2. **Immediate Child Order Cancellation**:
   - Issue cancellation directives for all active resting child limit orders.
   - Freeze TWAP time slice generators and VWAP volume participation trackers.
3. **Post-Resumption Re-Benchmarking**:
   - Upon status transition to `'AUCTION_REOPENING'` or `'TRADING_CONTINUOUS'`:
     - Evaluate remaining horizon $T_{\text{remaining}}$ and remaining quantity $Q_{\text{remaining}}$.
     - Smooth out missed slice quantities over remaining time to avoid market impact spikes.
     - Transition state back to `RUNNING`.
4. **Audit Report Generation**: Output structured `AlgoHaltAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Failing to Cancel Resting Orders During Halts**: Leaving stale child limit orders resting on exchange books during halts, which can get filled at off-market prices during reopening auctions.
- **Flooding Market with Accumulated Backlog Post-Resumption**: Dumping all missed TWAP/VWAP slices immediately upon reopening, causing market impact and triggering secondary LULD halts.
- **Continuing Slicing Timers During Halts**: Decrementing algo time horizons while trading is paused, distorting TWAP/VWAP participation rates.

## Verification

- Instantiate `ExecutionAlgoHaltEngine`. Start TWAP algo (`PARENT_TWAP_01`, 10,000 shares total, 4,000 executed). Ingest `HALTED_LULD` event with 2 active resting child orders. Verify engine transitions algo state to `PAUSED_HALTED`, issues cancellations for both child orders, and halts slicing. Ingest `TRADING_CONTINUOUS` event. Verify engine transitions to `RUNNING` and recalculates remaining schedule (6,000 shares).
- Run `python scripts/test_execution_algo_behavior_under_halted_instrument.py`.

## Related Skills

- `execution-algorithm-kill-switch-integration`
- `smart-order-router-failover-on-venue-outage`
---
