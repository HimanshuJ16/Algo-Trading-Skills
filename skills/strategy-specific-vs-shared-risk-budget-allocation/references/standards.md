# Standards for Strategy-Specific vs Shared Risk Budget Allocation

## Mathematical definitions (sourced)

| Item | Definition | Source |
|---|---|---|
| Portfolio volatility | $\sigma_p(w) = \sqrt{w^{\mathsf T}\Sigma w}$ | Standard; MRT (2009), Sec. 2 |
| Marginal contribution to risk | $\text{MCR}_i = \partial_{w_i}\sigma_p = (\Sigma w)_i / \sigma_p$ | MRT (2009), Sec. 2 |
| Risk contribution | $\text{RC}_i = w_i\,\text{MCR}_i$, with $\sum_i \text{RC}_i = \sigma_p$ by Euler's theorem ($\sigma_p$ is homogeneous of degree 1) | MRT (2009), Sec. 2; Tasche (2008) |
| Euler allocation principle | The Euler contributions are the unique allocation compatible with RORAC-consistent performance measurement | Tasche (2008), Sec. 2–4 |
| Component risk share | $c_i = \text{RC}_i/\sigma_p = w_i(\Sigma w)_i / (w^{\mathsf T}\Sigma w)$, $\sum_i c_i = 1$ | Follows from the two rows above |
| Component VaR | $\text{Component VaR}_i = w_i \times \text{Marginal VaR}_i = c_i \times \text{VaR}_p$; component VaRs sum to portfolio VaR with no residual, because parametric VaR is a fixed multiple of $\sigma_p$ | Jorion (2007), Ch. 7 |
| Parametric (variance–covariance) VaR | $\text{VaR}_{\alpha} = \text{Capital}\times\sigma_p\times\sqrt{H}\times z_{\alpha}$ over $H$ trading days, assuming Gaussian zero-mean returns | Jorion (2007), Ch. 7 |
| 95% one-tailed normal quantile | $z_{0.95} = 1.6448536269514722$ | Standard normal distribution |

## Terminology (important)

**"CVaR" in this domain means Conditional Value-at-Risk**, i.e. Expected Shortfall / average value-at-risk — the expected loss *given* the loss exceeds the VaR quantile. It is a different quantity from Component VaR and is **not** computed by this skill. Reports generated from this engine must say "Component VaR" in full. The engine's own field is named `component_risk_pct` for exactly this reason.

## Properties this engine relies on

| Property | Statement | Consequence |
|---|---|---|
| Euler identity | $\sum_i c_i = 1$ holds *algebraically* once $\sigma_p$ is computed exactly | `is_euler_decomposition_valid` is a **degeneracy detector** (NaN, floored $\sigma_p$), not evidence that $\Sigma$ is sound |
| Standalone volatility is weight-free | $\sigma_i = \sqrt{\Sigma_{ii}}$ has no dependence on $w$ | A standalone breach cannot be cleared by reallocating capital; it requires de-levering the strategy |
| Component share is non-linear in weight | $c_i = w_i(\Sigma w)_i/(w^{\mathsf T}\Sigma w)$ is roughly quadratic in $w_i$ for a dominant strategy, and rescaling one weight renormalizes all others | The `budget / actual` ratio does not bring a strategy to budget; the factor must be solved |
| Component share is not monotone in weight | $\partial c_i/\partial w_i$ can be negative when $\Sigma_{ij} < 0$ for the rest of the book | The solver uses bisection on a sign change, not a monotone search |
| Shares are scale-invariant | $c_i$ depends only on the relative capital split | The solver may scale one strategy's capital without renormalizing the rest |
| Budget feasibility | $\sum_i c_i = 100\%$ always, so budgets summing to $<100\%$ are unsatisfiable | Reported as `budgets_feasible = False` |

## Known limitations of the risk measure (sourced)

| Limitation | Statement | Source |
|---|---|---|
| Square-root-of-time scaling | Scaling VaR by $\sqrt{H}$ "leads to a systematic underestimation of risk, whereby the degree of underestimation worsens with the time horizon, the jump intensity and the confidence level" under jump diffusion | Danielsson & Zigrand (2006) |
| Parametric VaR tail behaviour | Gaussian, zero-mean parametric VaR understates fat-tailed and skewed strategy return distributions, and says nothing about loss magnitude beyond the quantile | Jorion (2007), Ch. 5 and 7 |
| Volatility ≠ tail risk | Equal volatility contribution is not equal tail contribution; short-gamma and carry strategies contribute little volatility until they contribute the entire loss | See `tail-correlation-between-strategies-under-stress` |

