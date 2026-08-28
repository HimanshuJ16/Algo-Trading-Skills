# Standards for Risk Parity Allocation Across Strategies

## Mathematical definitions (sourced)

| Item | Definition | Source |
|---|---|---|
| Marginal contribution to risk | $\text{MCR}_i = \partial_{w_i}\sigma(w) = (\Sigma w)_i / \sigma(w)$ | Maillard, Roncalli & Teïletche (2009), Sec. 2 |
| Risk contribution | $\text{RC}_i = w_i \cdot \text{MCR}_i$, with $\sum_i \text{RC}_i = \sigma(w)$ by Euler's theorem ($\sigma$ is homogeneous of degree 1) | ibid., Sec. 2 |
| ERC portfolio | $w$ such that $\text{RC}_i = \text{RC}_j$ for all $i, j$ | ibid., Sec. 2 |
| Risk budgeting portfolio | $\text{RC}_i(w) = b_i\,\mathcal{R}(w)$, $b_i > 0$, $w_i > 0$, $\sum b_i = \sum w_i = 1$ | Griveau-Billion, Richard & Roncalli (2013), Sec. 1 |
| CCD update | $w_i^\star = \dfrac{-\beta_i + \sqrt{\beta_i^2 + 4\sigma_i^2 b_i \sigma(w)}}{2\sigma_i^2}$, $\beta_i = \sum_{j\neq i}\Sigma_{ij}w_j$ | ibid., Eq. 4 |

## Properties this engine relies on (sourced)

| Property | Statement | Source |
|---|---|---|
| Constant-correlation closed form | If $\rho_{ij} = \rho$ for all $i \neq j$, the ERC weights are exactly $w_i = \sigma_i^{-1} / \sum_j \sigma_j^{-1}$ | MRT (2009), Eq. 3 (valid for $\rho \geq -\tfrac{1}{n-1}$) |
| Bivariate case | For $n = 2$ the ERC solution is inverse-volatility and does not depend on $\rho$ | ibid., Sec. 3.1 |
| No general closed form | "In other cases, it is not possible to find explicit solutions of the ERC portfolio" — a numerical solver is required | ibid., Sec. 3.2–3.3 |
| Volatility ordering | $\sigma_{\text{mv}} \leq \sigma_{\text{erc}} \leq \sigma_{1/n}$ | ibid., Sec. 3.4 and Appendix A.3 |
| Normalization | Solving the log-barrier program in $y$ and setting $w^\star_i = y^\star_i / \sum_j y^\star_j$ yields the ERC portfolio | ibid., Sec. 3.3 and Appendix A.2 |
| Solver convergence | CCD on the quadratic-plus-log-barrier objective converges via Tseng (2001) Thm. 5.1; requires strictly positive risk budgets | Griveau-Billion et al. (2013), Remarks 2 and 3 |
| ERC optimality | The ERC portfolio is the maximum-Sharpe portfolio only under a constant correlation matrix **and** equal individual Sharpe ratios | MRT (2009), Sec. 3.5 |

## Engineering conventions (NOT external standards)

The thresholds below are operating defaults chosen for this implementation. **No regulator, exchange, or published standard mandates them.** Calibrate them to the book; do not cite them as requirements.

| Control | Default | Rationale |
|---|---|---|
| Volatility input domain | Finite and strictly positive; violations raise | A zero or negative standard deviation is not a risk estimate. Clamping a zero to a small floor awards that strategy the largest weight in the book. |
| Covariance matrix admissibility | Square, symmetric, finite, positive definite (Cholesky pivot $> 10^{-12}$ relative to the diagonal); violations raise | CCD convergence requires strict convexity. A relative pivot test is necessary: a perfectly correlated pair leaves a pivot near $10^{-18}$, not exactly $0$. |
| $\Sigma$ vs declared volatility | Agreement within 1% relative; violations raise | Otherwise the weights derive from one risk estimate and the risk decomposition from another. |
| Absolute risk-contribution error | $\leq 5.0$ percentage points | Legacy default, retained for compatibility. Loses resolution as $N$ grows: at $N=20$ the equal share is 5.00pp, so a zero-risk strategy passes. Never use it alone. |
| Relative risk-contribution error | $\leq 5.0\%$ of the target share | Scale-free, so it behaves identically for 3 strategies and for 300. A converged ERC solution lands well below $10^{-6}\%$. |
| Solver tolerance / sweep budget | $10^{-10}$ max weight change; 1000 sweeps | Observed convergence is 20–30 sweeps for $n \leq 200$; the budget is a runaway guard, and exhausting it raises rather than returning a partial solution. |
| Rebalancing trigger | Caller's decision | Deliberately **not** specified here. The often-quoted "rebalance on a 20% volatility drift" has no authoritative source and ignores transaction costs; see `rebalancing-frequency-optimization-cost-vs-drift`. |

## References

1. Maillard, S., Roncalli, T. and Teïletche, J. (2009), *On the properties of equally-weighted risk contributions portfolios*. Published as "The Properties of Equally Weighted Risk Contribution Portfolios", **Journal of Portfolio Management** 36(4), 2010, pp. 60–70. DOI [10.3905/jpm.2010.36.4.060](https://doi.org/10.3905/jpm.2010.36.4.060). Working paper: <http://www.thierry-roncalli.com/download/erc.pdf>, SSRN [1271972](https://ssrn.com/abstract=1271972).
2. Griveau-Billion, T., Richard, J.-C. and Roncalli, T. (2013), *A Fast Algorithm for Computing High-dimensional Risk Parity Portfolios*, arXiv:[1311.4057](https://arxiv.org/abs/1311.4057).
3. Tseng, P. (2001), "Convergence of a Block Coordinate Descent Method for Nondifferentiable Minimization", **Journal of Optimization Theory and Applications** 109(3), pp. 475–494 — the convergence result invoked by reference 2.
