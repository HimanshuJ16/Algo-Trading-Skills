# Pre-Flight Checklist

- [ ] Has the `bps_cost` been calibrated to realistic live trading conditions (including estimated market impact)?
- [ ] Is the ML signal properly aligned so prediction $P_t$ evaluates against actual return $R_{t+1}$?
- [ ] Has a grid search been performed on `signal_threshold` to verify the model has predictive power *after* costs, rather than just high turnover?
- [ ] Does the strategy still have a positive net CAGR?
