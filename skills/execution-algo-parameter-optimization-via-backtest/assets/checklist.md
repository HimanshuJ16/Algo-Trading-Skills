# Pre-Flight Checklist — Execution Algo Parameter Optimization

## Sample

- [ ] Are the samples **parent** orders, with side, arrival price, and the observed intraday price path?
- [ ] Is per-interval market volume supplied, rather than falling back to a uniform ADV split?
- [ ] Does `execution_horizon_days` match the span the price path actually covers?
- [ ] Is `shares_outstanding` supplied, or is the `PERMANENT IMPACT OMITTED` warning understood and accepted?
- [ ] Was the **holdout set split off before** any searching began, and split chronologically?
- [ ] Does the sample span more than one volatility regime?

## Cost model

- [ ] Have the impact coefficients been recalibrated against your own realized TCA, or is the ATHL (2005) US large-cap 2001-2003 fit being used knowingly as a placeholder?
- [ ] Is the temporary-impact exponent $3/5$ rather than a square root? (ATHL reject $\beta = 1/2$ at 95%.)
- [ ] Is the calibration in force recorded alongside the optimum it produced?

## Grid

- [ ] Is `max_allowed_participation_rate` set from written policy, **before** seeing which candidate won?
- [ ] If the flow is a US issuer repurchase, is the Rule 10b-18(b)(4) 25%-of-ADTV volume condition reflected?
- [ ] Is the grid coarse enough that its candidates are distinguishable given the sample size?
- [ ] Have `rejected_configs` been reviewed, so nothing was excluded that was meant to be searched?

## Result integrity

- [ ] Is `selection_is_separated` true — is the winner's margin outside the combined standard error of the two leading candidates?
- [ ] Have `worst_implementation_shortfall_bps` and `min_fill_completion_rate` been read, not just the means?
- [ ] Was `holdout_samples` supplied, and is `holdout_is_degradation_bps` acceptable?
- [ ] Are all `warnings` on the report resolved or explicitly accepted in writing?
- [ ] Does re-running the optimization reproduce the identical selection?

## Before promotion

- [ ] Is the full `AlgoOptimizationAuditReport` persisted for audit, not just the winning parameters?
- [ ] Is the live participation guard and kill switch independent of anything this search produced?
- [ ] Has the candidate gone through paper-to-live promotion rather than straight from the optimizer to production?
