---
name: cross-asset-correlation-regime-shifts
description: Quantitative macro risk engine for monitoring cross-asset correlation
  matrices (Equities, Bonds, Gold, Crypto), calculating Frobenius matrix distance,
  and detecting correlation breakdown regime shifts.
domain: Macro & Risk Management
subdomain: Correlation Regimes
tags:
- cross-asset
- correlation-matrix
- frobenius-norm
- regime-shift
- risk-parity
- diversification-breakdown
brokers_frameworks:
- NumPy
- Pandas
- SciPy
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in multi-asset macro strategies, risk-parity portfolios, or statistical arbitrage systems to detect when cross-asset correlation structures breakdown. In normal market regimes, bonds (`TLT`) hedge stocks (`SPY`) with negative correlation. During high-inflation shocks or liquidity panics, correlations shift dramatically (e.g., Stock-Bond correlation flips positive, or all asset correlations converge to $+1.0$). This module calculates the **Frobenius Matrix Distance** between short-term ($W_{short}$) and long-term ($W_{long}$) correlation matrices to trigger automated portfolio de-leveraging.

## Prerequisites

- Synchronized historical return series across multiple asset classes ($K \ge 3$ assets, e.g. `SPY`, `TLT`, `GLD`, `BTC`).
- Short-term window $W_{short}$ (e.g. 20 days) and baseline window $W_{long}$ (e.g. 100 days).

## Workflow

1. **Correlation Matrix Computation**:
   - Compute Pearson correlation matrix $C_{short}$ over short window $W_{short}$.
   - Compute Pearson correlation matrix $C_{long}$ over baseline window $W_{long}$.
2. **Frobenius Matrix Distance Calculation**:
   - $D_F(C_{short}, C_{long}) = \|C_{short} - C_{long}\|_F = \sqrt{\sum_{i,j} (C_{short, i,j} - C_{long, i,j})^2}$.
3. **Average Cross-Asset Correlation**:
   - $\bar{\rho}_{short} = \frac{1}{K(K-1)} \sum_{i \neq j} C_{short, i,j}$.
4. **Regime Classification**:
   - If $D_F > 0.80$ or $\bar{\rho}_{short} > 0.65 \implies$ `CRISIS_CONVERGENCE` (High Risk).
   - If $0.40 < D_F \le 0.80 \implies$ `CORRELATION_SHIFT` (Moderate Risk).
   - Else $\implies$ `STABLE_NORMAL`.
5. **Risk De-Leveraging Directive**:
   - If `CRISIS_CONVERGENCE`, downsize portfolio target leverage by 50%.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Relying on Static Correlations**: Assuming stock-bond diversification holds continuously, leading to catastrophic drawdowns during 2022-style stagflation shocks.
- **Ignoring Matrix Distance Signatures**: Monitoring only pairwise scalar correlations without evaluating full-matrix structural breakdown via Frobenius distance.
- **Sample Window Misalignment**: Comparing non-overlapping or poorly aligned asset return series across different timezones.

## Verification

- Instantiate `CrossAssetCorrelationRegimeDetector`. Input historical returns where $C_{long}$ has negative Stock-Bond correlation ($\rho = -0.40$). Input short-term returns where Stock-Bond correlation flips to $+0.75$. Verify Frobenius distance $D_F > 0.80$, regime is classified as `CRISIS_CONVERGENCE`, and de-leveraging directive is triggered.
- Run `python scripts/test_cross_asset_correlation_regime_shifts.py`.

## Related Skills

- `correlation-aware-exposure-limits`
- `strategy-correlation-matrix-live-recomputation`
---
