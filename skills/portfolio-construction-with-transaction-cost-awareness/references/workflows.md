# Workflows for Portfolio Construction with Transaction Cost Awareness

1. **Input Validation**:
   - Reject empty asset lists, duplicate symbols, non-finite weights or expected returns,
     empty symbols, negative cost parameters, and a negative rebalance threshold.
   - Reject $|w| > 10$ as a percent-vs-fraction input error: a weight of `40` instead of
     `0.40` inflates the quadratic impact term by four orders of magnitude.
2. **No-Trade Buffer Band Filtering**:
   - Suppress rebalancing trades where the proposed weight change is within the buffer
     threshold. The band is inclusive — exactly at the threshold means suppress.
   - On a breach, apply the configured policy: snap to target (default), or move only to
     the nearest band boundary (`trade_to_band_edge=True`, the proportional-cost optimum).
3. **Transaction Cost Calculation**:
   - Price the **executed** delta, never the proposed one.
   - Compute proportional commission, proportional spread, and quadratic market impact.
   - Costs are fractions of portfolio value, directly comparable to the weighted return.
4. **Turnover Audit**:
   - Compute two-way turnover (L1 norm) and derive one-way as half of it.
   - Compare the two-way figure against `max_turnover_limit`; set `turnover_limit_breached`.
   - Return the plan unclamped — the limit is advisory, and the caller decides which
     trades to drop.
5. **Budget / Self-Financing Audit**:
   - Compare the current and final weight sums. Report `net_weight_change` and
     `is_self_financing`; a non-zero net change is a cash funding leg that must be routed.
6. **Net Utility Evaluation**:
   - Net expected return $=$ weighted return on **final** weights $-$ total transaction cost.
   - Never credit target weights for a suppressed asset.
7. **Audit Report Generation**:
   - Output the structured `TCAwarePortfolioReport`, including the executable
     `final_weights` vector and per-asset `trade_decisions` with the cost breakdown.
