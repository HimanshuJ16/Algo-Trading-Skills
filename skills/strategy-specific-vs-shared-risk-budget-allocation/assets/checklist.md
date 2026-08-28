# Pre-Flight Checklist

## Inputs

- [ ] Is $\Sigma$ estimated from **daily** strategy returns, on one consistent basis across every strategy?
- [ ] Does `trading_days_per_year` match the calendar $\Sigma$ was estimated on? (252 US equities, ~250 NSE, 365 crypto.)
- [ ] Was $\Sigma$ estimated from more observations than strategies, and from a window that includes a stress regime?
- [ ] Is $\Sigma$ finite, symmetric and positive definite — and **rejected** rather than floored if not?
- [ ] Does `strategy_ids_order` list every registered strategy exactly once, in $\Sigma$'s row/column order?
- [ ] Are all `target_capital_usd` and both limit fields finite and strictly positive?

## Budget policy

- [ ] Do the `max_shared_risk_contribution_pct` values sum to at least 100%? (Shares always sum to 100%, so a smaller total is unsatisfiable — check `budgets_feasible`.)
- [ ] Is it clear which of the two tiers is meant to bind for each strategy, and who owns remediation for each?
- [ ] Is the standalone limit understood as **correlation-blind** — so a good hedge is expected to trip it?

## Risk audit

- [ ] Is `is_euler_decomposition_valid` true — and understood as a *degeneracy* check (NaN, floored $\sigma_p$), not evidence that $\Sigma$ is sound?
- [ ] Do the component shares sum to 100%, and are negative shares recognized as legitimate hedging rather than an error?
- [ ] Are `standalone_limit_breached` and `shared_budget_breached` read **separately**, so no hedge is cut on the standalone number alone?
- [ ] Is `var_horizon_days` carried alongside every VaR figure that leaves the engine? (252-day is ~15.9× the one-day number.)
- [ ] Is `var_horizon_days=1` used for any figure compared against a daily or intraday monitoring limit?
- [ ] Is the VaR understood as Gaussian, zero-mean and $\sqrt{T}$-scaled — a planning figure that understates fat tails, not a tail-loss estimate?

## Remediation

- [ ] Is `shared_budget_capital_factor` applied to **capital**, and `standalone_delever_factor` applied to the strategy's **positions**? (Capital scaling cannot change $\sigma_i$.)
- [ ] After applying any factor, was `evaluate_risk_budgets` re-run and `breached_strategies` confirmed empty? (Factors are computed one strategy at a time; applying several at once does not land them all on budget.)
- [ ] Is the remediation loop bounded, with escalation if it does not converge?
- [ ] Is any `shared_budget_infeasible_by_scaling = True` escalated as a budget-policy problem rather than retried?

## Reporting

- [ ] Do outbound reports say "Component VaR" in full, never "CVaR"? (CVaR means Conditional VaR / Expected Shortfall, which this engine does not compute.)
- [ ] Is an independent drawdown circuit breaker in place, given that equal volatility contribution is not equal tail contribution?
- [ ] Has the turnover implied by any recommended reallocation been costed before trading it?
