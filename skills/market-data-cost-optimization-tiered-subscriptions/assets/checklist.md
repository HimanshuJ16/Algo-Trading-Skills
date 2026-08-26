# Pre-Flight Checklist

## Cost model
- [ ] Is the invoice split into symbol-metered vs fixed (per-firm / per-subscriber / non-display / connectivity) spend?
- [ ] Is `tier_monthly_costs_usd` supplied from the firm's own contract, rather than the illustrative `TIER_COSTS` placeholder?
- [ ] Is `fixed_monthly_platform_cost_usd` supplied, so the reported reduction is not quoted against the metered slice alone?
- [ ] Are any per-symbol charge **caps** identified, so the addressable saving is not overstated past the cap?

## Safety
- [ ] Does every symbol with an open position **or** a live signal end on TIER1 or TIER2 — never TIER3 delayed/EOD?
- [ ] Is `has_active_signal` current as of the audit date, not a stale flag?
- [ ] Is every promotion covered by the required venue licence and subscriber classification before it is applied?

## Correctness
- [ ] Does the audit raise on an unrecognised tier, duplicate symbol, or negative day count rather than defaulting it?
- [ ] Is `days_since_last_trade=None` (never traded) handled as stale, not as recent?
- [ ] Is `min_days_before_demotion` set to at least one billing period, given that fees are not prorated?

## Reporting
- [ ] Is `total_savings_percentage_including_fixed` the figure quoted externally, not `savings_percentage`?
- [ ] Is a `NET_COST_INCREASE` result reported as a coverage-driven increase rather than as "already optimal"?
- [ ] Was the next invoice reconciled against the projected optimized spend?
