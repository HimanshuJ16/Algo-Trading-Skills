# Pre-Flight / Sign-off Checklist — dynamic-position-sizing-based-on-realized-volatility

## Input data
- [ ] Return history ends at the last **completed** observation before the bar being sized (no look-ahead).
- [ ] Non-finite returns (`NaN`/`inf`) are rejected before estimation, not floored into maximum leverage.
- [ ] History length meets the estimator's effective window ($\lambda = 0.94 \Rightarrow 74$ daily returns at 1% tolerance).
- [ ] `annualization_factor` matches the sampling frequency (252 for daily; not 252 for intraday bars).

## Estimation
- [ ] EWMA / Rolling StdDev annual volatility estimation active, and the choice is fixed for the strategy.
- [ ] It is understood that EWMA (zero-mean) and rolling (sample-mean, $n-1$) will not agree on identical data.
- [ ] Reported `realized_annualized_vol` is the unfloored estimate; `vol_used_for_scaling` carries the floored denominator.

## Sizing
- [ ] Volatility targeting scalar $\frac{\sigma_{\text{target}}}{\max(\sigma_{\text{floor}}, \sigma_{\text{realized}})}$ computed.
- [ ] Min/Max scalar bounding limits ($0.20\times$ – $2.0\times$) enforced, and it is known which of the floor or the cap actually binds.
- [ ] `vol_floor_binding` is surfaced when the size was set by the floor rather than by measured volatility.
- [ ] Share count is floored, never rounded up above the risk budget.
- [ ] `base_capital_usd` is non-negative and `price` is positive and finite.

## Scope
- [ ] An independent drawdown/kill-switch control exists — vol targeting is backward-looking and does not protect against gaps.
- [ ] Portfolio-level correlation is handled elsewhere; this sizer is per-asset.
- [ ] Defaults (15% target, $2.0\times$ cap, $0.20\times$ floor, 5% vol floor) have been calibrated and the rationale recorded — they are library defaults, not industry standards.

## Testing
- [ ] Automated Testing: Run `python -m unittest discover -s skills/dynamic-position-sizing-based-on-realized-volatility/scripts` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
