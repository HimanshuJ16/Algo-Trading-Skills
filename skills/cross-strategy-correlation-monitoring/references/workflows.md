# Workflows for Cross-Strategy Correlation Monitoring

1. **PnL Ingestion**:
   - Collect PnL return vectors $R_1, R_2, \dots, R_M$ on one shared timestamp index,
     oldest row first.
   - Strip market beta / common factor exposure first if the goal is strategy
     redundancy rather than shared factor loading. Align timestamps before, not
     during, estimation.
   - Column order must match the `strategy_names` list passed to the engine.
2. **Validation Gate** (runs before any estimation; each condition raises):
   - Non-finite values present → repair or drop upstream; imputation fabricates structure.
   - Window shorter than `min_observations` (hard floor 3) → widen the window.
   - Zero-variance column (flat / stale / idle pod) → exclude the pod explicitly;
     never substitute $\rho = 0$, which reports a dead feed as a perfect diversifier.
   - Weights: wrong length, non-finite, negative, or summing to zero → reject.
     Valid weights are normalized to sum to 1.

   **Decision point — an idle pod.** A pod that traded nothing over the window is a
   *scope* decision, not a data-cleaning one. Exclude the column and say so in the
   report you circulate. Do not fill it with zeros: a flat series has an undefined
   correlation to everything, and reporting 0.0 both invents a perfect diversifier
   and pulls $\bar{\rho}$ below its breakdown threshold, masking a genuine breach on
   the pods that *are* live.
3. **Windowing**:
   - With `lookback_window = W`, only the trailing $W$ rows are used; `None` leaves
     windowing to the caller. `observations_used` on the report records what was
     actually estimated from.
4. **Weighting Choice**:
   - `ewma_span = None` (default): every observation in the window counts equally —
     the explicit rolling-window view, and the right default for a periodic
     diversification review.
   - `ewma_span = S` ($S \ge 2$): weights $w_t \propto (1-\alpha)^{T-t}$ with
     $\alpha = 2/(S+1)$, normalized to sum to 1 — the right choice for *live*
     recomputation, where the point is to see a convergence within a handful of
     observations rather than after it has averaged out.

   **Decision point — span selection.** `ewma_span` is a statistical-power knob, not
   a smoothing preference. A short span reacts fast but manufactures threshold
   crossings; a long span is stable but reports a benign $\rho$ through the first days
   of a convergence. Choose it against how quickly you are willing to act, then set
   `min_observations` to match. Record $N_{\text{eff}} = 1/\sum_t w_t^2$ — the honest
   sample size, which converges to `ewma_span` regardless of how deep the history is.
5. **Correlation Calculation**:
   - Compute the covariance $S$ over the weighted window. The EWMA form is the
     debiased weighted second moment
     $S = \frac{1}{1 - \sum_t w_t^2}\sum_t w_t (x_t - \mu_w)(x_t - \mu_w)^\top$,
     computed as a single weighted sum of outer products so it is positive
     semi-definite by construction.
   - Standardize into $C = D^{-1/2} S D^{-1/2}$, $D = \operatorname{diag}(S)$; force the
     diagonal to exactly 1.0 and clip to $[-1, 1]$ to absorb floating-point drift.
     **This unshrunk matrix is the alerting surface.**
6. **Breach Audit** (one-sided, inclusive; redundancy takes precedence):
   - $\rho_{i,j} \ge 0.85$ → `REDUNDANT_POD`; else $\rho_{i,j} \ge 0.70$ → `HIGH_CORRELATION`.
   - Compare the unrounded value and round only for reporting — rounding first moves
     the effective threshold to 0.69995.
   - Negative correlations are diversification, not breaches, and are not flagged.
7. **Average-Correlation Breakdown**:
   - Compute $\bar{\rho}$ as the mean of the $M(M-1)/2$ unique off-diagonal entries and
     flag the portfolio when $\bar{\rho} \ge$ `max_avg_correlation_threshold`.
   - **Decision point.** $\bar{\rho}$ is signed, so a $+0.9$ pair and a $-0.9$ pair
     average to zero; always read `high_correlation_breaches` alongside it. A single
     converged pair inside a wide, otherwise-diversified book must still flag, and a
     uniformly elevated book with no individual breach must also flag — which is why
     the health verdict ORs the conditions rather than reading any one of them alone.
8. **Diversification Ratio Computation**:
   - $DR = \frac{\sum w_i \sigma_i}{\sqrt{w^T \Sigma w}}$, computed by one shared code
     path used by both `calculate_diversification_ratio` and
     `analyze_strategy_correlations` so the two can never disagree, and from the same
     weighted moments as the correlation matrix.
   - Zero portfolio variance → $DR = \infty$ with a warning log.
9. **Shrinkage for Downstream Optimizers**:
   - Compute $\hat{\Sigma} = \delta \operatorname{diag}(S) + (1-\delta) S$ and emit it
     alongside $C$. It is positive definite for $\delta > 0$, so a mean-variance
     optimizer can invert it even when the pod count exceeds the observation count and
     $S$ is singular.
   - **Decision point — never threshold $\hat{\Sigma}$.** A diagonal target leaves
     variances untouched and scales every off-diagonal correlation by exactly
     $(1-\delta)$. Comparing a 0.70 threshold against $\hat{\Sigma}$'s implied
     correlations raises the real trigger to $0.70/(1-\delta) = 0.824$ at
     $\delta = 0.15$: a pair genuinely at $\rho = 0.80$ reports 0.68 and never alerts.
     If a consumer needs a single matrix, hand it $C$ for monitoring and
     $\hat{\Sigma}$ for optimization, labelled.
10. **Re-allocation Action**:
    - Emit every pair breach, every $\bar{\rho}$ breach *and*, independently, any
      $DR < $ `min_diversification_ratio`.
    - Confirm persistence across consecutive windows before cutting capital: single-window
      crossings are within sampling noise, and with $M$ pods there are $M(M-1)/2$
      simultaneous comparisons (see `references/standards.md`). Check
      `effective_observations` before treating a single crossing as evidence.
