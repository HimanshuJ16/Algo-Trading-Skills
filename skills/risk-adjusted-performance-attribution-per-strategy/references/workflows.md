# Deep Workflow Reference — risk-adjusted-performance-attribution-per-strategy

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

0. **Validate and align the inputs**:
   - Reject non-finite returns. A `NaN` propagated into `statistics.stdev` fails deep
     inside the standard library with an opaque `AttributeError`; resolve missing
     observations upstream instead.
   - Reject any return $\le -1.0$. At exactly $-1.0$ the equity curve reaches zero; below
     it the curve goes negative, at which point the geometric annualization raises a
     negative base to a fractional power and returns a **complex number**, and the max
     drawdown exceeds 100%. Neither is a meaningful performance statistic.
   - Require at least 2 observations (volatility is undefined below that).
   - Require **equal-length** series. The engine has no timestamps, so alignment is
     positional. Truncating to the shortest series would compute per-strategy metrics
     over the full history while computing portfolio metrics over the short one — a
     single report mixing two horizons — and would discard the *most recent*
     observations of the longer series.
   - Validate that weights match the strategy count and sum to $1.0$. Do not rescale
     silently: a mis-scaled weight vector changes both the portfolio return and every
     risk contribution.

1. **Per-Strategy Metric Calculation**:
   - Total return: $\prod_t (1+r_t) - 1$.
   - Annualized return: $\left(\prod_t (1+r_t)\right)^{252/n} - 1$.
   - Annualized volatility: sample standard deviation ($n-1$) $\times \sqrt{252}$.
   - Max drawdown: track running peak equity; the maximum of $(\text{peak}-\text{equity})/\text{peak}$.
   - De-annualize the risk-free rate **geometrically**: $(1+r_f)^{1/252}-1$. Returns
     compound, so the hurdle must too. At $r_f = 5\%$ the arithmetic shortcut $r_f/252$
     gives $0.00019841$ against the correct $0.00019363$ — about 2.5% too high.
   - Downside deviation: $\sqrt{\frac{1}{n}\sum_t \min(r_t - \text{MAR},0)^2}\times\sqrt{252}$,
     averaged over **all** $n$ observations (Kidd 2012).
   - Sharpe, Sortino, Calmar from the above.
   - **Undefined ratios are `None`, with the reason recorded in `undefined_ratios`.**
     Zero volatility leaves Sharpe undefined; no observation below the MAR leaves
     Sortino undefined; no drawdown leaves Calmar undefined. Returning $0.0$ is a
     ranking inversion — $0.0$ is a legitimate mediocre score, so an excellent strategy
     sorts below an average one.

2. **Portfolio-Level Blended Returns**:
   - $r_{p,t} = \sum_i w_i r_{i,t}$, then annualized return, volatility and Sharpe on
     that series.

3. **Euler Risk Decomposition**:
   - Build the sample covariance matrix $\Sigma$ ($n-1$ denominator) of the strategy
     return series.
   - $\sigma_p^2 = w'\Sigma w$.
   - $\mathrm{MCR}_i = (\Sigma w)_i / \sigma_p$; $\mathrm{CR}_i = w_i \mathrm{MCR}_i$;
     report $\mathrm{CR}_i/\sigma_p = w_i(\Sigma w)_i / \sigma_p^2$ as a percentage.
   - Contributions sum to 100% and **may be negative** for a diversifying strategy.
   - If $\sigma_p^2 \approx 0$ (fully offsetting strategies) the decomposition is $0/0$:
     report `None` and set `risk_decomposition_available` to false. Do not fall back to
     the weighted-volatility share, which would claim 50/50 for a zero-risk portfolio.
   - `standalone_volatility_share_pct` retains the correlation-blind
     $w_i\sigma_i/\sum_j w_j\sigma_j$ as a measure of gross standalone scale. It is not
     a risk attribution and must not be presented as one.

4. **Attribution Report Generation**:
   - Emit `PortfolioAttributionReport` with per-strategy metrics ranked as the consumer
     requires, `observations`, `insufficient_history_warning`, and
     `risk_decomposition_available`.
   - Check `insufficient_history_warning` before quoting any annualized figure: below
     `min_recommended_observations` (default 252) the annualized numbers are
     arithmetically correct and statistically thin.

## Interpreting the output

- **A high Sharpe with a small or negative risk contribution** is the best case: the
  strategy earns well and diversifies the book.
- **A high Sharpe with a dominant risk contribution** means the portfolio is that
  strategy. Its standalone quality does not offset the concentration.
- **A negative risk contribution** means the strategy hedges the rest of the book.
  Cutting it because its standalone Sharpe is unimpressive will *raise* portfolio
  volatility. Never rank on the absolute value of the contribution.
- **`None` ratios** need explicit handling wherever strategies are sorted; treating
  `None` as zero reintroduces the ranking inversion the `None` exists to prevent.

## Production Implementation Reference

- Reference code: `scripts/risk_adjusted_attribution.py`
  (`RiskAdjustedPerformanceAttributionEngine`, `StrategyReturns`,
  `RiskAdjustedMetrics`, `PortfolioAttributionReport`).
- Automated unit tests: `scripts/test_risk_adjusted_attribution.py`, including the
  hand-derived $+200\%/-100\%$ Euler decomposition for a partially hedged pair, the
  closed-form volatility and drawdown checks, and the downside-deviation denominator
  convention.
