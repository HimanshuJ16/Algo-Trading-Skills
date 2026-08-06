---
name: strategy-latency-budget-decomposition
description: >-
  Production-grade tick-to-trade strategy latency budget decomposition engine profiling microsecond execution pipeline stages (ingress network, market data decoding, signal computation, pre-trade risk, order encoding), comparing against SLAs, and isolating bottlenecks.
domain: Market Microstructure & High-Frequency Trading
subdomain: Latency Profiling & Budget Accounting
tags: ["strategy-latency", "latency-budget", "tick-to-trade", "microsecond-latency", "pipeline-bottleneck", "sla-breach"]
brokers_frameworks: ["High-Frequency Trading Microstructure", "Microsecond Profiling Frameworks", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when engineering or optimizing low-latency trading strategies and execution engines. In high-frequency trading (HFT) and ultra-low-latency market making, tick-to-trade execution time must fit within a tight microsecond budget (e.g., total budget $\le 25 \mu\text{s}$). Sub-optimal code paths, dynamic memory allocations, or inefficient risk checks cause latency spikes and adverse selection. This engine decomposes tick-to-trade latency into 5 pipeline stages, audits microsecond measurements against stage SLAs, and identifies primary bottlenecks.

## Prerequisites

- Measured stage latencies in microseconds (`stage_latencies`: `INGRESS_NETWORK`, `MARKET_DATA_DECODE`, `SIGNAL_COMPUTATION`, `PRE_TRADE_RISK`, `EGRESS_ORDER_ENCODE`).
- Configured microsecond SLA budgets (`stage_sla_budgets`).

## Workflow

1. **Pipeline Stage Measurement Collection**:
   - Record microsecond timestamps at each hot-path stage boundary.
2. **SLA Budget Auditing**:
   - Compare stage latency measurements ($\mu\text{s}$) against maximum SLA budgets (Default: Ingress $2\mu\text{s}$, Decode $3\mu\text{s}$, Signal $10\mu\text{s}$, Risk $5\mu\text{s}$, Egress $5\mu\text{s}$).
3. **Bottleneck & Overage Isolation**:
   - Identify stages exceeding SLAs and isolate primary bottleneck stage with largest overage.
4. **Jitter Calculation**:
   - Calculate P99 jitter estimate across pipeline stages.
5. **Execution Output**: Output structured `LatencyDecompositionReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Measuring Only End-to-End Latency**: Tracking overall execution time without stage decomposition, making it impossible to pinpoint whether delays stem from decoding, risk, or network egress.
- **Ignoring Tail Jitter ($P_{99}$ Latency)**: Focusing solely on mean latency while ignoring microsecond $P_{99}$ jitter spikes caused by garbage collection or CPU context switching.
- **Unoptimized Risk Check Overhead**: Allowing pre-trade risk checks to consume $> 50\%$ of the total tick-to-trade budget instead of using vectorized in-memory lookups.

## Verification

- Instantiate `StrategyLatencyBudgetDecompositionEngine`. Profile compliant trade ($18\mu\text{s}$ total vs $25\mu\text{s}$ budget) $\implies$ verify `is_within_budget=True`, zero breached stages, and primary bottleneck identified. Profile breached trade ($25\mu\text{s}$ signal computation) $\implies$ verify `is_within_budget=False`, breached stage `SIGNAL_COMPUTATION`, and audit alert logged.
- Run `python scripts/test_strategy_latency_budget_decomposition.py`.

## Related Skills

- `tick-to-trade-latency-measurement`
- `colocation-latency-budget-accounting`
---
