# Deep Workflow Reference — tick-buffering-burst-handling

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Empirical Peak-Rate Capacity Sizing:**
   - Calculate buffer capacity via `BurstBufferManager.calculate_empirical_capacity(peak_ticks_per_sec, max_lag_sec)`.
   - Size buffer capacity to handle peak high-volatility sessions (opening 15 minutes, index expiry).

2. **Per-Symbol Buffer Isolation & Overwrite Semantics:**
   - Maintain isolated `deque(maxlen=capacity)` buffers per instrument symbol.
   - Use `KEEP_LATEST_N` overwrite semantics for latest-price strategy logic (dropping oldest tick when full).

3. **Structured Drop Audit Logging:**
   - Record `TickDropRecord` (timestamp, symbol, dropped_tick, occupancy_pct) for every buffer drop event for post-session audit analysis.

4. **High-Water Mark Occupancy Monitoring:**
   - Track `high_water_mark_pct` and output `get_occupancy_report()` to monitor buffer saturation under volatility bursts.

## Failure Modes Observed in Production

- **Average-Rate Buffer Sizing:** Sizing buffers for average-day tick rates, causing massive drop rates during market volatility bursts.
- **Unbounded Memory Buffers:** Making buffers unbounded to avoid data loss, converting backpressure into host Out-Of-Memory (OOM) crashes.
- **Silent Tick Drops:** Discarding overflow ticks without logging drop records, obscuring signal calculation errors.
- **Conflating Buffers with Backlogs:** Treating a sustained multi-second backlog as a buffer sizing problem rather than escalating to `backpressure-drop-degrade-policy`.

## Production Implementation Reference

- Reference code: `scripts/burst_buffer.py` (`BurstBufferManager`, `DropStrategy`, `TickDropRecord`).
- Automated unit tests: `scripts/test_burst_buffer.py`.
