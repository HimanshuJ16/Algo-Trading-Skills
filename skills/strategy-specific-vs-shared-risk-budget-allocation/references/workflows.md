# Workflows for Strategy-Specific vs Shared Risk Budget Allocation

## 1. Ingest and validate inputs

1. **Specs.** One `StrategyRiskBudgetSpec` per strategy. `strategy_id` unique and non-empty; `target_capital_usd`, `max_standalone_volatility_pct` and `max_shared_risk_contribution_pct` finite and strictly positive. Limits are percentages, so `15.0` is 15%.
2. **Covariance matrix.** $N \times N$, from **daily** strategy returns, ordered to match `strategy_ids_order`. Validate in this order and raise on the first failure:
   - shape is $(N, N)$ and two-dimensional;
   - every entry finite (a single NaN makes $\sigma_i$ NaN, and `NaN > limit` is `False`, so all limit checks silently pass);
   - symmetric within $10^{-9}$ relative to $\max\lvert\Sigma_{ij}\rvert$;
   - positive definite, tested as $\lambda_{\min} > 10^{-12}\lambda_{\max}$ — a relative gate, because a perfectly correlated pair leaves $\lambda_{\min}\approx 10^{-20}$ rather than $0$.
   - **Never floor the variance.** `max(w'Σw, 1e-8)` produces a plausible volatility from an impossible correlation, and the Euler identity still sums to 100%, so no downstream check catches it.
3. **Identifier order.** `strategy_ids_order` must be a permutation of the registered specs. An omission drops that strategy's capital from the denominator and under-reports total portfolio risk; a duplicate double-counts it. Raise on empty, unknown, duplicated or missing ids.

## 2. Portfolio volatility and VaR

- Capital weights $w_i = C_i / \sum_j C_j$.
- $\sigma_p = \sqrt{w^{\mathsf T}\Sigma w}$ (daily), annualized as $\sigma_p\sqrt{252}$.
- $\text{VaR}_{95\%} = \left(\sum_j C_j\right)\times\sigma_p\times\sqrt{H}\times 1.645$ for a horizon of $H$ trading days.
- **Always record $H$ with the number.** The default $H = 252$ makes the figure roughly $15.9\times$ the one-day equivalent. Set `var_horizon_days=1` for anything compared against a daily monitoring limit, and read the square-root-of-time caveat in `references/standards.md`.

## 3. Euler decomposition

$$\text{MCR}_i = \frac{(\Sigma w)_i}{\sigma_p}, \qquad \text{RC}_i = w_i\,\text{MCR}_i, \qquad c_i = \frac{\text{RC}_i}{\sigma_p} = \frac{w_i(\Sigma w)_i}{w^{\mathsf T}\Sigma w}$$

- $\sum_i \text{RC}_i = \sigma_p$ and $\sum_i c_i = 1$ exactly, by Euler's theorem on the degree-1 homogeneity of $\sigma_p$.
- Treat `is_euler_decomposition_valid` as a **degeneracy detector**: the identity is algebraic, so a `False` means NaN propagated or $\sigma_p$ was floored. It says nothing about whether $\Sigma$ is a good estimate.
- $c_i$ may be **negative** for a genuine hedge. That is correct output, not an error.

## 4. Dual-tier audit

| Tier | Quantity | Compared against | Correlation-aware? |
|---|---|---|---|
| Strategy-specific | $\sigma_i = \sqrt{\Sigma_{ii}}\sqrt{252}\times100$ | `max_standalone_volatility_pct` | No |
| Shared | $c_i \times 100$ | `max_shared_risk_contribution_pct` | Yes |

- Comparison is strict `>`: a strategy exactly on its limit is compliant.
- Report the two flags separately. A hedge can breach its standalone limit while contributing a negative component share — cutting it on the standalone number alone *raises* portfolio risk.
- Check budget feasibility: shares always sum to 100%, so budgets summing to less than 100% are unsatisfiable by construction. That is a policy error to escalate, not a strategy breach.

## 5. Remediation factors

### Shared budget → scale capital (solved, not divided)

$c_i$ is roughly quadratic in $w_i$ for a dominant strategy, and scaling one strategy renormalizes every other weight. It is also not monotone in $w_i$ when the strategy hedges the rest of the book. So:

- **Do not use `budget / actual`.** Worked example: 70/30 book, $\Sigma = [[4\times10^{-4}, 2\times10^{-5}],[2\times10^{-5}, 10^{-4}]]$. The dominant strategy holds 93.81% of portfolio risk against a 40% budget. The naive ratio is 0.4264; applying it leaves the share at **77.6%**. The solved factor is 0.1714, which lands it at 40%.
- Bisect on $k \mapsto c_i(k) - \text{budget}$ over $k \in (10^{-9}, 1]$. A sign change is available because the share tends to 0 as the weight tends to 0 and exceeds the budget at the current weight; bisection needs only that, so non-monotonicity is safe.
- Return the **lower** bracket and **floor** the printed factor. Rounding can push the factor above the root — $0.17142857 \to 0.171429$ — leaving a residual breach.
- If even the minimum scale exceeds the budget, the budget is unreachable by scaling (a one-strategy book holds 100% of its own risk at every capital level). Report `shared_budget_infeasible_by_scaling = True` and a factor of $0.0$ rather than a factor that does nothing.

### Standalone limit → de-lever the strategy, not the capital line

$\sigma_i = \sqrt{\Sigma_{ii}}$ has no dependence on $w$. Reducing the strategy's capital allocation leaves the reported standalone volatility **exactly unchanged**, so re-running the engine reports the identical breach forever and a naive remediation loop never terminates. `standalone_delever_factor` $= \text{limit}/\sigma_i$ is a target for the strategy's own position sizing; a standalone breach is a gate on the strategy, escalated to whoever owns it.

### Multiple simultaneous breaches

Both factors are computed **one strategy at a time, holding the others fixed**. Applying several at once does not land them all on budget, because each change moves every other strategy's share. Apply, re-run `evaluate_risk_budgets`, and iterate until `breached_strategies` is empty. Bound the loop and escalate if it does not converge.

## 6. Sign-off

Deploy capital only when `breached_strategies` is empty, `budgets_feasible` is true, and `is_euler_decomposition_valid` is true. Record `var_horizon_days` and `var_confidence_z` with any VaR figure that leaves the engine.
