# Deep Workflow Reference — tick-buffering-burst-handling

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Empirical Peak-Rate Capacity Sizing:**
   - Calculate buffer capacity via `BurstBufferManager.calculate_empirical_capacity(peak_ticks_per_sec, max_lag_sec)`.
   - Size from a peak measured on the feed you will actually consume, during a known
     high-volatility window (opening 15 minutes, index expiry) — not from a venue-wide
     consolidated figure and not from an average-day rate. See `references/standards.md`
     for what is and is not published per venue.
   - `max_lag_sec` is a lag *tolerance*, not a safety margin: raising it buys queueing
     delay on every downstream decision, not reliability.
   - Invalid inputs (zero, negative, NaN, infinite) raise `BurstBufferConfigError` rather
     than silently returning the `DEFAULT_MIN_CAPACITY` floor as though it were derived.

2. **Per-Symbol Buffer Isolation & Overwrite Semantics:**
   - Maintain isolated `deque(maxlen=capacity)` buffers per instrument symbol.
   - Use `KEEP_LATEST_N` overwrite semantics for latest-price strategy logic (dropping the
     oldest unconsumed tick when full); reserve `DROP_NEWEST_LOG` for logic that must not
     have its tick sequence reordered by an eviction it cannot see.
   - Capacities are validated at construction. A capacity of `0` is rejected: it previously
     produced a buffer that accepted every tick, retained none, counted no drops and
     reported 0% occupancy — total silent data loss behind a clean audit trail.
   - `custom_capacities` keys are normalised exactly as pushed symbols are, so a lowercase
     override is no longer ignored in silence.

3. **Structured Drop Audit Logging:**
   - Record `TickDropRecord` (timestamp, symbol, drop strategy, dropped tick, capacity,
     occupancy) for buffer drop events for post-session audit analysis.
   - The record ring is **bounded** (`drop_log_capacity`). An unbounded audit list is
     itself an OOM vector — a saturated buffer drops on every push, so one record per drop
     grows without limit and pins each dropped tick object, precisely during the burst the
     skill is meant to survive.
   - Exact totals live in `drop_counts` / `total_drops`, which are integer counters and
     never lose information. Persist records off-process if a full forensic trail is
     required by your record-keeping obligations.
   - Overflow warnings are rate-limited per symbol (`min_warn_interval_sec`) and carry an
     aggregate count, so the log write does not become the next bottleneck.

4. **High-Water Mark & Loss Occupancy Monitoring:**
   - Track `high_water_mark_pct` and output `get_occupancy_report()` to monitor buffer
     saturation under volatility bursts. The high-water mark survives draining — it is the
     sizing signal for the next session.
   - The report also carries `offered`, `accepted`, `dropped` and `drop_rate_pct` per
     symbol. Occupancy alone cannot distinguish a buffer that merely ran hot from one that
     overflowed, and "how much data did this burst cost us?" is the question the audit
     actually has to answer.
   - `drop_rate_pct` is taken against `offered` (one count per `push()`), never against
     `accepted + dropped`. Under `KEEP_LATEST_N` every push is accepted and a drop is the
     later eviction of a tick already counted as accepted, so summing the two
     double-counts the burst and understates the loss rate.

5. **Concurrent Consumption:**
   - `push()` (feed/callback thread) and `pop_oldest()` / `drain()` (strategy thread) are
     safe to call concurrently; all state mutation is under one manager-wide lock.
   - Prefer `drain()` over a read-`len()`-then-pop loop: the loop races a concurrent
     consumer and pops from an emptied buffer.
   - Read paths never create a buffer for an unseen symbol, so a monitoring loop over a
     rotating universe cannot grow manager state without bound.

## Failure Modes Observed in Production

- **Average-Rate Buffer Sizing:** Sizing buffers for average-day tick rates, causing massive drop rates during market volatility bursts.
- **Unbounded Memory Buffers:** Making buffers unbounded to avoid data loss, converting backpressure into host Out-Of-Memory (OOM) crashes.
- **Unbounded Audit Logs:** Bounding the buffer but not the drop log, relocating the OOM
  from the data path into the telemetry path.
- **Silent Tick Drops:** Discarding overflow ticks without logging drop records, obscuring signal calculation errors.
- **Unsynchronised Buffer Creation:** Creating per-symbol buffers with an unlocked
  check-then-create, so two threads racing on a new symbol each build a `deque` and the
  losing thread's ticks are discarded while `push()` reports success.
- **Conflating Buffers with Backlogs:** Treating a sustained multi-second backlog as a buffer sizing problem rather than escalating to `backpressure-drop-degrade-policy`.

## Production Implementation Reference

- Reference code: `scripts/burst_buffer.py` (`BurstBufferManager`, `DropStrategy`, `TickDropRecord`, `BurstBufferConfigError`).
- Automated unit tests: `scripts/test_burst_buffer.py`.
