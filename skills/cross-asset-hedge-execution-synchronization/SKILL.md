---
name: cross-asset-hedge-execution-synchronization
description: >-
  Quantitative execution synchronization engine for multi-leg strategies (Options Delta Hedging, Convertible Arbitrage, ETF Basis Trading) to eliminate legging risk and enforce latency bounds.
domain: Execution Algorithms
subdomain: Multi-Leg Execution & Hedging
tags: ["execution-algo", "hedge-synchronization", "legging-risk", "delta-hedging", "multi-leg", "latency-bounds"]
brokers_frameworks: ["Generic FIX / OMS", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in multi-leg trading algorithms (e.g. Options Delta Hedging, Convertible Bond Arbitrage, ETF Basis Trading, Cross-Asset Pairs) where primary leg fills must be immediately hedged in a secondary asset. Failing to synchronize hedge executions introduces severe **Legging Risk**—where market movements during the execution gap create unhedged directional exposure and slippage. This module calculates dynamic hedge ratios, generates hedge orders upon primary fill events, and enforces strict sub-second synchronization latency SLAs.

## Prerequisites

- Primary leg fill notification payload (`symbol`, `fill_qty`, `fill_price`, `fill_timestamp_ms`).
- Strategy parameters: `hedge_symbol`, `hedge_ratio` (Delta / Beta), `max_sync_delay_ms` (e.g. 100 ms).

## Workflow

1. **Fill Event Ingestion**: Receive primary leg execution fill ($Q_{primary}, P_{primary}, t_{fill}$).
2. **Hedge Quantity Calculation**:
   - $\text{Hedge Qty} = -1.0 \times Q_{primary} \times \text{Hedge Ratio}$.
3. **Synchronized Hedge Order Dispatch**:
   - Instantly dispatch market or aggressive limit order to hedge venue at $t_{dispatch}$.
4. **Latency & Synchronization Audit**:
   - Measure synchronization delay $\Delta t = t_{hedge\_fill} - t_{fill}$.
   - If $\Delta t > \text{Max Sync Delay MS}$, flag `SYNCHRONIZATION_DELAY_BREACH` and aggressively reprice hedge order.
5. **Legging Risk Exception Handling**: If hedge leg fails to fill within timeout, trigger primary leg emergency unwind.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Un-Synchronized Legging**: Routing primary and hedge legs independently without cross-leg atomic state tracking.
- **Ignoring Partial Fills**: Waiting for the primary order to fill 100% before routing any hedge leg, leaving large partial fills unhedged for seconds.
- **Static Delta Assumption**: Using static option delta for hedging during fast market moves without updating real-time implied volatility / spot delta.

## Verification

- Instantiate `CrossAssetHedgeSynchronizer`. Register an options delta hedge strategy (`Primary` = `AAPL_250516_C200`, `Hedge` = `AAPL`, `Delta` = 0.50, `Max Delay` = 100 ms). Ingest primary fill of +10 option contracts at $t = 1000\text{ ms}$. Verify synchronizer generates hedge order of -500 shares `AAPL`. Submit hedge fill at $t = 1040\text{ ms}$ ($\Delta t = 40\text{ ms}$) and verify `SYNCHRONIZED_OK` status.
- Run `python scripts/test_cross_asset_hedge_execution_synchronization.py`.

## Related Skills

- `cross-venue-latency-arbitrage-defensive-design`
- `delta-hedging-cadence-gamma-vs-theta`
---
