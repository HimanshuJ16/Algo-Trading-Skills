# Pre-Flight Checklist

## Risk inputs

- [ ] Is an annualized volatility estimated for every strategy, on one consistent basis?
- [ ] Is every volatility finite and strictly positive? (Zero, negative, and NaN must raise — never be clamped.)
- [ ] Is each `strategy_id` unique and non-empty?
- [ ] Is a full covariance matrix supplied? If not, is the team aware that **zero correlation is being assumed** and that the reported portfolio volatility is a floor?
- [ ] Was $\Sigma$ estimated from more observations than strategies, and from a period that includes a stress regime?
- [ ] Does $\sqrt{\Sigma_{ii}}$ agree with each declared $\sigma_i$? (Otherwise weights and risk decomposition come from different models.)

## Weighting method

- [ ] If correlations are **not** uniform, is the ERC solver being used rather than inverse volatility? (Inverse volatility is exact ERC only under equal pairwise correlations — MRT 2009 Eq. 3.)
- [ ] Did the solver converge, rather than exhaust its sweep budget?
- [ ] Are all resulting weights strictly positive and summing to 1.0?

## Risk audit

- [ ] Is the Euler decomposition self-consistent: $\sum_i \text{RC}_i = \sigma_p$?
- [ ] Is the risk-contribution error checked on **both** the absolute (percentage-point) and the relative gate? (Absolute alone is vacuous at large $N$.)
- [ ] Is `is_risk_balanced` true before any capital is deployed?
- [ ] Does $\sigma_{\text{erc}}$ sit between the minimum-variance and equal-weight portfolio volatilities, as theory requires?

## Deployment

- [ ] Is the cent-rounding residual in `allocated_capital_usd` reconciled rather than assumed to be zero?
- [ ] Is an independent drawdown circuit breaker in place? (Equal volatility contribution is not equal tail contribution.)
- [ ] Is any leverage or volatility-targeting decision handled outside this engine, which only splits a fixed pool?
- [ ] Has the turnover implied by the new weights been costed before rebalancing?
