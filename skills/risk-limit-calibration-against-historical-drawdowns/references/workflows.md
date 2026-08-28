# Workflows for Risk Limit Calibration Against Historical Drawdowns

The full procedure. `SKILL.md` carries the condensed version and the decision points.

## 1. Assemble and validate the return series

1. Collect **daily fractional returns on account equity** (`0.02` = +2%), chronologically
   ordered, ending at the last completed session. Convert currency P&L to returns on
   equity *before* passing them in; compounding dollars produces a meaningless equity
   curve.
2. Confirm the window covers at least one adverse regime. A calibration window drawn
   entirely from a bull market produces a limit that is valid only in a bull market.
   Basel's analogue for banks requires the stressed observation horizon to reach back to
   2007 (MAR33.6) — the principle transfers even though the rule does not.
3. Pass the series to `compute_drawdown_metrics` or `calibrate_risk_limits`. Validation
   is not optional and not configurable:
   - non-finite returns are rejected — a `NaN` reaching the daily loss limit makes the
     limit `NaN`, and `loss > NaN` is `False` for every loss;
   - returns `<= -1.0` are rejected — they drive equity through zero, after which every
     drawdown figure is arithmetic on a sign-flipped equity curve;
   - fewer than `min_observations` (default 252, hard floor 126) is rejected;
   - a confidence level the window cannot support is rejected at construction time.
4. If validation raises, **fix the data**. Do not widen the tolerances to get a number.

## 2. Measure the realized path

`compute_drawdown_metrics` returns:

- `max_drawdown_pct` — deepest peak-to-trough decline of the compounded equity curve.
- `max_drawdown_duration_days` — longest run of consecutive observations closing
  *strictly* below the running peak. A day closing exactly at the peak is not
  underwater.
- `drawdown_unrecovered` — true when the series ends below its peak. The duration is
  then right-censored: it is a lower bound on the recovery time, not the recovery time.
  Report it as such.
- `ulcer_index` — root mean square of the percentage drawdowns from the running peak
  (Martin & McCann). Penalises deep and long drawdowns together, unlike max drawdown
  which sees only the deepest point.
- `mean_daily_return_pct`, `daily_volatility_pct`, `volatility_annualized`.
- `var_pct`, `cvar_pct` — historical (order-statistic) VaR and Expected Shortfall at
  `confidence_level_pct`. With `n` observations and confidence `q`,
  `k = ceil((1-q) * n)` tail observations are used. ES >= VaR by construction.

A `var_pct` of `0.0` is a real result: the sample holds no loss at that confidence.
`calibrate_risk_limits` raises rather than issuing a `$0` daily loss limit from it.

## 3. Choose the calibration method deliberately

The three methods measure different quantities. Record which one was used and why;
`limit_basis` on the result states it in prose for the audit file.

| Method | Quantity | Assumes | Use when |
|---|---|---|---|
| `HISTORICAL_MAX_DD` | Observed peak-to-trough drawdown x stress buffer | Nothing beyond the sample being representative | The default. The sample contains a regime you consider representative of the risk ahead. |
| `PARAMETRIC_VAR` | `h`-day cumulative loss quantile, `-h*mu + z_q*sigma*sqrt(h)` | IID normal returns | You want a limit that does not depend on whether the worst drawdown happened to land inside the window. Note this is a *lower bound* on the drawdown over a window of length `h`. |
| `EXTREME_VALUE_THEORY` | POT/GPD tail ES, scaled to `h` days by `sqrt(h)` | A GPD tail above the threshold; IID for the horizon step | The sample's tail is visibly fatter than normal and there are enough exceedances (default: 10% of the sample, at least 25) to fit one. |

**Drift scales with `h`, volatility with `sqrt(h)`.** Scaling a one-day VaR — which
already contains `-mu` — by `sqrt(h)` mis-scales the drift term and inflates the limit
on any strategy with meaningful positive drift.

## 4. Fit the tail, or find out that you cannot

`fit_gpd_left_tail` is callable on its own and is worth running before committing to
`EXTREME_VALUE_THEORY`:

1. Losses are sorted descending; the threshold `u` is the `(N_u + 1)`-th largest, so
   exactly `N_u = int(n * tail_fraction)` losses exceed it.
2. Excesses `y_i = loss_i - u` are fitted by method of moments:
   `xi = (1 - mean^2/var)/2`, `beta = mean*(1 + mean^2/var)/2`.
3. `VaR_q = u + (beta/xi)*(((n/N_u)(1-q))^(-xi) - 1)`, with the `xi -> 0` exponential
   limit handled separately; `ES_q = VaR_q/(1-xi) + (beta - xi*u)/(1-xi)`.

It raises — deliberately, rather than degrading to another method — when:

- there are fewer than `min_exceedances` exceedances;
- the excesses are degenerate (all tied with the threshold, or zero variance);
- the requested confidence sits *below* the fitted threshold's own exceedance rate,
  where the POT formula would extrapolate into a region it was not fitted on;
- the fitted shape implies an infinite-mean tail (`xi >= 1`).

Inspect `shape_xi` before trusting the number. `xi > 0` is a heavy tail; `xi < 0` a
bounded one. A warning is logged at `xi >= 0.25`, past which the method-of-moments
estimator's own variance is not finite, and the estimator can never report `xi >= 0.5`
at all — on a genuinely infinite-variance tail it understates.

## 5. Apply the policy band and read the binding flags

The raw figure is clipped into `[drawdown_limit_floor_pct, drawdown_limit_cap_pct]` for
**every** method — a floor applied to only one method lets the others emit a `0%` limit
that halts the strategy on its first tick.

- `floor_binding` true: a benign sample was raised to the policy floor. The limit is
  policy, not a measurement.
- `cap_binding` true: the strategy's measured risk exceeds the largest limit this engine
  will issue. That is a signal about the strategy, not a calibration result. Both are
  logged as warnings, not silently applied.

## 6. Derive the remaining limits

- **Daily loss limit** = `capital x var_pct x daily_loss_var_multiple`, computed from the
  unrounded VaR. Rounding the VaR to two decimals before multiplying by capital moves a
  \$1m limit by over \$100.
- **Position scalar** = `position_scalar_threshold_pct / max_drawdown_pct` when the
  observed drawdown exceeds the threshold, else `1.0`.

## 7. Hand the record to review, then enforce it elsewhere

`CalibratedRiskLimits` is self-contained for an audit file: it carries the full
`metrics`, the `tail_fit` when EVT was used, `limit_basis`, `horizon_days`, both binding
flags and `audit_notes`. Take it to the risk committee with the window, the regime it
covers, and the policy parameters that were chosen.

Then enforce the numbers somewhere else. Calibration is an offline research step; the
runtime control that halts trading must be independent of strategy logic — see
`kill-switch-and-drawdown-circuit-breakers` and
`portfolio-level-stop-loss-independent-of-strategy-stops`. Recalibrate on a stated
cadence and after any material change in strategy or market regime; RTS 6 Art. 9's
annual self-assessment is a floor for firms in scope, not a target.
