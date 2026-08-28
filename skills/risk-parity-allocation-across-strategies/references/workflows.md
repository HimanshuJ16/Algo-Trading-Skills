# Workflows for Risk Parity Allocation Across Strategies

## 1. Volatility & covariance estimation (upstream of this engine)

Estimate annualized volatility for every candidate strategy, and — strongly preferred — the full cross-strategy covariance matrix $\Sigma$ on the same annualized basis. This engine consumes those estimates; it does not produce them, and `daily_returns` on `StrategyRiskData` is caller context only.

Two properties of the estimate determine whether anything downstream is meaningful:

- **Sample length.** A covariance matrix estimated from fewer observations than strategies is singular and will be rejected. Near-singularity (highly similar strategies) is the realistic failure and shows up as slow solver convergence.
- **Regime.** Correlations estimated in calm conditions understate crisis co-movement. Risk parity exists to neutralize co-movement, so a stale $\Sigma$ undermines exactly the thing being bought.

## 2. Input validation (fail closed)

Reject, do not repair:

- non-finite, zero, or negative `annualized_volatility`;
- duplicate or empty `strategy_id`;
- non-finite or non-positive `total_capital_usd`;
- a covariance matrix that is non-square, ragged, non-finite, asymmetric, not positive definite, or whose diagonal disagrees with the declared volatilities by more than 1% relative.

Each of these previously produced a *confident, wrong* allocation rather than an error: NaN volatilities yielded a `RISK_PARITY_BALANCED` report with NaN capital, a negative volatility handed 99.9% of the book to one strategy, and a variance floor absorbed an impossible correlation of 5.0 into a plausible 17% portfolio volatility.

## 3. Weighting

**No covariance matrix supplied.** Correlations are assumed zero — uniform by definition — so the closed form is the exact ERC solution (MRT 2009, Eq. 3):

$$w_i = \frac{1/\sigma_i}{\sum_j 1/\sigma_j}$$

Record that the assumption was made; the resulting portfolio volatility is a floor.

**Covariance matrix supplied.** Solve the risk budgeting condition $w_i(\Sigma w)_i = b_i\,\sigma(w)$ with $b_i = 1/n$ by cyclical coordinate descent (Griveau-Billion et al. 2013, Eq. 4). Each sweep visits every coordinate and sets

$$w_i^\star = \frac{-\beta_i + \sqrt{\beta_i^2 + 4\sigma_i^2 b_i \sigma(w)}}{2\sigma_i^2}, \qquad \beta_i = \sum_{j \neq i}\Sigma_{ij}w_j$$

the positive root of the quadratic in $w_i$ obtained by holding the other weights fixed. Iterate until the largest weight change in a sweep falls below tolerance, then rescale so $\sum_i w_i = 1$ (the fixed point is defined only up to scale — MRT 2009, Appendix A.2).

Implementation notes:

- Seed from inverse-volatility weights. Under equal correlations that is already the answer, so the equicorrelated case converges immediately rather than drifting toward it.
- Maintain $\Sigma w$ incrementally: updating one weight changes it in $O(n)$, keeping a sweep at $O(n^2)$.
- Positive starting weights keep every iterate positive, so long-only holds by construction with no explicit constraint.
- Exhausting the sweep budget raises. A partial solution is not a risk parity portfolio, and returning one silently is how a near-singular $\Sigma$ turns into a live allocation.
- The same routine solves unequal risk budgets — pass `risk_budgets`. Budgets must be strictly positive; Eq. 4 is undefined at $b_i = 0$ (ibid., Remark 3).

## 4. Risk decomposition

$$\sigma_p = \sqrt{w^{\mathsf T}\Sigma w}, \qquad \text{MCR}_i = \frac{(\Sigma w)_i}{\sigma_p}, \qquad \text{RC}_i = w_i\,\text{MCR}_i, \qquad c_i = \frac{\text{RC}_i}{\sigma_p}$$

$\sum_i \text{RC}_i = \sigma_p$ exactly, so $\sum_i c_i = 1$. Assert it — a violation means the weights and $\Sigma$ came from different places.

## 5. Balance audit and capital deployment

Compare each $c_i$ against the equal share $1/N$ on **two** gates:

- absolute: $|c_i - 1/N| \times 100 \leq$ `max_allowed_risk_error_pct` (percentage points);
- relative: $|c_i - 1/N| / (1/N) \times 100 \leq$ `max_allowed_relative_error_pct`.

Both must pass. The absolute gate alone degrades as $N$ grows — at $N = 20$ the equal share is 5.00pp, so a strategy contributing zero risk sits exactly 5.00pp away and clears a 5pp limit. A worked case: ten equally volatile strategies at base correlation 0.30 with one pair at 0.80, weighted by inverse volatility, land 1.05pp from target (clears 5pp) but 10.5% away in relative terms (fails). Solving for ERC on the same matrix brings both to zero.

Emit `RiskParityReport` and deploy only on `is_risk_balanced`. Check `covariance_supplied` before quoting `portfolio_annualized_volatility` to anyone: if it is `False`, that number assumes zero correlation.

## 6. Interpreting the result

- Risk parity assigns the **largest** capital weight to the **quietest** strategy. That is the intent, and also the exposure: if the quiet is an artifact of a short sample or a stale mark, parity concentrates capital where the estimate is least reliable.
- $\sigma_{\text{mv}} \leq \sigma_{\text{erc}} \leq \sigma_{1/n}$ (MRT 2009, Appendix A.3). An ERC portfolio outside that band indicates an estimation problem, not a discovery.
- Equal volatility contribution is not equal tail contribution, and the ERC portfolio is the maximum-Sharpe portfolio only under constant correlation with equal individual Sharpe ratios (ibid., Sec. 3.5). Risk parity is a diversification rule, not an optimality claim.
