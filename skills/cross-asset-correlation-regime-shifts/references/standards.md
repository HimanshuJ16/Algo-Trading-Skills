# Standards for Cross-Asset Correlation Regime Shifts

Primary sources (all consulted 2026-08-22):

- **Longin & Solnik (2001)**, "Extreme Correlation of International Equity Markets", *Journal of Finance* 56(2): 649-676: https://onlinelibrary.wiley.com/doi/10.1111/0022-1082.00340
- **BIS Quarterly Review (4 Dec 2023)**, Lombardi & Sushko, "The correlation of equity and bond returns": https://www.bis.org/publ/qtrpdf/r_qt2312v.htm — documents the US equity/government-bond return correlation switching sign in mid-2021 and remaining positive since.
- **Herdin et al. (2005)**, "Correlation Matrix Distance, a Meaningful Measure for Evaluation of Non-Stationary MIMO Channels": https://repositum.tuwien.at/handle/20.500.12708/68763
- **RiskMetrics** (J.P. Morgan, 1996) — EWMA decay $\lambda = 0.94$ (daily), half-life ~11 days.

| Metric | Engineering Standard | Source |
|---|---|---|
| Distance metric | $D_F = \|C_1 - C_2\|_F / K$ (per-element RMS). NOT bounded by 1 and scales as $1/K$ with universe size; thresholds are universe-specific. The bounded alternative $1 - \mathrm{tr}(C_1 C_2)/(\|C_1\|_F\|C_2\|_F) \in [0,1]$ exists (Herdin et al. 2005) if cross-universe comparability is needed. | Herdin et al. (2005); matrix-distance survey literature |
| Regime thresholds | 0.30 (shift) / 0.60 (crisis) / 0.65 (avg-corr) are DEFAULTS: calibrate to the empirical rolling-$D_F$ distribution (percentile-based) per universe, window lengths, and K before automated action. No universal threshold exists. | Engineering default |
| Crisis-convergence premise | Correlations increase in bear markets but not bull markets (asymmetric dependence); stock-bond correlation flipped positive in the 2022 inflation regime. | Longin & Solnik (2001); BIS QTR Dec 2023 |
| Window ratio | Baseline ≈ 5× short window (e.g. 100d vs 20d) is a sensible default, not a rule; ensure $W \ge 30$ for correlation noise $\approx 1/\sqrt{W}$ to be tolerable. | Engineering default; sampling theory |
| Dynamic leverage scaling | 0.50 crisis / 0.80 shift multipliers are POLICY defaults: mandate-specific, must be validated against whipsaw costs before automation. | Engineering default |
| Data hygiene | Zero-variance (stale/flat) series and non-finite inputs MUST be rejected, never imputed — silent imputation fabricates correlation values. | Engine requirement |
| Minimum window length | Windows of 1-2 observations are algebraically degenerate (all off-diagonal entries exactly $\pm 1$) and MUST be rejected; the engine's hard floor is 3 rows, with `min_observations` configurable up to the caller's calibrated floor. | Sampling theory; engine requirement |
| Input matrix hygiene | Matrices passed to the distance/average routines MUST be validated as correlation matrices (symmetric, unit diagonal, entries in $[-1, 1]$, to float tolerance). A covariance matrix is otherwise silently accepted and inflates $D_F$ into a false crisis. | Engine requirement |
| Estimation alternative | EWMA correlation (RiskMetrics $\lambda = 0.94$ daily / 0.97 monthly) provides smoother regime tracking than hard two-window splits; use as a cross-check. | RiskMetrics (1996) |
