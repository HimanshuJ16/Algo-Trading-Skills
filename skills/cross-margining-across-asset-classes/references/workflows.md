# Workflows for Cross-Margining Across Asset Classes

1. **Margin Component Ingestion**:
   - Collect standalone initial margin requirements $M_1, M_2, \dots, M_K$, one aggregated figure per asset class.
   - Reject non-finite, negative, or duplicated components. A duplicated `asset_class_id` would be looked up as a self-pair and take the default correlation instead of 1.0, silently mispricing the portfolio.

2. **Offset Registration (contractual, not statistical)**:
   - Register $\rho_{i,j}$ only for pairs covered by an active arrangement, tagged with the program (`CME-OCC`, `CME-FICC/GSD`). These are offset credits established by the clearing arrangement, not correlations estimated from a return series.
   - Any pair left unregistered receives `default_correlation` (1.0 — no offset) and is reported in `unregistered_pairs`. Fail-closed: an unregistered pair is normally a pair with no agreement behind it, and defaulting it to $\rho = 0$ would hand out an unearned $\sqrt{M_1^2 + M_2^2}$ benefit.

3. **Cross-Margin Computation**:
   - $V = \sum_i M_i^2 + 2\sum_{i<j} \rho_{i,j} M_i M_j$ (the ISDA SIMM cross-risk-class aggregation shape).
   - If $V < -\varepsilon$ (with $\varepsilon = 10^{-9} \sum_i M_i^2$), the pairwise offsets are jointly inconsistent — not positive semi-definite for these weights — and the calculation raises `InconsistentCorrelationError`. Clamping to zero here would manufacture a ~100% margin saving. $V = 0$ exactly (the PSD boundary, e.g. three equal legs pairwise at $\rho = -0.5$) is legitimate and accepted.
   - $M_{\text{cross}} = \max\left(f \times M_{\text{standalone}},\ \sqrt{V}\right)$, where $f$ = `minimum_floor_pct` (default 0.20, an internal model-risk guard — see `standards.md`).

4. **Savings & Efficiency Reporting**:
   - $\text{Savings} = M_{\text{standalone}} - M_{\text{cross}}$.
   - $\text{Efficiency Pct} = \frac{\text{Savings}}{M_{\text{standalone}}} \times 100\%$.
   - Because $|\rho_{i,j}| \le 1$, $M_{\text{cross}} \le M_{\text{standalone}}$ always, so savings can never be negative.

5. **Reconciliation Before Collateral Release**:
   - Compare $M_{\text{cross}}$ against the requirement published by the CCP or clearing broker. The two are computed by different methods (SPAN 2 scenario HVaR, STANS Monte Carlo Expected Shortfall) and will differ.
   - Release freed collateral only up to the reconciled figure, and record the applied offsets, program attribution, floor, and unregistered pairs for the treasury audit trail.
