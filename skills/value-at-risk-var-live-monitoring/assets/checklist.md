# Pre-Flight / Sign-off Checklist — value-at-risk-var-live-monitoring

Use this before considering the skill's implementation complete.

## Inputs and alignment

- [ ] **Series alignment:** All return series are the same length AND indexed to the
      same observation dates, oldest first. Ragged input is rejected, not truncated —
      confirm the caller aligns on date, not on length.
- [ ] **No look-ahead:** Every series ends at the last *completed* period before the
      valuation instant. The module cannot check this.
- [ ] **Shorts as negative quantities**, never negative prices. Confirm a negative price
      raises `VaRMonitorError`.
- [ ] **Coverage:** Every held symbol has both a price and a return series. Confirm a
      missing one raises rather than silently dropping the position's exposure.
- [ ] **Sample size:** $n \ge \lceil 1/(1-c) \rceil$ (100 at 99%), and ideally
      $\ge 252$ — the one-year floor of BCBS MAR32.18 / 12 CFR 217.205(b)(2). Confirm
      the short-sample warning is being logged and read, not swallowed.

## Estimator correctness

- [ ] **Parametric VaR:** $z_c \sigma_p - \mu_p$ with $z_c$ from an exact normal
      quantile function. Confirm no lookup table with a silent fallback default.
- [ ] **Historical VaR:** the $k$-th worst loss with $k = \lceil n(1-c) \rceil$.
      Verify on the reference sample: 100 observations whose four worst are −10%, −8%,
      −6%, −4% give $k=1$ and VaR 10.00% at 99%; at 95%, $k=5$ and VaR 0.00%.
- [ ] **CVaR / Expected Shortfall:** mean of those same $k$ worst. Same sample at 95%
      gives 5.58%. Confirm $\text{CVaR} \ge \text{VaR}$ always holds.
- [ ] **$k$ is visible:** `tail_observations_used` is surfaced. When $k=1$, CVaR equals
      VaR by definition — confirm nobody is reading that as a tail-severity signal.
- [ ] **Leverage:** `gross_exposure_pct` $= \sum |w_i|$ matches the book's actual gross
      exposure; a 3x book shows 3.0 and ~3x the VaR of the 1x book.

## Fail-closed behaviour

- [ ] **Non-finite input:** NaN/Inf in any price, quantity, return or NAV raises
      `VaRMonitorError`. Confirm no path reaches a `NaN >= limit` comparison, which is
      `False` and would approve every order.
- [ ] **Single exception type:** every rejection is a `VaRMonitorError`. Confirm no bare
      `KeyError` or `AttributeError` can escape past the caller's guard.
- [ ] **Configuration validated at construction:** `confidence_level` in $(0.5, 1)$,
      limits strictly positive. Confirm `confidence_level=1.5` raises rather than
      producing a negative quantile index that reads the profit tail.
- [ ] **NAV:** non-positive or non-finite NAV raises; the margin/liquidation path is
      what handles zero equity, not the VaR monitor.

## Breaker behaviour

- [ ] **Breach is inclusive** at $\ge$ limit, and verified at the exact boundary.
- [ ] **Attribution:** `breaching_measures` and `binding_var_pct` name the measure that
      actually tripped. Confirm a historical-only breach does not log the parametric
      figure as the breaching value.
- [ ] **CVaR limit:** either `cvar_limit_pct` is deliberately set, or it is a conscious
      decision that Expected Shortfall is reported but not enforced.
- [ ] **Risk-reducing orders pass:** `is_risk_reducing=True` approves through a live
      breach with `breach_reason` retained and `risk_reducing_override=True`. Confirm
      the caller's classification of "reduces exposure" is correct — the module trusts it.
- [ ] **Out-of-band:** the breaker runs outside strategy logic, and the caller enforces
      the verdict. This module submits and cancels nothing.

## Validation

- [ ] **Automated tests:** run
      `python -m unittest discover -s skills/value-at-risk-var-live-monitoring/scripts`
      and confirm all 31 pass.
- [ ] **Exceedance validation:** the limit's implied breach rate has been checked
      against realised outcomes — see `real-time-var-backtesting-kupiec-test`. At 99%
      one-day, 2–3 exceedances per year are *expected*, not a model failure.
- [ ] **Scope confirmed:** the book contains no options or other convex payoffs, which
      this delta-normal measure does not capture.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
