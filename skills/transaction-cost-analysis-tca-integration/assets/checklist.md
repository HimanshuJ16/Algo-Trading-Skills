# Pre-Flight / Sign-off Checklist — transaction-cost-analysis-tca-integration

Use this before considering the skill's implementation complete.

## Measurement

- [ ] **Realized shortfall reads the fill.** Confirm `realized_shortfall_bps` changes when `p_fill` changes, and that it is measured against $P_{\text{decision}}$ in bps.
- [ ] **Side signs verified.** Confirm a SELL whose price fell between decision and fill reports a **positive** (adverse) delay and realized cost.
- [ ] **Estimate and realization are differenced, not summed.** Confirm `model_error_bps` $=$ realized $-$ estimated and that no downstream code adds the two totals.

## Cost model

- [ ] **Component decomposition.** Confirm delay, half-spread, market impact, commission, and opportunity cost are each isolated and separately attributable.
- [ ] **Sqrt impact scaling.** Confirm quadrupling $\text{Size}/\text{ADV}$ exactly doubles the impact estimate.
- [ ] **Participation range.** Confirm `participation_out_of_model_range` is set outside $[10^{-5}, 0.1]$ and that participation is **not** clamped.
- [ ] **Gamma is calibrated, not inherited.** Confirm $\gamma$ was fitted with `suggest_market_impact_gamma` on your own fills, per liquidity bucket and volatility regime — not left at the placeholder default.
- [ ] **Passive flow.** Confirm any maker-heavy strategy is judged on realized cost, since the estimate charges the half-spread unconditionally.
- [ ] **No double-counted fees.** Confirm `fixed_commission_bps` excludes anything the broker already nets into the fill price.

## Aggregation

- [ ] **Capital base supplied.** Confirm `evaluate_portfolio_tca` receives the capital that produced the gross return, in the same currency as the prices.
- [ ] **Drag is notional-based.** Confirm the return drag scales with traded value, not trade count — 1,000 odd-lot trades must not move the net return materially.
- [ ] **Viability uses the notional-weighted figure.** Confirm `notional_weighted_shortfall_bps`, not the equal-weighted mean, gates `is_strategy_viable`.
- [ ] **Unpriced misses accounted for.** Confirm `unpriced_opportunity_trades` is zero, or that `net_tca_return_pct` is explicitly treated as an optimistic bound.

## Robustness

- [ ] **Bad input fails loudly.** Confirm non-positive ADV, non-positive prices, NaN/Inf, unknown `action`, over-fills, and non-positive `capital_base` all raise rather than returning a plausible number.
- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/transaction-cost-analysis-tca-integration/scripts` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
