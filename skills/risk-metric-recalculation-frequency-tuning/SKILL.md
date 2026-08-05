---
name: risk-metric-recalculation-frequency-tuning
description: >-
  Production-grade differential risk metric recalculation scheduler tuning computation cadences across 4 tiers (Per-tick drawdown, 1s Greeks, 30s VaR, 300s stress tests) with dynamic P&L velocity volatility acceleration.
domain: Risk Management & Computational Optimization
subdomain: Risk Engine Performance & Frequency Tuning
tags: ["risk-frequency", "recalculation-tuning", "volatility-acceleration", "pnl-velocity", "cpu-optimization", "tiered-cadence"]
brokers_frameworks: ["Risk Metric Scheduler", "Python Dataclasses", "Unittest"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when optimizing computational efficiency and latency budgets for real-time risk engines. Naively recalculating all risk metrics (VaR, CVaR, option Greeks, portfolio stress tests) on every incoming market data tick consumes excessive CPU resources and introduces execution thread lag. This engine establishes a 4-tier differential calculation schedule (per-tick drawdown, 2s option Greeks, 30s 1-day VaR, 300s stress testing) and dynamically accelerates calculation frequencies when P&L velocity spikes above threshold.

## Prerequisites

- Risk metric schedule configuration (`metric_name`, `tier`, `base_interval_sec`, `accelerated_interval_sec`).
- P&L velocity threshold ($/sec; default $500/sec).

## Workflow

1. **Tiered Metric Registration**:
   - Tier 1: Per-tick (`TICK_DRAWDOWN` - 0.0s interval).
   - Tier 2: Fast (`GREEKS_DELTA` - 2.0s base / 0.5s accelerated).
   - Tier 3: Medium (`VAR_1DAY` - 30.0s base / 5.0s accelerated).
   - Tier 4: Slow (`STRESS_TEST` - 300.0s base / 30.0s accelerated).
2. **P&L Velocity Monitoring**:
   - Compute P&L change rate: $\text{Velocity} = \frac{|\Delta \text{PnL}|}{\Delta t}$.
3. **Dynamic Acceleration**:
   - If velocity $\ge \text{threshold}$, switch scheduler to accelerated intervals.
4. **Tuner Execution Report**: Output structured `TunerExecutionReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Per-Tick Recalculation of Heavy VaR/Monte Carlo**: Running 10,000-scenario Monte Carlo simulations on every tick freezes pre-trade risk threads.
- **Static Calculation Cadences During Crashes**: Keeping VaR updates at 300s intervals during market volatility spikes delays risk detection.
- **Ignoring CPU Cycle Savings**: Failing to measure CPU cycles saved by tiering calculations.

## Verification

- Instantiate `RiskMetricFrequencyTuner`. Evaluate at normal P&L velocity $\implies$ verify only `TICK_DRAWDOWN` due between intervals, saving ~75% CPU cycles. Sudden $2,000/s P&L drop $\implies$ verify `is_accelerated_mode=True` and `GREEKS_DELTA` / `VAR_1DAY` intervals accelerated.
- Run `python scripts/test_risk_frequency_tuner.py`.

## Related Skills

- `risk-control-latency-budget`
- `risk-limit-calibration-against-historical-drawdowns`
---
