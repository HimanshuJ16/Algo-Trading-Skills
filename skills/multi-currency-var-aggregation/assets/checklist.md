# Pre-Flight / Sign-off Checklist — multi-currency-var-aggregation

## Input data
- [ ] Position quantities and prices are in **native** currency; `fx_rate_to_base` is base units per one native unit.
- [ ] Base-currency positions carry `fx_rate_to_base` of exactly `1.0`.
- [ ] An FX return series exists for **every** non-base currency in the book — none are zero-filled by default.
- [ ] The FX return series is the return of the **base-per-native** rate; the quoting direction has been checked by hand against one known move.
- [ ] The base currency's FX series is omitted or identically zero.
- [ ] All asset and FX series are aligned to the same observation dates and have the same length.
- [ ] Every series ends at the last **completed** period before the valuation date (no look-ahead).
- [ ] Non-finite (`NaN`/`inf`) returns, prices, quantities and FX rates are rejected, not silently propagated.

## Sample adequacy
- [ ] Observation count $\ge \lceil 1/(1-\alpha) \rceil$ (20 at 95%, 100 at 99%) so the tail bucket holds at least one loss.
- [ ] For a regulatory-facing measure, at least one year of history — 12 CFR 217.205(b)(2).
- [ ] A zero-variance P&L series has been investigated as stale data, not accepted as a risk-free book.

## Calculation
- [ ] Joint base return synthesised by compounding asset and FX returns, not by converting values and using asset volatility alone.
- [ ] Portfolio P&L aggregated on position **values**, so a hedged/market-neutral book with ~zero net value is still measurable.
- [ ] Series indexed by position, not by symbol — multiple lots of one instrument are not collapsed.
- [ ] Parametric VaR, Historical Simulation VaR and Expected Shortfall (CVaR) all reported.
- [ ] Historical VaR is the $\lceil n(1-\alpha) \rceil$-th worst loss and ES the mean of those same $k$ losses; $\text{ES} \ge \text{VaR}$ verified.
- [ ] The ceiling is computed with a floating-point epsilon (`ceil(100 * (1 - 0.95))` is 6, not 5).
- [ ] The drift convention (`subtract_mean_drift`) is the same for the parametric and historical figures being compared.
- [ ] `holding_period_scaled` is surfaced whenever $\sqrt{T}$ scaling was applied.

## Reporting
- [ ] `currency_risk_breakdown` (net exposure) is never presented as a risk contribution.
- [ ] `currency_component_var_base` is reported for the per-currency risk view, and its sum reconciles to `parametric_var_base`.
- [ ] Negative component VaR values are understood as genuine hedges and checked against the FX quoting direction.
- [ ] `observations_used`, `tail_observations_used` and `confidence_level` are carried through to the audit record.

## Scope
- [ ] No options, convertibles or other convex payoffs are in the book — both branches here are linear.
- [ ] The output is treated as an internal risk measure, not a regulatory capital number; MAR33.4(5) forbids the $\sqrt{T}$ scaling that § 217.205(b)(1) permits.
- [ ] The parametric-vs-historical gap at 99% has been reviewed as a tail-shape signal rather than assumed to be noise.

## Testing
- [ ] Automated testing: `python -m unittest discover -s skills/multi-currency-var-aggregation/scripts` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
