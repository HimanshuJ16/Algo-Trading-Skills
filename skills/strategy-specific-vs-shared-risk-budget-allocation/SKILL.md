---
name: strategy-specific-vs-shared-risk-budget-allocation
description: >-
  Use when a multi-strategy book enforces two different risk limits per strategy — a strategy-specific cap on its own standalone volatility and a shared cap on its share of total portfolio risk — decomposing portfolio volatility by Euler allocation into marginal contribution to risk (MCR) and Component VaR shares, and solving for the capital scaling factor that actually brings a breaching strategy back inside its budget.
domain: Risk Management & Portfolio Optimization
subdomain: Euler Risk Budgeting & Allocation
tags: ["risk-budgeting", "euler-allocation", "component-var", "mcr", "strategy-risk-limits", "portfolio-var"]
brokers_frameworks: ["Euler Allocation Principle (Tasche 2008)", "Component VaR (Jorion, Value at Risk 3rd ed. Ch. 7)", "Parametric Variance-Covariance VaR", "Python Dataclasses", "numpy"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a multi-strategy book runs **two limits per strategy** and needs to know which one is binding. Policing risk only at the strategy level ignores diversification and punishes good hedges; policing it only at the portfolio level lets one volatile strategy quietly consume the fund's whole risk capacity.

- **Strategy-specific limit — standalone volatility.** $\sigma_i = \sqrt{\Sigma_{ii}}$, annualized. A property of the strategy's *own* return series. Correlation-blind by construction.
- **Shared limit — component risk contribution.** The strategy's share of *portfolio* volatility once correlation is accounted for, by Euler allocation:

$$\sigma_p = \sqrt{w^{\mathsf T}\Sigma w}, \qquad \text{MCR}_i = \frac{(\Sigma w)_i}{\sigma_p}, \qquad \text{RC}_i = w_i\,\text{MCR}_i, \qquad c_i = \frac{\text{RC}_i}{\sigma_p}$$

$\sigma_p$ is homogeneous of degree 1 in $w$, so Euler's theorem gives $\sum_i \text{RC}_i = \sigma_p$ exactly, with no residual (Tasche 2008). Because parametric VaR is a fixed multiple of $\sigma_p$, $c_i$ is also the **Component VaR** share: $\text{Component VaR}_i = c_i \times \text{VaR}_p$.

> **Terminology.** $c_i$ is the *Component VaR* share. It is **not** CVaR. In the risk literature CVaR means Conditional Value-at-Risk (Expected Shortfall) — a tail-average loss measure this engine does not compute. Never label these outputs "CVaR" in a risk report.

## When NOT to Use

- **As a tail-risk or drawdown budget.** Equal volatility contribution is not equal tail contribution. A short-gamma or carry strategy contributes little volatility right up to the point it contributes all of the loss. The reported figure is a Gaussian, zero-mean parametric VaR — it says nothing about losses beyond the quantile. Pair with `kill-switch-and-drawdown-circuit-breakers` and `tail-correlation-between-strategies-under-stress`.
- **As a monitoring limit at the default horizon.** `var_horizon_days` defaults to 252 (one year) to match the annualized volatility reported alongside it. That is the most aggressive possible use of the square-root-of-time rule, which systematically *underestimates* risk under jump diffusion, worsening with horizon and confidence level (Danielsson & Zigrand 2006). For any figure compared against an intraday or daily limit, set `var_horizon_days=1`.
- **Without a trustworthy $\Sigma$.** Every number is a function of the caller's covariance matrix. The engine does not estimate it, does not know it is stale, and does not know correlations converge under stress. A book balanced on a calm-period $\Sigma$ is not balanced in the drawdown the budget existed for. See `cross-strategy-correlation-monitoring`.
- **To clear a standalone volatility breach by reallocating capital.** $\sigma_i$ is read from the covariance diagonal and is invariant to capital weights — see the Workflow decision point below.
- **As a rebalancing scheduler or optimizer.** No turnover, transaction-cost, or weight-bound awareness; the audit is recomputed from scratch each call. Whether a reallocation is worth paying for is `rebalancing-frequency-optimization-cost-vs-drift`. To *solve* for balanced weights rather than audit given ones, use `risk-parity-allocation-across-strategies`.

## Prerequisites

- One `StrategyRiskBudgetSpec` per strategy: `strategy_id` (unique, non-empty), `target_capital_usd`, `max_standalone_volatility_pct`, `max_shared_risk_contribution_pct`. All three numbers must be finite and **strictly positive** — limits are given in percent, so `15.0` means 15%.
- An $N \times N$ covariance matrix of **daily** strategy returns: finite, symmetric, and positive definite. It is annualized internally by $\sqrt{252}$; override `trading_days_per_year` if $\Sigma$ was estimated on a different calendar (NSE ~250, crypto 365). If $\Sigma$ is *already* annualized, pass `trading_days_per_year=1` **and** `var_horizon_days=1` — otherwise it is annualized twice and every figure is ~15.9× too large. Nothing in the numbers reveals this, so the unit convention is the caller's responsibility.
- `strategy_ids_order` listing **every** registered `strategy_id` exactly once, in the row/column order of $\Sigma$.

## Workflow

1. **Validate $\Sigma$ before computing anything.** Check shape, finiteness, symmetry, and positive definiteness (smallest eigenvalue relative to the largest — a reciprocal condition-number floor).
   - **Decision point — reject a bad matrix, never repair it.** `port_vol = sqrt(max(1e-8, w'Σw))` is the tempting one-liner and it is the most dangerous line in a risk engine: an implied correlation of 5.0 becomes a plausible-looking portfolio volatility, the Euler identity still sums to 100%, and the audit passes. A negative diagonal is worse — $\sqrt{\Sigma_{ii}}$ is NaN, and `NaN > limit` is `False`, so *every* standalone breach silently disappears.
   - **Decision point — test the eigenvalue relatively, not against zero.** Two perfectly correlated strategies leave a smallest eigenvalue around $10^{-20}$, not exactly $0$.
2. **Validate `strategy_ids_order` as a permutation of the registered specs.** An omitted id drops that strategy's capital from the portfolio and under-reports total risk; a duplicate id counts one strategy's capital twice. Both are silent without an explicit check — raise on either.
3. **Compute portfolio volatility and VaR.** $\sigma_p = \sqrt{w^{\mathsf T}\Sigma w}$ with $w_i$ the capital weights; $\text{VaR}_{95\%} = \text{Capital} \times \sigma_p \times \sqrt{H} \times 1.645$ for a horizon of $H$ trading days. Record $H$ alongside the number — an unlabelled "95% VaR" is ambiguous by a factor of $\sqrt{252} \approx 15.9$.
4. **Decompose by Euler allocation**: $\text{MCR}_i$, $\text{RC}_i$, and the share $c_i$.
   - **Decision point — the Euler identity check is a degeneracy detector, not a model check.** $\sum_i c_i = 1$ holds *algebraically* whenever $\sigma_p$ is computed exactly, so `is_euler_decomposition_valid = True` proves only that no NaN propagated and $\sigma_p$ was not floored. It is not evidence the covariance matrix is sound.
5. **Audit both tiers independently.** Compare $\sigma_i$ against `max_standalone_volatility_pct` and $c_i$ against `max_shared_risk_contribution_pct`, strictly (`>`), so a strategy sitting exactly on its limit is compliant. Report the two flags separately: a strong hedge can be the loudest standalone offender while contributing *negative* portfolio risk.
   - **Decision point — also check the budgets are jointly satisfiable.** Shares always sum to 100%, so if the declared `max_shared_risk_contribution_pct` values sum to less than 100 no allocation can ever satisfy them all. That is a policy error, not a breach.
6. **Produce the two adjustment factors, and do not confuse them.**
   - **`shared_budget_capital_factor` — multiply target capital by this.** Solve for it; do **not** divide. Component risk share is not linear in the capital weight (roughly quadratic for a dominant strategy, and scaling one strategy renormalizes every other weight), and it is not even monotone when the strategy is a strong hedge. The engine bisects on the exact share function and returns the conservative lower bracket, so the result lands at or under budget.
   - **`standalone_delever_factor` — de-lever the strategy's positions, not its capital line.** $\text{limit}/\sigma_i$ is a target for the strategy's own risk-taking. Re-running this engine with reduced capital reports the *identical* standalone breach forever, because $\sigma_i$ comes from the covariance diagonal. A standalone breach is a gate on the strategy, not something the allocator can fix.
   - **Decision point — factors are computed one strategy at a time, holding the others fixed.** Applying several simultaneously does not land them all on budget. Re-run the engine and iterate until `breached_strategies` is empty.
7. **Emit `PortfolioRiskBudgetAllocationReport`** and deploy capital only once `breached_strategies` is empty and `budgets_feasible` is true.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Calling the recommended factor `budget / actual`.** The single most consequential bug in this skill's history. On a 70/30 book with $\Sigma = [[4\times10^{-4}, 2\times10^{-5}], [2\times10^{-5}, 10^{-4}]]$, the dominant strategy holds 93.81% of portfolio risk. Against a 40% budget the naive ratio is $40/93.81 = 0.4264$, and applying it leaves the strategy at **77.6%** — still nearly double its budget, while the report claims the breach is remediated. The solved factor is 0.1714.
- **Trying to fix a standalone volatility breach with capital.** $\sigma_i = \sqrt{\Sigma_{ii}}$ does not depend on $w$. Halve the strategy's capital and the reported standalone volatility is unchanged, so the breach never clears and the remediation loop never terminates.
- **Cutting a high-volatility strategy that is the book's best diversifier.** Standalone volatility ignores correlation entirely. A hedge can breach its standalone limit while contributing a *negative* component risk share; removing it raises portfolio risk. Always read both flags before acting on either.
- **Flooring the portfolio variance.** `max(w'Σw, 1e-8)` converts an indefinite covariance matrix into a plausible volatility and a passing audit — and the Euler identity still sums to 100%, so the self-check does not catch it.
- **Letting a NaN through.** One NaN in $\Sigma$ makes $\sigma_i$ NaN, and `NaN > limit` is `False`. Every standalone limit then passes and the strategy appears compliant. Non-finite input must raise.
- **Reading an unlabelled "95% VaR" as a daily number.** At the default 252-day horizon the figure is roughly 15.9× the one-day equivalent. A 95% VaR of 30% of capital is unremarkable for a year and alarming for a day. Always carry `var_horizon_days` with the number.
- **Trusting $\sqrt{T}$ scaling to a one-year horizon.** The square-root-of-time rule systematically underestimates risk under jump diffusion, and the underestimation worsens with the horizon and the confidence level (Danielsson & Zigrand 2006). The annual figure is a capital-planning number, not a risk limit.
- **Abbreviating Component VaR as "CVaR".** CVaR is Conditional Value-at-Risk / Expected Shortfall everywhere else in the literature. The collision turns a volatility-share report into an apparent tail-loss report.
- **Setting shared budgets that sum to less than 100%.** Component shares always sum to 100%, so such a budget set is unsatisfiable by construction and will show a permanent breach somewhere in the book.
- **Omitting a live strategy from `strategy_ids_order`.** Without validation its capital vanishes from the denominator and total portfolio risk is under-reported while every individual share looks fine.
- **Rebalancing on a stale $\Sigma$.** Correlations converge under stress; a calm-period covariance understates exactly the co-movement the shared budget exists to cap.

## Verification

- Instantiate `StrategySpecificVsSharedRiskBudgetEngine` on a 50/50 book with daily $\Sigma = [[10^{-4}, 2\times10^{-5}], [2\times10^{-5}, 4\times10^{-4}]]$. By hand: $\Sigma w = [6\times10^{-5},\, 2.1\times10^{-4}]$, $w^{\mathsf T}\Sigma w = 1.35\times10^{-4}$, so the component shares are the exact rationals $2/9 = 22.22\%$ and $7/9 = 77.78\%$ $\implies$ verify `is_euler_decomposition_valid = True`, the two shares, and $\sum_i \text{RC}_i = \sigma_p$.
- Standalone volatilities from the diagonal alone: $\sqrt{10^{-4}}\sqrt{252}\times100 = 15.87\%$ and $\sqrt{4\times10^{-4}}\sqrt{252}\times100 = 31.75\%$.
- **Adjustment-factor regression** (the test that fails against the old `budget / actual` behavior): on the 70/30 concentrated book, the dominant strategy's share is 93.81% against a 40% budget; verify the returned `shared_budget_capital_factor` is strictly below the naive 0.4264, that re-running with capital scaled by it leaves the share $\leq 40\%$ and `breached_strategies` empty, and that the naive ratio would instead leave it above 70%.
- Verify a hedge with $\Sigma = [[4\times10^{-4}, -1.8\times10^{-4}], [-1.8\times10^{-4}, 10^{-4}]]$ breaches its standalone limit while its component share is negative and its shared budget is not breached.
- Verify $\sigma_i$ is unchanged when the strategy's capital is halved, and that a strategy sitting exactly on its limit is not flagged.
- Verify the 252-day VaR equals $\sqrt{252}$ times the one-day VaR and that `var_horizon_days` is reported.
- Negative checks — each must raise `ValueError`: covariance matrix of the wrong shape, one-dimensional, non-numeric, containing NaN or infinity, asymmetric, with a negative or zero diagonal entry, indefinite, or singular through perfect correlation; `strategy_ids_order` that is empty, omits a registered strategy, repeats one, or names an unknown one; a spec with non-positive or non-finite capital or limits, or an empty `strategy_id`; duplicate specs; and a non-positive or non-finite `confidence_level_z`, `trading_days_per_year`, or `var_horizon_days`.
- Run `python -m unittest discover -s skills/strategy-specific-vs-shared-risk-budget-allocation/scripts`.

## Related Skills

- `risk-parity-allocation-across-strategies`
- `multi-strategy-capital-allocation-limits`
- `correlation-aware-exposure-limits`
- `cross-strategy-correlation-monitoring`
- `risk-budget-allocation-across-time-horizons`
- `tail-correlation-between-strategies-under-stress`
- `value-at-risk-var-live-monitoring`
- `portfolio-level-stop-loss-independent-of-strategy-stops`
- `kill-switch-and-drawdown-circuit-breakers`
