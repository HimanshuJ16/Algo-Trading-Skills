# Workflows for Strategy Correlation Matrix Live Recomputation

## 1. Return Series Collection

Collect synchronized live strategy return streams into a DataFrame, one numeric column per
strategy, one row per timestamp, oldest first. Strip market beta and shared factor exposure
upstream — otherwise the monitor mostly rediscovers common beta rather than strategy
convergence. Align timestamps before, not during, estimation.

## 2. Validation Gate

Reject the panel before estimating anything if it has fewer than 2 strategies, fewer than
`min_observations` rows, duplicate column names, non-numeric columns, non-finite values, or a
zero-variance column. All six raise; none is imputed.

**Decision point — an idle strategy.** A strategy that traded nothing over the window is a
*scope* decision, not a data-cleaning one. Exclude the column from the panel and say so in the
report you circulate. Do not fill it with zeros: a flat series has an undefined correlation to
everything, and reporting 0.0 both invents a perfect diversifier and pulls $\bar{\rho}$ below
its breakdown threshold, masking a genuine breach on the strategies that *are* live.

## 3. EWMA Covariance Estimation

Compute normalized weights $w_t \propto (1-\alpha)^{T-t}$ with $\alpha = 2/(\text{span}+1)$,
then the debiased weighted covariance at the latest observation:

$$S = \frac{1}{1 - \sum_t w_t^2}\sum_t w_t\,(x_t - \mu_w)(x_t - \mu_w)^\top,
\qquad \mu_w = \sum_t w_t x_t.$$

Computed as a single weighted sum of outer products, $S$ is positive semi-definite by
construction. Record $N_{\text{eff}} = 1/\sum_t w_t^2$ — the honest sample size, which
converges to `ewma_span` regardless of how deep the history is.

**Decision point — span selection.** `ewma_span` is a statistical-power knob, not a smoothing
preference. A short span reacts to a regime change within a handful of observations but
manufactures threshold crossings; a long span is stable but will report a benign $\rho$
through the first days of a convergence. Choose it against how quickly you are willing to act,
then set `min_observations` to match.

## 4. Correlation Matrix Extraction

Standardize into $R = D^{-1/2} S D^{-1/2}$, $D = \operatorname{diag}(S)$; force the diagonal to
exactly 1.0 and clip to $[-1, 1]$ to absorb floating-point drift. **This unshrunk matrix is
the alerting surface** — it is the estimate of how correlated the strategies actually are.

## 5. Shrinkage for Downstream Optimizers

Compute $\hat{\Sigma} = \delta \operatorname{diag}(S) + (1-\delta) S$ and emit it alongside
$R$. It is positive definite for $\delta > 0$, so a mean-variance optimizer can invert it even
when the strategy count exceeds the observation count and $S$ is singular.

**Decision point — never threshold $\hat{\Sigma}$.** A diagonal target leaves variances
untouched and scales every off-diagonal correlation by exactly $(1-\delta)$. Comparing a 0.70
threshold against $\hat{\Sigma}$'s implied correlations raises the real trigger to
$0.70/(1-\delta) = 0.824$ at $\delta = 0.15$: a pair genuinely at $\rho = 0.80$ reports 0.68
and never alerts. If a consumer needs a single matrix, hand it $R$ for monitoring and
$\hat{\Sigma}$ for optimization, labelled.

## 6. Pairwise Breakdown Alerting

For each of the $N(N-1)/2$ unique pairs, alert when $\rho_{i,j} \ge$
`high_correlation_threshold`. Compare the unrounded value and round only for reporting —
rounding first moves the effective threshold to 0.69995. The test is one-sided: negative
$\rho$ is a diversification benefit, never an alert.

## 7. Portfolio Breakdown Alerting

Compute $\bar{\rho}$ as the mean of the unique off-diagonal entries and flag the portfolio when
$\bar{\rho} \ge$ `max_avg_correlation_threshold`. `is_portfolio_diversification_compromised`
ORs that with the presence of any pair alert, so neither condition can mask the other: a single
converged pair inside a wide, otherwise-diversified book still flags, and a uniformly elevated
book with no individual breach also flags.

**Decision point — acting on an alert.** $\bar{\rho}$ is signed, so a $+0.9$ pair and a $-0.9$
pair average to zero; always read `high_correlation_pairs` alongside it. With $N$ strategies
there are $N(N-1)/2$ simultaneous comparisons, so isolated crossings are expected even under
independence. Require persistence across consecutive recomputations before cutting capital,
and check `effective_observations` before treating a single crossing as evidence.
