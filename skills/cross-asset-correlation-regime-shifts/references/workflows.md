# Workflows for Cross-Asset Correlation Regime Shifts

1. **Matrix Estimation**:
   - Estimate short-term matrix $C_{short}$ (20 days) and baseline matrix $C_{long}$ (100 days) on synchronized returns; zero-variance series raise data errors.
   - Decision point: set `min_observations` to the calibrated window floor before going live. Windows below 3 rows are rejected outright (correlations are then exactly $\pm 1$ by construction); between 3 and ~30 rows the estimate is dominated by sampling noise, so a gapped feed should raise rather than trigger a de-leverage.
   - Decision point: if windows shorter than ~30 days are too noisy for your universe (sample-correlation std $\approx 1/\sqrt{W}$), prefer EWMA estimation (RiskMetrics decay $\lambda = 0.94$ daily, ~11-day half-life) over a hard two-window split.
2. **Frobenius Distance Calculation** (K-normalized per-element RMS):
   - $D_F = \frac{1}{K}\sqrt{\sum_{i,j} (C_{short, i,j} - C_{long, i,j})^2}$.
   - Sensitivity: one pairwise flip of $\Delta\rho$ moves $D_F$ by $\sqrt{2}|\Delta\rho|/K$.
3. **Average Correlation**:
   - $\bar{\rho}_{short} = \frac{1}{K(K-1)} \sum_{i \neq j} C_{short, i,j}$.
4. **Regime Classification** (inclusive boundaries, tunable thresholds):
   - If $D_F \ge 0.60$ or $\bar{\rho}_{short} \ge 0.65 \implies$ `CRISIS_CONVERGENCE`.
   - If $0.30 \le D_F < 0.60 \implies$ `CORRELATION_SHIFT`.
   - Else $\implies$ `STABLE_NORMAL`.
   - Decision point: calibrate 0.30/0.60 to rolling $D_F$ percentiles (e.g. 80th/95th) on at least several years of history for the same universe and windows; require N consecutive days above threshold before acting (whipsaw guard).
5. **Portfolio Action**:
   - Leverage multipliers: `CRISIS_CONVERGENCE` 0.50, `CORRELATION_SHIFT` 0.80, `STABLE_NORMAL` 1.00 — policy defaults, not mandated constants; confirm with exposure-limit and drawdown controls before executing.
