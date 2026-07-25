---
name: cold-start-handling-for-newly-listed-instruments
description: >-
  Use when deploying ML signal generation models to handle newly-listed instruments (IPOs, new crypto pairs) with zero or minimal trading history, using fallback heuristics and cluster-proxy feature transfer without extrapolating invalid historical statistics.
domain: algorithmic-trading
subdomain: financial-ml
tags: ["financial-ml", "cold-start", "new-instruments", "feature-transfer", "fallback-heuristics", "cluster-proxy"]
brokers_frameworks: ["Cold Start Handler Engine", "Python NumPy"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when deploying ML signal generation to a dynamic asset universe containing newly listed securities (e.g. IPOs, SPACs, new tokens). Standard ML features (200-day moving average, 60-day volatility, historical beta) crash or return NaN on day 1 of listing. This skill implements a cold-start fallback policy: assigning proxy cluster features from sector peers, enforcing reduced position sizing, and transitioning to model-driven signals once minimum history $T_{\text{min}}$ is accumulated.

## Prerequisites

- Instrument listing timestamp $T_{\text{list}}$ and current bar count $N_{\text{bars}}$.
- Sector peer cluster mapping and default cold-start risk scaling factor (e.g. 0.25x size).

## Workflow

1. **Check Instrument History Maturity**:
   - Compute history maturity ratio $M = \frac{N_{\text{bars}}}{N_{\text{required}}}$.
2. **Apply Cold-Start Fallback State**:
   - If $N_{\text{bars}} < N_{\text{min}}$ (e.g. $<30$ bars): Use sector peer cluster average features and apply `COLD_START_SIZE_SCALING` ($25\%$ max allocation).
3. **Transition to Native Model**:
   - If $N_{\text{bars}} \ge N_{\text{required}}$: Fully enable native ML model predictions.
4. **Audit Universe Cold-Start Ratio**: Track percent of active universe currently in cold-start status.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Unbound Feature Extrapolation**: Computing rolling 100-day statistics on a 3-day-old stock, producing extreme NaN or zero-std anomalies.
- **Uncapped Cold-Start Allocation**: Allocating full portfolio weight to an unproven newly listed IPO before price discovery stabilizes.

## Verification

- Submit newly listed instrument ($N_{\text{bars}} = 5$), verify proxy cluster feature substitution and position scaling ($25\%$).
- Run `python scripts/test_cold_start_handler.py` and confirm 100% pass rate.

## Related Skills

- `transfer-learning-across-correlated-instruments`
- `fallback-and-redundancy-architecture`
---
