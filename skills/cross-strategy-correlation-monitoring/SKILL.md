---
name: cross-strategy-correlation-monitoring
description: Quantitative multi-strategy risk management engine for monitoring rolling
  PnL correlations across strategy pods, detecting diversification breakdown, and
  computing Diversification Ratios.
domain: Multi-Strategy & Portfolio Risk
subdomain: Cross-Strategy Correlation
tags:
- multi-strategy
- cross-strategy
- pnl-correlation
- diversification-ratio
- pod-risk
- correlation-breach
brokers_frameworks:
- NumPy
- Pandas
- SciPy
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in multi-strategy hedge fund platforms or multi-pod quantitative trading architectures to monitor rolling PnL correlations across active strategies (e.g., Statistical Arbitrage, Trend Following, Options Volatility, Alt-Data Equity). Sub-strategy pods that appear independent during normal markets often converge during market stress, exhibiting high PnL correlation ($\rho \ge 0.70$). This module calculates pairwise PnL correlation matrices, computes the Portfolio Diversification Ratio ($DR = \frac{\sum w_i \sigma_i}{\sigma_{\text{portfolio}}}$), and flags strategy redundancies.

## Prerequisites

- Synchronized daily or hourly PnL return series for all active sub-strategies ($S_1, S_2, \dots, S_M$).
- Strategy capital allocation weights ($w_i$).

## Workflow

1. **PnL Return Ingestion**: Ingest PnL return matrix $R_{N, M}$ ($N$ timestamps, $M$ strategies).
2. **Rolling Correlation Matrix Computation**:
   - Compute pairwise Pearson correlation matrix $C_{\text{pnl}}$ over rolling window $W$.
3. **Correlation Pairway Breach Audit**:
   - Identify pairs with $\rho_{i,j} \ge 0.70$ (`HIGH_CORRELATION`).
   - Identify pairs with $\rho_{i,j} \ge 0.85$ (`REDUNDANT_POD`).
4. **Diversification Ratio (DR) Calculation**:
   - $DR = \frac{\sum_{i=1}^M w_i \sigma_i}{\sqrt{w^T \Sigma w}}$.
   - $DR = 1.0 \implies$ Zero diversification benefit.
5. **Capital Re-Allocation Alert**: Recommend downsizing allocated capital for highly correlated pods.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Relying on Static Historical PnL Correlations**: Using 3-year static PnL correlations that hide real-time correlation spikes during market sell-offs.
- **Ignoring Equal-Weighted Capital Fallacies**: Assuming equal capital allocations guarantee zero PnL correlation across pods.
- **Neglecting Un-hedged Factor Contamination**: Failing to strip market beta or factor exposures before computing strategy PnL correlations.

## Verification

- Instantiate `CrossStrategyCorrelationMonitor`. Input 60 days of PnL returns for 3 sub-strategy pods (`StatArb`, `TrendFollow`, `OptionsArb`). Set `StatArb` and `TrendFollow` returns with high correlation ($\rho = 0.82$). Verify monitor flags a `HIGH_CORRELATION` breach and calculates Diversification Ratio.
- Run `python scripts/test_cross_strategy_correlation_monitoring.py`.

## Related Skills

- `capital-reallocation-based-on-live-performance`
- `strategy-correlation-matrix-live-recomputation`
---
