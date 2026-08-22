# Pre-Flight Checklist — Cold Start for Newly Listed Instruments

## Inputs

- [ ] Is `n_obs` a count of **usable observations** (valid prices, halted sessions and
      the listing auction excluded), not `today - list_date`?
- [ ] Is it consistent about counting prices vs returns?
- [ ] Is the case distinguished from a backfill gap and from a stale, thinly traded old
      instrument? Neither is a cold start.
- [ ] Is the peer prior a **single-name** volatility (median of comparable names), not a
      sector-ETF volatility?
- [ ] Are the sample volatility and the prior in the **same units** (both annualized, or
      neither)? Nothing downstream can detect a mismatch.
- [ ] Was the peer-selection rule fixed before the instrument listed, and is the prior
      computed point-in-time for backtests?
- [ ] Is `nu_0` (`prior_strength_days`) calibrated from the prior's own uncertainty
      (`nu_0 = 2 / v`) rather than picked to feel about right?

## Estimator

- [ ] Is shrinkage applied in **variance** space, not to standard deviations?
- [ ] Does the weight come from degrees of freedom (`nu / (nu + nu_0)`), not from the
      probation window's length?
- [ ] Is `n_obs < 2` handled as a branch that returns the pure prior — not as a zero
      weight multiplied into a possibly-`NaN` sample?
- [ ] Do a missing, zero, or negative peer prior raise rather than produce a zero or
      `NaN` volatility?
- [ ] Is it clear to the caller whether the scale or the posterior mean of `sigma**2` is
      being consumed?

## Risk

- [ ] Is `warmup_period_days` justified by something (lock-up expiry, index seasoning,
      first earnings) rather than being a round number?
- [ ] Is the size cap monotonically non-decreasing in `n_obs` and never above the base
      allocation?
- [ ] Is the cap treated as a ceiling applied *after* the volatility-scaled target, not
      as a target itself?
- [ ] Is a `probation_floor_pct` set if the alternative is excluding new listings
      entirely — so the onboarding path is exercised before it matters?
- [ ] Is the status recomputed on every rebalance rather than cached from onboarding?
- [ ] Is borrow availability checked separately for any short leg?

## After graduation

- [ ] Is it understood that `is_probationary == False` governs the **cap only**, and that
      the estimate is still shrunk?
- [ ] Is a review scheduled at lock-up expiry, first earnings, and index addition?

## Verification

- [ ] Do the unit tests pass (`python -m unittest discover -s . -p "test_*.py"` from
      `scripts/`)?
- [ ] Does the backtest assert that no `NaN` volatility reaches the sizer and that no
      position exceeds the cap on any bar?
- [ ] Does the universe include listings that were later delisted or never graduated?