## Engineering conventions (NOT external standards)

The defaults below are operating choices for this implementation. **No regulator, exchange, or published standard mandates them.** Calibrate them to the book; do not cite them as requirements.

| Control | Default | Rationale |
|---|---|---|
| Confidence Z-score | $1.645$ | Conventional rounding of $z_{0.95} = 1.644854$; the $0.009\%$ difference is immaterial next to the normality assumption. Configurable via `confidence_level_z`. |
| Trading days per year | $252$ | US equity convention. NSE is ~250 and crypto trades 365; set `trading_days_per_year` to match how $\Sigma$ was estimated. It is a convention, not a standard. |
| VaR horizon | $252$ days (one year) | Matches the annualized volatility reported alongside it, and preserves this skill's historical output. It is the most aggressive use of the $\sqrt{T}$ rule — set `var_horizon_days=1` for any figure compared against a daily monitoring limit. |
| Euler identity tolerance | $\lvert\sum_i c_i - 1\rvert < 10^{-4}$ | Detects numerical degeneracy only (see the Properties table). |
| Covariance symmetry tolerance | $10^{-9}$, relative to $\max\lvert\Sigma_{ij}\rvert$ | Absorbs estimator round-off without accepting a genuinely asymmetric matrix. |
| Positive-definiteness gate | $\lambda_{\min} > 10^{-12}\,\lambda_{\max}$ | A reciprocal condition-number floor. A strict $\lambda_{\min} > 0$ test does not detect singularity in floating point: a perfectly correlated pair leaves $\lambda_{\min} \approx 10^{-20}$, not exactly $0$. |
| Variance flooring | **Not applied** — violations raise | `max(w'Σw, 1e-8)` turns an impossible correlation into a plausible volatility and a passing audit, and the Euler identity still sums to 100%, so nothing downstream catches it. |
| Limit comparison | Strict `>` | A strategy sitting exactly on its limit is compliant. |
| Adjustment-factor solver | Bisection, tolerance $10^{-12}$, 200 iterations, minimum scale $10^{-9}$ | Bisection needs only a sign change, so it is safe where the share is non-monotone in the weight. The conservative lower bracket is returned, so the resulting share is at or under budget. |
| Reported factor rounding | Floored, not rounded (6 dp internal, 4 dp public) | Ordinary rounding can move the factor *above* the solved root — $0.17142857 \to 0.171429$ — leaving a residual breach after the recommendation is applied. |
| Infeasible-by-scaling result | Factor $0.0$, `shared_budget_infeasible_by_scaling = True` | A one-strategy book holds 100% of its own risk at every capital level; no scaling satisfies a sub-100% budget. Returning a factor that does nothing would be worse than saying so. |

## References

1. Tasche, D. (2008), *Capital Allocation to Business Units and Sub-Portfolios: the Euler Principle*, arXiv:[0708.2542](https://arxiv.org/abs/0708.2542).
2. Maillard, S., Roncalli, T. and Teïletche, J. (2009), *On the properties of equally-weighted risk contributions portfolios*. Published as "The Properties of Equally Weighted Risk Contribution Portfolios", **Journal of Portfolio Management** 36(4), 2010, pp. 60–70. DOI [10.3905/jpm.2010.36.4.060](https://doi.org/10.3905/jpm.2010.36.4.060). Working paper: <http://www.thierry-roncalli.com/download/erc.pdf>.
3. Danielsson, J. and Zigrand, J.-P. (2006), "On time-scaling of risk and the square-root-of-time rule", **Journal of Banking & Finance** 30(10), pp. 2701–2713. DOI [10.1016/j.jbankfin.2005.10.002](https://doi.org/10.1016/j.jbankfin.2005.10.002).
4. Jorion, P. (2007), *Value at Risk: The New Benchmark for Managing Financial Risk*, 3rd ed., McGraw-Hill — Ch. 7 for marginal, incremental and component VaR; Ch. 5 for the limitations of the parametric approach.
