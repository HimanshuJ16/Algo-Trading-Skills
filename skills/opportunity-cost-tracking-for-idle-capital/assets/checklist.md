# Pre-Flight / Sign-off Checklist — opportunity-cost-tracking-for-idle-capital

## Input data
- [ ] `allocated_capital + unallocated_cash` reconciles to `total_capital` within tolerance.
- [ ] `unallocated_cash` is non-negative — a negative balance is a margin debit, not idle capital.
- [ ] `benchmark_rate_pct` is a rate **observed for the relevant date**, pulled from the FRBNY SOFR publication, not a hardcoded constant.
- [ ] `cash_yield_pct` is set to the rate the idle cash **already earns** (broker credit interest / current sweep yield). Leaving it at $0.0$ is a deliberate assertion, not a default to ignore.
- [ ] Both rate fields are in **percent** ($5.25$, not $0.0525$).
- [ ] `holding_period_days` is positive and reflects the actual idle duration.

## Measurement
- [ ] Day count matches how the rate is quoted: **ACT/360** for SOFR / SOFR Averages / SOFR Index / T-bills; ACT/365F for SONIA or coupon Treasury yields.
- [ ] Drag is computed on the **net spread** ($r_{\text{benchmark}} - r_{\text{cash}}$), not the full benchmark.
- [ ] Period drag and annualized drag are reported separately and never conflated in downstream reporting.
- [ ] Gross drag is measured on the full idle balance, including the operational buffer, which costs yield even though it cannot be swept.
- [ ] If `DAILY_COMPOUNDED` is used, it is understood to be a calendar-day approximation of the business-day SOFR Index.

## Sweep decision
- [ ] `operational_buffer_usd` is set from the actual margin-call and settlement profile — a buffer of $0$ asserts none is needed.
- [ ] `sweep_transaction_cost_usd` is the **all-in round-trip** cost (out and back), not one leg.
- [ ] `sweepable_cash_usd` (not the raw idle balance) is what the min-sweep threshold is applied to.
- [ ] `breakeven_sweep_notional_usd` has been reviewed against the policy threshold — if breakeven exceeds the threshold, the threshold is too low.
- [ ] Redemption timing of the sweep destination is compatible with the strategy's liquidity needs (a T+1 fund cannot fund an intraday margin call).
- [ ] `sweep_blocked_reason` is surfaced on every `MAINTAIN_IDLE_CASH` result so the decision is auditable.

## Configuration
- [ ] `target_idle_ratio_max` is a **fraction** ($0.05$ = 5%), not a percentage.
- [ ] Defaults (5% idle cap, \$100k threshold, \$50 round-trip cost) have been calibrated and the rationale recorded — they are library defaults, not industry standards.
- [ ] It is understood that SOFR is a benchmark, not an achievable yield; the real sweep destination's net yield and fees have been checked separately.
- [ ] Tax treatment of the sweep yield has been considered — the reported net gain is pre-tax.

## Testing
- [ ] Automated Testing: Run `python scripts/test_opportunity_cost_tracking_for_idle_capital.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
