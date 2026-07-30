---
name: execution-algo-parameter-optimization-via-backtest
description: >-
  Quantitative execution research engine for optimizing Almgren-Chriss & VWAP/TWAP algorithm parameters (participation rate ceilings, risk aversion lambda, peg offsets) via historical backtest grid search.
domain: Execution Algorithms
subdomain: Algo Parameter Tuning & Backtesting
tags: ["execution-algo", "parameter-optimization", "almgren-chriss", "implementation-shortfall", "backtesting", "grid-search", "tca"]
brokers_frameworks: ["Almgren-Chriss Framework", "Python Dataclasses", "NumPy / Math"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in quantitative execution research, Transaction Cost Analysis (TCA), and execution algorithm parameter tuning. Institutional execution algorithms (Implementation Shortfall, VWAP, POV) rely on key parameters like participation rate ceilings ($\alpha_{\text{max}}$), risk aversion ($\lambda$), and order aggressiveness. This module runs historical backtest grid search optimizations to identify parameter configurations that minimize expected Implementation Shortfall (IS) and execution variance.

## Prerequisites

- Historical execution trade samples (order size, decision price, benchmark prices, market volume/volatility).
- Parameter search space grid ($\alpha_{\text{max}}$, risk aversion $\lambda$, peg offset ticks).
- Penalty weights for IS volatility and incomplete fill penalties.

## Workflow

1. **Parameter Search Grid Construction**:
   - Construct candidate parameter combinations $\mathbf{\theta}_i = (\alpha_{\text{max}}, \lambda, \text{peg\_offset})$.
2. **Historical Simulation & IS Evaluation**:
   - For each trade sample and parameter candidate $\mathbf{\theta}_i$:
     - Simulate trade execution trajectory.
     - Calculate Implementation Shortfall $\text{IS}_k$ in basis points.
     - Record fill completion percentage.
3. **Objective Utility Score Calculation**:
   - $\text{Score}(\mathbf{\theta}) = \overline{\text{IS}} + \gamma_{\text{penalty}} \cdot \sigma_{\text{IS}} + (1.0 - \text{Fill Rate}) \times 100.0$.
4. **Optimal Configuration Selection**:
   - Select candidate $\mathbf{\theta}^*$ achieving lowest utility score.
5. **Audit Report Generation**: Output structured `AlgoOptimizationAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Overfitting to In-Sample Historical Data**: Tuning parameters to a specific quiet market period, causing severe slippage during unexpected volatility regimes.
- **Ignoring Fill Completion Penalties**: Selecting a low-slippage parameter set that frequently fails to complete large order fills before market close.
- **Unrealistic Market Impact Models**: Assuming zero market impact when increasing participation rates above 20% of ADV.

## Verification

- Instantiate `ExecutionAlgoOptimizerEngine`. Define parameter grid ($\alpha_{\text{max}} \in [0.10, 0.20]$, $\lambda \in [1e-5, 1e-4]$). Supply 50 historical execution trade samples. Run backtest optimization. Verify engine evaluates candidate scores, identifies optimal parameter set ($\mathbf{\theta}^*$), and outputs `AlgoOptimizationAuditReport`.
- Run `python scripts/test_execution_algo_parameter_optimization_via_backtest.py`.

## Related Skills

- `execution-slippage-attribution-timing-vs-sizing`
- `execution-cost-model-recalibration-cadence`
---
