---
name: cross-asset-correlation-regime-shifts
description: >-
  Use when a multi-asset or risk-parity book depends on bonds hedging equities and you
  need to detect the regime shift where that relationship breaks, using a normalised
  matrix distance between correlation snapshots.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: multi-asset-derivatives
  tags: cross-asset, correlation-matrix, frobenius-norm, regime-shift, risk-parity, diversification-breakdown
  brokers_frameworks: NumPy
  version: "1.2.0"
  author: algo-trading-skills-contributors
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
- A minimum observation count per window. The engine's hard floor is 3 rows (below that every off-diagonal correlation is algebraically $\pm 1$ regardless of the data); set `min_observations` to your own calibrated floor — e.g. 30, where sample-correlation noise $pprox 1/\sqrt{W} pprox 0.18$ — so a truncated or gapped feed raises instead of emitting a de-leverage directive.
- Column order must be identical across both windows. The engine can verify that $K$ matches, not that column 0 is the same instrument in both — align the universe upstream.

## Workflow

1. **Correlation Matrix Computation**:
   - Reject the window before estimating if it is shorter than `min_observations`, contains non-finite values, or holds a zero-variance (stale/flat) column — all three fabricate correlation structure rather than measuring it.
   - Compute Pearson correlation matrix $C_{short}$ over short window $W_{short}$.
   - Compute Pearson correlation matrix $C_{long}$ over baseline window $W_{long}$.
2. **Frobenius Matrix Distance Calculation** (K-normalized, per-element RMS):
   - $D_F(C_{short}, C_{long}) = \frac{1}{K}\sqrt{\sum_{i,j} (C_{short, i,j} - C_{long, i,j})^2}$.
   - Note: a single pairwise flip of $\Delta\rho$ moves $D_F$ by $\sqrt{2}|\Delta\rho|/K$ — the metric shrinks with universe size, so thresholds calibrated for one $K$ do not transfer to another.
3. **Average Cross-Asset Correlation**:
   - $\bar{\rho}_{short} = \frac{1}{K(K-1)} \sum_{i \neq j} C_{short, i,j}$.
4. **Regime Classification** (boundaries inclusive; thresholds are tunable defaults):
   - Decision point: if $D_F$ is large, check *why* before de-leveraging — a full-matrix convergence and a single mis-scaled input (e.g. a covariance matrix supplied where a correlation matrix was expected, which the engine now rejects) produce the same headline number.
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
- **Degenerate Short Windows**: a data gap that leaves 1-2 observations in the short window yields off-diagonal correlations of exactly $\pm 1$ — a pure algebraic artefact that reads as `CRISIS_CONVERGENCE` and halves leverage. The engine rejects such windows; raise `min_observations` to your calibrated floor rather than relying on the hard floor of 3.
- **Passing a Covariance Matrix**: covariance and correlation matrices are both square, symmetric and finite, but covariance entries are on the variance scale, inflating $D_F$ by orders of magnitude into a false crisis. The engine validates unit diagonal, symmetry and $[-1, 1]$ range before computing distances.
- **Whipsaw on Boundary Values**: classification boundaries are inclusive — a $D_F$ oscillating around 0.60 flaps between regimes; require confirmation (e.g. N consecutive days) before de-leveraging.

## Verification

- Construct a 3-asset baseline from orthogonal sign vectors (exact identity correlation) and a short window of lockstep rows (exact ones matrix): verify $D_F = \sqrt{6}/3 \approx 0.8165 \ge 0.60$, $\bar{\rho}_{short} = 1.0$, regime `CRISIS_CONVERGENCE`, multiplier 0.50.
- Construct a short window with exact pairwise correlations (0.5, 0.5, 0.25) against the identity baseline: verify $D_F = 1/(2\sqrt{2}) \approx 0.3536$, $\bar{\rho}_{short} = 1.25/3 \approx 0.4167 < 0.65$, regime `CORRELATION_SHIFT`, multiplier 0.80.
- A stock-bond flip from $-0.40$ to $+0.75$ alone (other pairs stable, K=4) gives $D_F = \sqrt{2} \times 1.15 / 4 \approx 0.4066$ → `CORRELATION_SHIFT`, **not** crisis convergence — full-matrix confirmation is required before de-leveraging.
- Feed a 2-row short window: the engine must raise, **not** return `CRISIS_CONVERGENCE`.
- Run `python -m unittest discover -s skills/cross-asset-correlation-regime-shifts/scripts`.

## Related Skills

- `correlation-aware-exposure-limits`
- `cross-strategy-correlation-monitoring`
