# Deep Workflow Reference — ensemble-signal-combination-without-overfitting

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Validate inputs:**
   - All sub-model streams the same length, non-empty, uniquely named, all values finite.
   - For fitted methods, a realized forward return series of the same length.
   - At least $N + 1$ observations to fit $N$ weights (in practice, far more).

2. **Normalize sub-model signals causally:**
   - Compute $Z_{i,t} = (S_{i,t} - \mu_{i,\le t}) / \sigma_{i,\le t}$ over an expanding or
     trailing window that ends at $t$. Never over the full series.
   - Emit $0.0$ while fewer than `min_periods` observations are available, and when the
     window is constant (zero dispersion carries no information).
   - Clip to $[-3.0, +3.0]$.

3. **Compute the regularized weight vector ($w$):**
   - `EQUAL_WEIGHT`: $w_i = 1/N$.
   - `INVERSE_VARIANCE`: rescale each $Z_i$ onto the target by its least-squares slope
     through the origin, $\beta_i = \sum_t Z_{i,t} y_t / \sum_t Z_{i,t}^2$; then
     $\hat{\sigma}_i^2 = \frac{1}{T}\sum_t (\beta_i Z_{i,t} - y_t)^2$ and
     $w_i \propto 1/\hat{\sigma}_i^2$.
   - `SHRUNK_NNLS`: solve $\min_w \lVert Zw - y \rVert^2 + \rho \lVert w \rVert^2$ subject to
     $w \ge 0$ by an active-set method; $\rho$ is scaled by the mean Gram diagonal so it
     is unit-free. Normalize the solution to sum $1$. If the solution is all zeros, fall
     back to $1/N$ and log it — no sub-model had non-negative explanatory power.
   - Apply $1/N$ shrinkage: $w_{\text{shrunk}} = (1 - \lambda) w_{\text{raw}} + \lambda (1/N)$,
     with $\lambda \in [0, 1]$. A $\lambda$ outside that range extrapolates *away* from
     $1/N$ and can produce negative weights.
   - Normalize the sum of weights to $1.0$.

4. **Enforce the per-model weight cap:**
   - Effective cap is $\max(\text{configured cap}, 1/N)$.
   - Water-fill: pin every breaching model at the cap, rescale the remaining models pro
     rata to absorb the freed budget, repeat. Models already pinned must be excluded
     from subsequent redistribution, or mass ping-pongs between them and never settles.

5. **Aggregate the composite ensemble signal:**
   - $S_{\text{ensemble}, t} = \sum_i w_i Z_{i,t}$, bounded by the clip interval.

6. **Verify out-of-sample stability:**
   - $w_i \ge 0$, $\sum w_i = 1$, $\max_i w_i \le$ effective cap.
   - Compare weight vectors across adjacent walk-forward refits; large flips mean the
     fit is tracking noise.

## Known Failure Modes

- **Full-sample normalization:** Z-scoring against the mean and standard deviation of the
  entire series, so every historical bar encodes the future. The backtest looks excellent
  and the live signal does not reproduce it.
- **Degenerate "inverse variance":** weighting by the variance of the *standardized*
  signal. Standardized signals have unit variance, so every model receives $1/N$ while
  the code appears to be optimizing. Symptom: changing the weighting method changes
  nothing.
- **Unconstrained regression overfitting:** using standard OLS to fit model weights,
  generating extreme negative weights that short historically weak sub-signals.
- **Un-normalized scale distortion:** combining sub-model signals with different native
  scales without $Z$-score normalization.
- **Silently infeasible weight cap:** configuring a cap below $1/N$, which no weight
  vector on the simplex can satisfy; the cap is then either ignored or forces equal
  weights depending on the implementation.
- **Multicollinear sub-models:** near-duplicate signals make the fit rank-deficient, so
  the split of weight between them is arbitrary and flips between refits.
- **NaN propagation:** one non-finite observation in one stream poisons the mean, the
  standard deviation, and every downstream weight without raising.

## Production Implementation Reference

- Reference code: `scripts/ensemble_combiner.py` (`EnsembleSignalCombiner`, `SignalStream`,
  `EnsembleResult`, `EnsembleMethod`, `EnsembleError`).
- Automated unit tests: `scripts/test_ensemble_combiner.py`.
- The NNLS solver is a pure-stdlib active-set implementation; it was cross-checked
  against `scipy.optimize.nnls` on randomized designs (including rank-deficient and
  all-zero columns) and agrees to machine precision. SciPy is not a runtime dependency
  of this skill.
