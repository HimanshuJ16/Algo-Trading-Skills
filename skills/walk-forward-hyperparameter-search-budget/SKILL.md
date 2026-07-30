---
name: walk-forward-hyperparameter-search-budget
description: Use when conducting walk-forward strategy optimization to compute, bound,
  and enforce hyperparameter search budgets ($N_{\text{evals}}$), preventing indirect
  overfitting from excessive trial iterations across out-of-sample slices.
domain: algorithmic-trading
subdomain: backtesting-methodology
tags:
- backtesting-methodology
- hyperparameter-budget
- walk-forward
- overfitting-prevention
- pbo
- search-space-bounding
brokers_frameworks:
- Hyperparameter Search Budgeter
- Python
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill during walk-forward validation setup. Running unconstrained hyperparameter searches (e.g. 50,000 parameter combinations per in-sample window) guarantees finding parameters that perform artificially well in-sample by pure chance, leading to severe out-of-sample degradation. Bounding the search budget $N_{\text{evals}} \le N_{\text{max}}$ enforces statistical significance and limits Probability of Backtest Overfitting (PBO).

## Prerequisites

- Parameter space dimensions $D$ (number of tunable parameters) and grid sizes $K_i$.
- Length of in-sample training window $T_{\text{in}}$ (trading days).

## Workflow

1. **Calculate Raw Parameter Combination Space**:
   $$N_{\text{raw}} = \prod_{i=1}^D K_i$$

2. **Compute Max Recommended Search Budget**:
   $$N_{\text{max}} = \min\left(100, \left\lfloor \frac{T_{\text{in}}}{25} \right\rfloor \times 10\right)$$

3. **Prune Search Space or Sample Grid**:
   If $N_{\text{raw}} > N_{\text{max}}$, apply quasi-Monte Carlo Sobol sampling or grid pruning to enforce $N_{\text{evals}} \le N_{\text{max}}$.

4. **Audit Walk-Forward Optimization Runs**:
   Track cumulative evaluations across all walk-forward windows and flag budget overruns.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Searching 10,000 Combinations on 1-Year Data**: Over-optimizing 250 bars of data with thousands of parameter trials.
- **Ignoring Cumulative Search Budget**: Counting trials per window separately without auditing the total cumulative trials across all 10 walk-forward windows.

## Verification

- Submit parameter grid with $N_{\text{raw}} = 500$ on 1-year in-sample window ($T_{\text{in}} = 250$), verify budget restriction ($N_{\text{max}} = 100$) and sampling.
- Run `python scripts/test_search_budgeter.py` and confirm 100% pass rate.

## Related Skills

- `walk-forward-validation-setup`
- `backtest-parameter-sensitivity-analysis`
- `monte-carlo-strategy-robustness-testing`
---
