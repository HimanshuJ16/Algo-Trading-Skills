# Pre-Flight Checklist

## Data

- [ ] Are daily realised P&L and one-day forecast VaR recorded for the full window, ideally
      the most recent 250 trading days (12 months)?
- [ ] Are dates strict ISO 8601, unique, and in strictly increasing order?
- [ ] Is every forecast VaR a **positive** magnitude, and is a zero or negative value
      rejected rather than absolute-valued?
- [ ] Do all observations carry the **same** coverage level, matching the engine's?
- [ ] Is hypothetical P&L (HPL) captured alongside actual P&L (APL) where available?

## Exception counting

- [ ] Is the exception rule strict (`pnl < -var`), so a loss exactly equal to VaR is covered?
- [ ] Does a **missing or non-computable** P&L or VaR count as an outlier, per MAR32.5(2) —
      rather than silently comparing false and vanishing from the count?
- [ ] When both APL and HPL are present for the whole window, is the governing count the
      **greater** of the two, per MAR32.5(1)?
- [ ] Is a partial hypothetical series ignored rather than mixed into the actual series?
- [ ] Is the breach magnitude recorded for each exception, and left undefined for
      missing-data outliers?

## Statistics

- [ ] Is the Kupiec POF statistic computed in log space, with the zero-exception and
      all-exception analytic limits handled?
- [ ] Is the one-degree-of-freedom p-value `erfc(sqrt(s/2))` — and **not** `exp(-s/2)`,
      which is the two-degree-of-freedom function and inflates p-values roughly threefold?
- [ ] Is `exp(-s/2)` used only for the conditional-coverage statistic, which genuinely has
      two degrees of freedom?
- [ ] Is the Christoffersen Markov independence test run, so that clustered breaches are
      distinguished from evenly spread ones at the same count?
- [ ] Does the conditional-coverage statistic equal the sum of its two components?
- [ ] Is a two-sided Kupiec rejection with fewer exceptions than expected reported as
      over-conservatism rather than escalated as a breach event?

## Basel classification

- [ ] Are the zone boundaries derived from the **binomial** cumulative-probability rule at
      the actual sample size (95% for yellow, 99.99% for red)?
- [ ] Is the exception count **never** linearly rescaled to a 250-day equivalent?
- [ ] At 250 observations and 99% coverage, do the boundaries reproduce the published
      green 0-4 / yellow 5-9 / red 10 or more?
- [ ] Is the exact cumulative probability reported alongside the zone?
- [ ] Are windows shorter than 250 observations explicitly flagged as below the Basel basis?

## Capital and reporting

- [ ] Are the capital multipliers withheld off the published 250-day, 99% basis rather than
      extrapolated?
- [ ] Are the bcbs22 increments, MAR32.9 total multipliers, and SEC Appendix E factors kept
      distinct and never added to one another?
- [ ] Does the report describe a red zone as an automatic multiplier increase with possible
      supervisory disallowance — **not** as automatic model disqualification?
- [ ] Are statistics returned at full precision, so a decisive rejection is never printed as
      "p = 0.0"?
- [ ] Is each exception documented with an explanation of its cause (bcbs22 Sec. III(e),
      MAR32.12)?

## Expected Shortfall (if used)

- [ ] Is the ES series complete, positive, and at the same coverage level as the VaR series?
- [ ] Is the Z2 statistic reported **without** a p-value, with the caveat that its critical
      value requires simulating the predictive distribution?
- [ ] Is a negative Z2 read as "VaR and/or ES underestimate realised risk"?
