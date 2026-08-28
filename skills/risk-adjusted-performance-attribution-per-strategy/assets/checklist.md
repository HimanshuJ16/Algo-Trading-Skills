# Pre-Flight / Sign-off Checklist — risk-adjusted-performance-attribution-per-strategy

## Input data
- [ ] Return series are net of fees, commissions and financing.
- [ ] All series are **equal length and position-aligned** on the same periods, in the same order (the engine has no timestamps and cannot detect a misalignment that is not ragged).
- [ ] Non-finite returns (`NaN`/`inf`) are resolved upstream, not passed in.
- [ ] Every return is strictly greater than $-1.0$ (a return at or below $-1.0$ implies zero or negative equity).
- [ ] Portfolio weights match the strategy count and sum to $1.0$.
- [ ] Observation count meets the reporting horizon being claimed; `insufficient_history_warning` is checked before any annualized figure is published.

## Metric configuration
- [ ] Risk-free rate / MAR is current for the evaluation period and **identical across strategies** being compared (per-strategy overrides make cross-strategy Sortino ratios incomparable).
- [ ] The risk-free rate is de-annualized geometrically, not as $r_f/252$.
- [ ] The annualization factor (252) matches the sampling frequency of the returns.
- [ ] If a Calmar ratio will be compared to a published figure, the window is 36 months (Young's convention).

## Interpreting ratios
- [ ] `None` ratios are handled explicitly wherever strategies are sorted or ranked — never coerced to $0.0$.
- [ ] `undefined_ratios` is read to learn *why* a ratio is absent (zero volatility / no observation below the MAR / no drawdown).
- [ ] Both upside and downside measures (Sharpe **and** Sortino) are evaluated together, alongside max drawdown.
- [ ] It is understood that downside deviation from a mostly-positive discrete sample understates tail risk (Sortino & Forsey 1996).

## Risk decomposition
- [ ] `risk_contribution_pct` (Euler) is used for attribution — **not** `standalone_volatility_share_pct`, which is correlation-blind and only a measure of gross scale.
- [ ] Negative risk contributions are preserved as diversification benefits, not clipped to zero and not ranked by absolute value.
- [ ] `risk_decomposition_available` is checked; when false, portfolio volatility is zero and contributions are genuinely undefined.
- [ ] Contributions are confirmed to sum to ~100% before the report is circulated.

## Scope
- [ ] These are realized, backward-looking statistics for the supplied window, not forecasts.
- [ ] Weight *selection* is handled elsewhere (`risk-parity-allocation-across-strategies`); this engine measures the weights already in place.
- [ ] Drawdown *enforcement* is handled by an independent control (`kill-switch-and-drawdown-circuit-breakers`); this is a reporting engine.

## Testing
- [ ] Automated Testing: Run `python scripts/test_risk_adjusted_attribution.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
