---
name: cross-asset-correlation-regime-shifts
description: Quantitative macro risk engine for monitoring cross-asset correlation
  matrices (Equities, Bonds, Gold, Crypto), calculating K-normalized Frobenius matrix
  distance, and detecting correlation breakdown regime shifts.
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
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in multi-asset macro strategies, risk-parity portfolios, or statistical arbitrage systems to detect when cross-asset correlation structures break down. In normal market regimes, bonds (`TLT`) hedge stocks (`SPY`) with negative correlation; correlation asymmetry is empirically documented — correlations increase in bear markets but not bull markets (Longin & Solnik, 2001), and the stock-bond correlation flipped positive in the 2022 inflation regime (BIS Quarterly Review, Dec 2023). This module computes the **K-normalized Frobenius distance** between short-term ($W_{short}$) and baseline ($W_{long}$) correlation matrices and issues leverage directives.

## When NOT to Use

- **As a stand-alone risk control.** The regime label is an input to a risk process, not a risk limit by itself; pair it with exposure/VaR/tail-risk controls (see `correlation-aware-exposure-limits`).
- **Without threshold calibration.** The default thresholds (0.30 / 0.60 / 0.65) are uncalibrated defaults, not validated constants — calibrate against the empirical distribution of rolling $D_F$ on your universe before automating de-leveraging.
- **With unsynchronized series.** Mixed timezones, missing dates, or misaligned calendars produce spurious regime flips (see pitfalls).
- **As a tail-dependence model.** Pearson windows underestimate extreme co-movement; use EVT/copula methods for tail risk.

## Prerequisites

- Synchronized historical return series across multiple asset classes ($K \ge 2$ assets, e.g. `SPY`, `TLT`, `GLD`, `BTC`), with no constant/stale series (zero-variance columns are rejected as data errors).
- Short-term window $W_{short}$ (e.g. 20 days) and baseline window $W_{long}$ (e.g. 100 days; ~5× the short window is a reasonable default, not a rule).

## Workflow

1. **Correlation Matrix Computation**:
   - Compute Pearson correlation matrix $C_{short}$ over short window $W_{short}$.
   - Compute Pearson correlation matrix $C_{long}$ over baseline window $W_{long}$.
2. **Frobenius Matrix Distance Calculation** (K-normalized, per-element RMS):
   - $D_F(C_{short}, C_{long}) = \frac{1}{K}\sqrt{\sum_{i,j} (C_{short, i,j} - C_{long, i,j})^2}$.
   - Note: a single pairwise flip of $\Delta\rho$ moves $D_F$ by $\sqrt{2}|\Delta\rho|/K$ — the metric shrinks with universe size, so thresholds calibrated for one $K$ do not transfer to another.
3. **Average Cross-Asset Correlation**:
   - $\bar{\rho}_{short} = \frac{1}{K(K-1)} \sum_{i \neq j} C_{short, i,j}$.
4. **Regime Classification** (boundaries inclusive; thresholds are tunable defaults):
   - If $D_F \ge 0.60$ or $\bar{\rho}_{short} \ge 0.65 \implies$ `CRISIS_CONVERGENCE` (High Risk).
   - If $0.30 \le D_F < 0.60 \implies$ `CORRELATION_SHIFT` (Moderate Risk).
   - Else $\implies$ `STABLE_NORMAL`.
5. **Risk De-Leveraging Directive**:
   - `CRISIS_CONVERGENCE` → leverage multiplier 0.50; `CORRELATION_SHIFT` → 0.80; `STABLE_NORMAL` → 1.00. Multipliers are policy defaults — size them to your mandate before acting.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Relying on Static Correlations**: Assuming stock-bond diversification holds continuously, leading to catastrophic drawdowns during 2022-style stagflation shocks (both asset classes fell together — the first such year since 1977).
- **Threshold Transfer Without Calibration**: reusing 0.30/0.60 across universes or window lengths — $D_F$ scales as $1/K$ and with window noise ($\sigma_{\hat\rho} \approx 1/\sqrt{W}$); recalibrate per configuration.
- **Imputing Stale Series**: a flat feed yields undefined correlations; this engine raises rather than silently substituting 0.0 — never bypass that by pre-filling zeros.
- **Ignoring Matrix Distance Signatures**: Monitoring only pairwise scalar correlations without evaluating full-matrix structural breakdown via Frobenius distance.
- **Sample Window Misalignment**: Comparing non-overlapping or poorly aligned asset return series across different timezones.
- **Whipsaw on Boundary Values**: classification boundaries are inclusive — a $D_F$ oscillating around 0.60 flaps between regimes; require confirmation (e.g. N consecutive days) before de-leveraging.

## Verification

- Construct a 3-asset baseline from orthogonal sign vectors (exact identity correlation) and a short window of lockstep rows (exact ones matrix): verify $D_F = \sqrt{6}/3 \approx 0.8165 \ge 0.60$, $\bar{\rho}_{short} = 1.0$, regime `CRISIS_CONVERGENCE`, multiplier 0.50.
- Construct a short window with exact pairwise correlations (0.5, 0.5, 0.25) against the identity baseline: verify $D_F = 1/(2\sqrt{2}) \approx 0.3536$, $\bar{\rho}_{short} = 1.25/3 \approx 0.4167 < 0.65$, regime `CORRELATION_SHIFT`, multiplier 0.80.
- A stock-bond flip from $-0.40$ to $+0.75$ alone (other pairs stable, K=4) gives $D_F = \sqrt{2} \times 1.15 / 4 \approx 0.4066$ → `CORRELATION_SHIFT`, **not** crisis convergence — full-matrix confirmation is required before de-leveraging.
- Run `python -m unittest discover -s skills/cross-asset-correlation-regime-shifts/scripts`.

## Related Skills

- `correlation-aware-exposure-limits`
- `strategy-correlation-matrix-live-recomputation`
