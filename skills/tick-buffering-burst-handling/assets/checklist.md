# Pre-Flight / Sign-off Checklist — tick-buffering-burst-handling

Use this before considering the skill's implementation complete.

- [ ] **Empirical Capacity Sizing:** Confirm buffer capacity is calculated using `calculate_empirical_capacity()` from a peak tick rate measured on the feed actually consumed — not an average-day rate and not a venue-wide published peak.
- [ ] **Aggregate Memory Bound:** Confirm (per-symbol capacity × symbol count × tick size) has been checked against host RAM, not just the per-symbol bound.
- [ ] **Configuration Rejected, Not Degraded:** Confirm a zero/negative capacity and a mis-cased `custom_capacities` key both raise `BurstBufferConfigError` instead of silently discarding ticks or applying the default.
- [ ] **Bounded Memory Plateaus:** Confirm memory usage under sustained peak-rate replay plateaus rather than growing unbounded — for the drop log and telemetry structures as well as the buffers.
- [ ] **Exact Drop Accounting:** Confirm `drop_counts` / `total_drops` report the exact number of lost ticks even though `drop_logs` retains only the most recent records.
- [ ] **Structured Drop Audit Logging:** Confirm overflow drop events record a `TickDropRecord` containing timestamp, symbol, dropped tick, capacity, and occupancy.
- [ ] **Rate-Limited Alerting:** Confirm sustained saturation produces rate-limited aggregate warnings, not one log line per dropped tick.
- [ ] **Loss Attribution in Reporting:** Confirm `get_occupancy_report()` reports peak occupancy plus per-symbol `offered` / `accepted` / `dropped` / `drop_rate_pct`, with the drop rate taken against ticks offered rather than `accepted + dropped`.
- [ ] **Thread Safety:** Confirm concurrent `push()` and `drain()`/`pop_oldest()` conserve every tick (buffered + consumed + dropped == pushed), asserting on totals rather than absence of exceptions.
- [ ] **Non-Mutating Reads:** Confirm read accessors for unseen symbols create no buffer state.
- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/tick-buffering-burst-handling/scripts` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
