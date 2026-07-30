# Pre-Flight Checklist

- [ ] Are market data metrics (stale time, sequence gaps, price spikes, crossed books) ingested?
- [ ] Is data quality score $Q \in [0, 100\%]$ calculated deterministically?
- [ ] Is Tier 1 (50% size haircut) triggered when $Q \in [70\%, 89\%]$?
- [ ] Is Tier 3 (EMERGENCY HALT & FLATTEN) triggered when $Q < 40\%$?