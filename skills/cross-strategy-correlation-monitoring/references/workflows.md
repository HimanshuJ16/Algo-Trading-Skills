# Workflows for Cross-Strategy Correlation Monitoring

1. **PnL Ingestion**:
   - Collect PnL return vectors $R_1, R_2, \dots, R_M$ on one shared timestamp index.
   - Strip market beta / common factor exposure first if the goal is strategy
     redundancy rather than shared factor loading.
   - Column order must match the `strategy_names` list passed to the engine.
2. **Validation Gate** (runs before any estimation; each condition raises):
   - Non-finite values present → repair or drop upstream; imputation fabricates structure.
   - Window shorter than `min_observations` (hard floor 3) → widen the window.
   - Zero-variance column (flat / stale / idle pod) → exclude the pod explicitly;
     never substitute $\rho = 0$, which reports a dead feed as a perfect diversifier.
   - Weights: wrong length, non-finite, negative, or summing to zero → reject.
     Valid weights are normalized to sum to 1.
3. **Windowing**:
   - With `lookback_window = W`, only the trailing $W$ rows are used; `None` leaves
     windowing to the caller. `observations_used` on the report records what was
     actually estimated from.
4. **Correlation Calculation**:
   - Compute the Pearson correlation matrix $C_{\text{pnl}}$ over the window and
     clip to $[-1, 1]$ against float error.
5. **Breach Audit** (one-sided, inclusive; redundancy takes precedence):
   - $\rho_{i,j} \ge 0.85$ → `REDUNDANT_POD`; else $\rho_{i,j} \ge 0.70$ → `HIGH_CORRELATION`.
   - Negative correlations are diversification, not breaches, and are not flagged.
6. **Diversification Ratio Computation**:
   - $DR = \frac{\sum w_i \sigma_i}{\sqrt{w^T \Sigma w}}$, computed by one shared code
     path used by both `calculate_diversification_ratio` and
     `analyze_strategy_correlations` so the two can never disagree.
   - Zero portfolio variance → $DR = \infty$ with a warning log.
7. **Re-allocation Action**:
   - Emit every pair breach *and*, independently, any $DR < $ `min_diversification_ratio`.
   - Confirm persistence across consecutive windows before cutting capital: single-window
     crossings are within sampling noise (see `references/standards.md`).
