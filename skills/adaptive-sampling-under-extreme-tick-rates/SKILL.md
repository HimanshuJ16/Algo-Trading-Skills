---
name: adaptive-sampling-under-extreme-tick-rates
description: Use during extreme market volatility or flash-crash volume spikes to
  dynamically sample tick streams (1:N systematic sampling), preserving strategy engine
  throughput and cumulative volume/VWAP tracking while preventing memory buffer overflow.
domain: algorithmic-trading
subdomain: real-time-architecture
tags:
- real-time-architecture
- adaptive-sampling
- flash-crash
- tick-filtering
- volume-preservation
- throughput-protection
brokers_frameworks:
- Adaptive Tick Sampler
- Python Real-Time Engine
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when operating real-time strategy engines during flash-crash events or extreme news volatility (e.g. 100,000+ ticks/second). When tick rates exceed system CPU capacity, queues overflow and drop random ticks across all symbols indiscriminately. An adaptive tick sampler monitors rolling tick frequency $F_t$ per symbol, dynamically engaging systematic sampling ($1:N$ reduction) while aggregating skipped volume to maintain accurate VWAP and total volume metrics.

## Prerequisites

- Target maximum processing capacity per symbol (e.g., $F_{\text{target}} = 5,000$ ticks/sec).
- Cumulative volume and VWAP tracking logic.

## Workflow

1. **Monitor Rolling Tick Frequency**:
   - Compute 1-second rolling tick arrival frequency $F_t$ per symbol.

2. **Determine Sampling Rate**:
   - $F_t \le F_{\text{target}}$: `PASSTHROUGH` mode ($100\%$ ticks emitted).
   - $F_t > F_{\text{target}}$: Set sampling ratio $k = \lceil F_t / F_{\text{target}} \rceil$ (emit 1 out of every $k$ ticks).

3. **Aggregate Skipped Tick Metrics**:
   - Accumulate volume $V_{\text{skipped}}$ and price-volume sum $\sum (P \cdot V)$ of skipped ticks.

4. **Emit Sampled Tick with Aggregated Volume**:
   - When emit tick is dispatched, attach $V_{\text{total}} = V_{\text{current}} + V_{\text{skipped}}$ to preserve cumulative volume and VWAP integrity.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Discarding Volume Data**: Dropping skipped ticks without accumulating their volume, corrupting strategy VWAP indicators.
- **Applying Uniform Sampling Factor Across All Symbols**: Sampling a quiet illiquid stock at the same $1:10$ rate as a hyper-active index component.
- **Oscillating Sampling Modes**: Lacking hysteresis on tick frequency thresholds, causing rapid mode switching between passthrough and sampled modes.

## Verification

- Simulate 20,000 ticks/sec with target capacity 5,000 ticks/sec, verifying $1:4$ sampling reduction.
- Verify cumulative volume of sampled ticks matches exact total volume of raw input ticks.
- Run `python scripts/test_tick_sampler.py` and confirm 100% pass rate.

## Related Skills

- `backpressure-drop-degrade-policy`
- `tick-buffering-burst-handling`
- `adaptive-batch-size-tuning-under-load`
---
