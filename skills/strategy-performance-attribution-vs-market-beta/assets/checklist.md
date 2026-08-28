# Pre-Flight Checklist — Strategy Performance Attribution vs Market Beta

Sign off before any alpha figure leaves the desk.

## Input conventions

- [ ] All return series are **decimal fractions**, not percent (a 0.53% day is `0.0053`).
- [ ] Ken French factor columns were **divided by 100** before use.
- [ ] `market_returns_are_excess` is set correctly — `True` for French's `Mkt-RF`, which
      is already net of the risk-free rate; `False` only for a total-return market series.
- [ ] The risk-free rate was **not** subtracted from SMB or HML (they are zero-investment
      spreads and the engine leaves them raw — confirm nothing upstream did it first).
- [ ] Strategy returns are **net** of fees, financing and borrow cost.
- [ ] The benchmark is the one the strategy actually trades against, not the nearest
      convenient index.
- [ ] The index is unique, and sorted chronologically if HAC is to be used.

## Sample integrity

- [ ] `observations` matches the window you intended to analyse.
- [ ] `dropped_observations` is explained — a short factor history truncates the whole
      regression, market beta included.
- [ ] `insufficient_history_warning` is `False`, or the annualized figures are labelled
      as provisional and not published.
- [ ] The window contains more than one market regime, or the single-regime limitation
      is stated alongside the result.

## Model and inference

- [ ] Fama-French SMB and HML are included for any equity strategy; their omission is
      justified in writing for any strategy where they are left out.
- [ ] `standard_error_type` is recorded with the result, and `"hac"` was used if the
      strategy holds illiquid or stale-marked assets, runs overlapping holding periods,
      or is trend/momentum.
- [ ] `hac_lags` is either the documented default or an explicitly justified horizon.
- [ ] Significance was judged against `t_critical_value`, **not** a hardcoded 1.96.
- [ ] `alpha_t_statistic is None` was read as "undefined (perfect fit)", never as zero;
      `undefined_metrics` was checked.
- [ ] If several strategies were screened, a multiple-testing correction was applied
      outside this engine.

## Reconciliation

- [ ] `abs(unexplained_residual_pct) < 1e-8` — the additive decomposition closes.
- [ ] `risk_free_contribution_pct + annualized_jensens_alpha_pct + Σ return_contribution_pct`
      reconstructs `total_realized_annual_return_pct` to within rounding.
- [ ] `alpha_percentage_of_total_return` was reported as `None` (not 0.0) whenever the
      total return is zero or negative.

## Interpretation

- [ ] Market beta contribution is reported separately from alpha; a high return under a
      high beta is not presented as skill.
- [ ] A high $R^2$ was **not** used as an argument that alpha is absent — $R^2$ describes
      variance, alpha describes the mean.
- [ ] An insignificant alpha was reported as *unproven*, not as *zero*.
- [ ] The alpha claim states the model (CAPM or FF3), the window, the sample size, the
      standard-error type and the $p$-value — enough for someone else to reproduce it.
