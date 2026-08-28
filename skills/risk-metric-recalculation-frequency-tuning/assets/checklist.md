# Pre-Flight Checklist — Risk Metric Recalculation Frequency Tuning

## Scope separation (do this before anything else)

- [ ] Has every risk computation been split into "per-order, never tiered" and "tierable analytics"?
- [ ] Are credit/capital threshold checks, fat-finger price and size limits, position caps that gate order entry, duplicate-order checks and execution reporting **outside** the tier table? (17 CFR 240.15c3-5(c)(1)-(c)(2))
- [ ] For any computation that appears on both lists, is the per-order gate reading a published value, with a documented maximum staleness for that value?
- [ ] EU/UK only: does any tiered metric feed a disorderly-trading alert? If so, is its cadence inside the five-second bound of RTS 6 Article 16(5), or is the alert fed from a separate always-on path?

## Tier configuration

- [ ] Has the wall-clock cost of each tierable metric been **measured** on production-sized data (not estimated)?
- [ ] Is every `accelerated_interval_sec` comfortably longer than the measured cost of that metric?
- [ ] Are all Tier 1 intervals `0.0`, so the config cannot claim a cadence the scheduler ignores?
- [ ] Is `accelerated_interval_sec <= base_interval_sec` for every metric?
- [ ] Are the chosen intervals recorded as house defaults with a rationale, rather than presented as regulatory minima?

## Driving the scheduler

- [ ] Is `evaluate_due_metrics` driven by a wall-clock heartbeat **in addition to** market-data ticks, so a stalled feed cannot silently freeze every risk number?
- [ ] Does the heartbeat run at least as fast as the shortest non-tier-1 interval?
- [ ] Do all timestamps come from one non-decreasing clock (`time.monotonic()` live, event timestamps in replay)?
- [ ] Is the P&L passed in a single currency, converted before the call?
- [ ] Does the caller actually execute every metric named in `metrics_due_for_calc`, and handle the case where one of them fails after the scheduler has already marked it calculated?

## Velocity and acceleration

- [ ] Is `pnl_velocity_threshold_usd_per_sec` sized against this book's own P&L distribution rather than left at the $500/s default?
- [ ] Is `min_velocity_sample_sec` long enough that tick jitter cannot manufacture a velocity spike at your tick rate?
- [ ] Is hysteresis enabled (`acceleration_exit_ratio < 1.0` and a non-zero dwell), so one quiet sample mid-crash cannot restore a 300 s stress cadence?
- [ ] Has it been confirmed that entering accelerated mode forces the heavy metrics due **on the detecting call**, not one interval later?
- [ ] Is it understood that P&L velocity does not detect gamma, correlation or concentration build-up in a flat market?

## Monitoring and reporting

- [ ] Is `overdue_metrics` alerted on, not just logged?
- [ ] Has a deliberate feed-gap drill been run to confirm the staleness flag fires on the next evaluation?
- [ ] Is `calculation_load_reduction_pct` reported as an invocation ratio (or a cost-weighted ratio using **measured** `relative_cost_units`) and never as a CPU-cycle benchmark?
- [ ] Is it understood that the reduction figure is cumulative since construction and therefore lags recent behaviour?

## Governance

- [ ] Are the tier intervals, velocity threshold and hysteresis parameters recorded in the risk-control documentation for the annual review under § 15c3-5(e)(1)?
- [ ] Is there a documented owner for re-measuring metric costs when the position count or scenario grid grows materially?
- [ ] Is it explicit that this scheduler changes *when* risk is measured and never *what happens on a breach* — the breach action belongs to the kill switch?
