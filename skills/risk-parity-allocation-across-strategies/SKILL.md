---
name: risk-parity-allocation-across-strategies
description: >-
  Use when allocating capital across a multi-strategy book so each strategy contributes an equal share of portfolio volatility — solving the Equal Risk Contribution (ERC) portfolio from a full covariance matrix, or falling back to inverse-volatility weights where correlations are uniform, with a risk-contribution audit before capital is deployed.
domain: Portfolio & Risk Management
subdomain: Risk Parity & Capital Allocation
tags: ["risk-parity", "equal-risk-contribution", "erc", "inverse-volatility", "portfolio-allocation", "capital-scaling"]
brokers_frameworks: ["Risk Parity / Equal Risk Contribution (ERC)", "Cyclical Coordinate Descent (Griveau-Billion et al. 2013)", "Covariance Matrix Risk Decomposition", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when allocating capital across a multi-strategy quantitative portfolio (trend following, statistical arbitrage, mean reversion, market making). Equal-capital allocation lets the highest-volatility strategy dominate total portfolio risk: split $1M evenly between a 30% vol strategy and a 10% vol strategy and the first carries 90% of the risk. Risk parity instead chooses weights so every strategy contributes the same share of portfolio volatility.

Two weighting schemes, and picking the wrong one is the main way this goes wrong:

- **Inverse volatility**, $w_i = \frac{1/\sigma_i}{\sum_j 1/\sigma_j}$ — closed form, and the *exact* ERC solution **only when every pairwise correlation is equal** (Maillard–Roncalli–Teïletche 2009, Eq. 3; for $n = 2$ it holds for any $\rho$).
- **Equal Risk Contribution**, solving $w_i (\Sigma w)_i = b_i\,\sigma(w)$ with equal budgets — correct under any correlation structure, and the only option that is actually risk parity when correlations differ.

## When NOT to Use

- **When you have no trustworthy covariance matrix.** With no $\Sigma$ this engine assumes zero correlation. Strategy correlations are rarely zero and rise under stress, so the reported portfolio volatility is a floor, not a forecast. An allocation "balanced" on that assumption is not balanced in a crisis.
- **As a drawdown control or tail-risk budget.** Equal *volatility* contribution is not equal *tail* contribution. A short-gamma or carry strategy contributes little volatility right up to the point it contributes all of the loss. Pair with `kill-switch-and-drawdown-circuit-breakers`.
- **To set portfolio leverage.** Weights sum to $1.0$; this splits a fixed pool. Levering the result to a target volatility is a separate decision — see `dynamic-position-sizing-based-on-realized-volatility`.
- **On strategies with too little history to estimate $\Sigma$.** A covariance matrix estimated from fewer observations than strategies is singular; the engine rejects it rather than returning weights fitted to noise.
- **As a rebalancing scheduler.** The allocation is recomputed from scratch each call with no turnover, cost, or weight-bound awareness. Deciding whether a drift is worth trading is `rebalancing-frequency-optimization-cost-vs-drift`.

## Prerequisites

- Strategy risk specifications (`StrategyRiskData`: `strategy_id`, `annualized_volatility`). Volatility must be finite and strictly positive — a standard deviation, so `0.20` is 20% annualized. `daily_returns` is optional caller context; the engine does **not** estimate volatility or covariance from it.
- Total capital to allocate (finite, positive; default \$1,000,000).
- Optional covariance matrix $\Sigma$ — square, symmetric, positive definite, on the same annualized basis as the declared volatilities. Strongly recommended: without it, correlations are assumed zero.

## Workflow

1. **Validate the risk inputs before computing anything.** Reject non-finite, zero, and negative volatilities, duplicate `strategy_id`s, and non-positive capital.
   - **Decision point — a corrupt volatility must stop the allocation, not be repaired.** Clamping a zero volatility to a small floor does not produce a conservative allocation; it produces the *largest* inverse-volatility weight in the book. Raise.
2. **Validate $\Sigma$ if supplied.** Check shape, finiteness, symmetry, positive definiteness (Cholesky), and that $\sqrt{\Sigma_{ii}}$ agrees with each declared $\sigma_i$.
   - **Decision point — a failed Cholesky is a data problem, not a numerical nuisance.** Do not floor a negative portfolio variance to a small positive number: that converts an impossible correlation into a plausible-looking volatility and a passing audit. Test the pivot against a *relative* tolerance — a perfectly correlated pair yields a final pivot near $10^{-18}$, not exactly zero.
3. **Choose the weighting scheme.**
   - No $\Sigma$ supplied ⟹ correlations are uniform (all zero) by assumption, so the closed form is already exact ERC. Use it.
   - $\Sigma$ supplied ⟹ solve for ERC by cyclical coordinate descent, iterating $w_i^\star = \frac{-\beta_i + \sqrt{\beta_i^2 + 4\sigma_i^2 b_i \sigma(w)}}{2\sigma_i^2}$ with $\beta_i = \sum_{j \neq i}\Sigma_{ij}w_j$, then rescale to $\sum_i w_i = 1$. Starting from strictly positive weights keeps every iterate positive, so the result is long-only without an explicit constraint.
   - **Decision point — do not reach for inverse volatility just because $\Sigma$ is inconvenient.** On MRT's own four-strategy example it puts 78% of portfolio risk in two of four strategies while reporting weights that look diversified.
4. **Decompose risk by Euler allocation**: $\sigma_p = \sqrt{w^{\mathsf T}\Sigma w}$, $\text{MCR}_i = (\Sigma w)_i/\sigma_p$, $\text{RC}_i = w_i \text{MCR}_i$. Volatility is homogeneous of degree 1, so $\sum_i \text{RC}_i = \sigma_p$ exactly — a useful self-check.
5. **Audit against the equal share $100\%/N$ on both an absolute and a relative tolerance.**
   - **Decision point — an absolute percentage-point tolerance alone is vacuous for a large book.** With 20 strategies the equal share is 5.00pp, so a strategy contributing *zero* risk sits exactly 5.00pp from target and passes a 5pp gate. Gate on the relative deviation too.
6. **Emit `RiskParityReport`** and deploy capital only when `is_risk_balanced` is true.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Calling inverse volatility "risk parity" under non-uniform correlations.** It is risk parity only when all pairwise correlations are equal (MRT 2009 Eq. 3). Feed the paper's own example — vols 10/20/30/40% with $\rho_{12}=0.8$, $\rho_{34}=-0.5$ — and inverse volatility returns 48/24/16/12% weights carrying 39.1/39.1/10.9/10.9% of risk against a 25% target. The true ERC weights are 38.4/19.2/24.3/18.2%.
- **Confusing capital parity with risk parity**: 50% capital to a 30% vol strategy and 50% to a 10% vol strategy gives the first 90% of total risk.
- **Silently assuming zero correlation** because no covariance matrix was passed. That is the single most optimistic assumption available, and it is the default when $\Sigma$ is omitted. Check `covariance_supplied` on the report before trusting the volatility figure.
- **Flooring a bad covariance matrix instead of rejecting it.** `max(variance, 1e-8)` turns a correlation of 5.0 into a 17% portfolio volatility and a "balanced" verdict. So does clamping a zero volatility to $10^{-4}$ — that strategy then receives roughly 1000× the weight of every other.
- **Letting a NaN through.** A single NaN volatility propagates to NaN weights, and `NaN > limit` is `False`, so the breach check passes and the report reads `RISK_PARITY_BALANCED` with NaN capital allocations. Non-finite inputs must raise.
- **Mixing two risk estimates in one report.** If the $\Sigma$ diagonal disagrees with the declared volatilities, the weights come from one model and the risk decomposition from another; nothing in the output reveals it.
- **Treating the audit tolerance as a scale-free number.** 5 percentage points means something different for 3 strategies (target 33.3pp) than for 20 (target 5pp).
- **Over-leveraging low-vol strategies.** Risk parity assigns the largest capital weight to the quietest strategy. If that quiet is a measurement artifact — a short sample, a stale mark, an untriggered tail — parity concentrates capital exactly where the risk estimate is least trustworthy.
- **Rebalancing on a stale covariance matrix.** Correlations converge under stress; a $\Sigma$ estimated in calm conditions understates exactly the co-movement that risk parity exists to neutralize.

## Verification

- Instantiate `RiskParityAllocationEngine`. Allocate across 3 uncorrelated strategies with vols 10%, 20%, 30% $\implies$ verify weights of 54.55%, 27.27%, 18.18% and equal 33.33% risk contributions.
- **Reproduce the published ERC solutions of MRT (2009) Sec. 4.1** — vols 10/20/30/40%:
  - Constant correlation matrix, any $\rho$ $\implies$ ERC weights 48/24/16/12% (their Eq. 3).
  - $\rho_{12}=0.8$, $\rho_{34}=-0.5$, others 0 $\implies$ ERC weights 38.4/19.2/24.3/18.2%, $\sigma_p = 10.3\%$, MCR 0.067/0.134/0.106/0.141, each contributing 25%.
  - The same matrix with equal weights $\implies$ $\sigma_p = 11.5\%$ and risk shares 12.3/26.4/14.1/47.2%, which validates the Euler decomposition independently of the solver.
- Negative checks: NaN, infinite, zero, and negative volatility; duplicate or empty `strategy_id`; non-positive or non-finite capital; and a covariance matrix that is the wrong shape, ragged, asymmetric, non-finite, non–positive-definite, perfectly correlated, or inconsistent with the declared volatilities — each must raise `ValueError`.
- Verify $\sum_i \text{RC}_i = \sigma_p$ and that unequal risk budgets are honoured proportionally.
- Run `python -m unittest discover -s skills/risk-parity-allocation-across-strategies/scripts`.

## Related Skills

- `risk-budget-allocation-across-time-horizons`
- `risk-adjusted-performance-attribution-per-strategy`
- `cross-strategy-correlation-monitoring`
- `correlation-aware-exposure-limits`
- `multi-strategy-capital-allocation-limits`
- `dynamic-position-sizing-based-on-realized-volatility`
