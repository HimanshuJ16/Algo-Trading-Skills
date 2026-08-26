# Workflows for Multi-Strategy Reporting Consolidation

## 0. Validate the telemetry before any aggregation

Every downstream figure is computed from the joint return series, so a defect in the
inputs is invisible in the output. Reject, do not repair:

- **Duplicate `strategy_id`** — the same sleeve would be counted twice in capital, PnL,
  and weights, silently halving the reported diversification.
- **Negative or non-finite `allocated_capital_usd`** — a negative weight breaks the
  $\text{DR} \ge 1$ property, which is defined for long-only weights only.
- **Non-finite PnL or returns** — a single `NaN` propagates into every headline figure.
- **A daily return below $-1.0$** — the compounded equity path turns negative and
  drawdown stops meaning anything. This is usually a unit error: a percent-scaled series
  (`-5.0` meaning $-5\%$) fed where decimals were expected.
- **Unequal return-series lengths** — see step 2.
- **Fewer than 2 observations** — a sample standard deviation does not exist.

## 1. Capital & PnL Aggregation

- $C_{\text{total}} = \sum_k C_k$; $\text{PnL}_{\text{total}} = \sum_k (\text{realized}_k + \text{unrealized}_k)$.
- `portfolio_return_pct` $= \text{PnL}_{\text{total}} / C_{\text{total}}$ is a **period**
  return on allocated capital. It is not annualized and is not the compounded return of
  the joint series.
- Per-sleeve `pnl_contribution_pct` is a share of total PnL. When total PnL is negative
  the sign inverts (a profitable sleeve shows a negative share); when it is exactly zero
  the shares are undefined and reported as `NaN`. Both cases raise a warning.

## 2. Align the return series by date — outside this engine

`daily_returns` is an untimestamped list, so the engine cannot align sleeves itself and
will not guess. Truncating every sleeve to the shortest one aligns by *index from the
start*: a sleeve that launched six months later is paired with the six-month-old
observations of the others, and the resulting covariance — and therefore the whole
diversification ratio — describes co-movement that never occurred. Join on dates
upstream, decide explicitly whether to use the intersection of dates or to exclude the
late-launch sleeve from the risk section, and pass equal-length series.

## 3. Joint Return & Volatility Synthesis

- $R_{p,t} = \sum_k w_k R_{k,t}$ with $w_k = C_k / C_{\text{total}}$ held fixed, i.e. a
  series rebalanced to the target allocation each period.
- $\sigma_p = \text{stdev}(R_{p,t}) \cdot \sqrt{F}$ using the sample $(n-1)$ standard
  deviation, with $F$ = `trading_days_per_year`. $F$ must match the sampling frequency of
  the returns; it cannot be inferred from a list of floats.
- If `observations < F` the window is shorter than a year and the annualized figures are
  extrapolations (GIPS 2020 Provision 2.A.12). A warning is emitted.

## 4. Sharpe & Diversification Audit

- $SR_p = (\text{mean}(R_{p,t}) \cdot F - R_f) / \sigma_p$.
- $\text{DR} = (\sum_k w_k \sigma_k) / \sigma_p$ (Choueifaty & Coignard 2008, Eq. 1).
- **If $\sigma_p$ is exactly zero**, both are undefined — not large, not zero. The engine
  returns `NaN` and records a warning. The degeneracy test is exact equality with zero:
  a tolerance would misclassify a genuinely low-volatility market-neutral book.
- Sleeve Sharpe ratios are standalone figures. Their average is not the portfolio Sharpe
  ratio and should never be presented as one.

## 5. Drawdown Consolidation

- `portfolio_max_drawdown_pct` is the peak-to-trough drawdown of the compounded joint
  equity path.
- `max_strategy_max_drawdown_pct` is the worst single sleeve **over the same window**,
  recomputed from that sleeve's returns so the comparison is like-for-like.
- `reported_max_drawdown_pct` on each contribution passes through whatever the sleeve's
  own telemetry claimed. Its window and methodology are unknown to this engine, so it is
  carried for traceability, not compared.
- The portfolio figure is typically below the worst sleeve figure but is not bounded by
  it in either direction — sleeves that trough on the same day compound.

## 6. Reconcile and Report

- Compare `portfolio_return_pct` (from booked PnL) against `series_implied_return_pct`
  (compounded joint series). They describe the same portfolio over the same window; a
  material gap means the PnL and the return series came from different books, systems, or
  date ranges. Resolve it before publishing either figure.
- Read `report.warnings` before quoting any number. `status` remains
  `REPORT_CONSOLIDATED_SUCCESS` even when individual metrics are qualified or `NaN` —
  the warnings, not the status, carry the caveats.
- Record which figures are gross and which are net before the report leaves the firm.
