# Pre-Flight / Sign-off Checklist — backtest-reporting-standardized-tearsheet

Use this before considering the skill's implementation complete.

## Input Contract

- [ ] **Returns are simple, not log.** The equity curve compounds as $\prod(1+R_t)$.
- [ ] **Returns array has no missing (NaN) values** — and gaps were resolved at source, not filled with 0.0 by default. Filling lowers volatility and raises Sharpe.
- [ ] **One consistent frequency** across the whole series; no mixed daily/intraday rows.
- [ ] **No return below $-100\%$.** If the strategy can blow past zero equity, drawdown and CAGR are undefined and the model needs fixing first.
- [ ] **Sample long enough to mean something.** `Annualization Extrapolated` is False, or the projection is labelled as such wherever the tearsheet is shown.

## Convention Settings

- [ ] **Correct `periods_per_year` setting for the given frequency** — 252 daily, 52 weekly, 12 monthly. Not 365 for trading days.
- [ ] **Risk-free rate on the same annual basis** as `periods_per_year`.
- [ ] **Calmar convention chosen deliberately** and `Calmar Convention` carried into any comparison table.
- [ ] **Sortino divisor confirmed** to be the total period count, not the count of losing periods.

## Metric Sanity

- [ ] **Drawdown measured from starting capital.** A series that opens with a loss must report that loss.
- [ ] **Drawdown cross-checked** against an independent peak-to-trough scan on at least one real series.
- [ ] **Depth, duration and recovery all read together**, not depth alone.
- [ ] **Degenerate values understood:** $\pm\infty$ and `nan` mean a zero denominator, not a bad or broken strategy.
- [ ] **`Hit Rate` reported as a per-period figure**, never relabelled as a trade win rate.

## Before the Tearsheet Leaves the Desk

- [ ] **Provenance attached:** frequency, `periods_per_year`, risk-free rate, Calmar convention, observation count, sample start/end, and whether costs and slippage were modelled.
- [ ] **Selection bias addressed** if the parameters came from a search — the reported Sharpe is then the maximum of many trials.
- [ ] **Distribution reviewed.** If this goes into a US investment adviser's advertisement it is hypothetical performance under 17 CFR § 275.206(4)-1 and the (d)(6) conditions apply. Confirm the requirements in your own jurisdiction.

## Testing

- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/backtest-reporting-standardized-tearsheet/scripts` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Frequency, `periods_per_year`, $r_f$, Calmar convention, sample range: ___________________________
