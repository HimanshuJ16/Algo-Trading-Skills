# Pre-Flight Checklist

## Inputs
- [ ] Are all sleeve return series aligned by date and of equal length (not truncated to the shortest)?
- [ ] Are returns decimals (0.001 = 0.1%), finite, and no worse than -1.0?
- [ ] Is `trading_days_per_year` set to the sampling frequency of the returns (252 for daily bars)?
- [ ] Are `strategy_id` values unique and allocated capital non-negative?

## Consolidation
- [ ] Are sub-strategy capital allocations and PnL aggregated?
- [ ] Is the capital-weighted joint daily return series synthesized?
- [ ] Is the true portfolio Sharpe ratio calculated from that series, rather than averaged from sleeve Sharpe ratios?
- [ ] Is the diversification benefit ratio ($\frac{\sum w_k \sigma_k}{\sigma_p}$) audited?
- [ ] Is portfolio max drawdown computed from the joint equity path, not taken as the maximum of sleeve drawdowns?

## Before the report leaves the firm
- [ ] Has `report.warnings` been read, and every warning either resolved or disclosed?
- [ ] Are any `NaN` metrics understood as undefined (zero-volatility window), not as zero or as missing data?
- [ ] Do `portfolio_return_pct` (booked PnL) and `series_implied_return_pct` (compounded series) reconcile?
- [ ] Is the window at least one year, or is the annualization explicitly disclosed as an extrapolation (GIPS 2020 Provision 2.A.12)?
- [ ] Is it recorded whether the figures are gross or net of fees and transaction costs, and is net performance shown alongside gross where 17 CFR 275.206(4)-1(d)(1) applies?
- [ ] Are the sleeves' marks liquid enough that $\sqrt{252}$ annualization is defensible, or is the serial-correlation caveat disclosed (Lo 2002)?
