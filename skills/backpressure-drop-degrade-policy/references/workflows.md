# Deep Workflow Reference — backpressure-drop-degrade-policy

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Classify each data stream** by what backpressure response is acceptable:
   - **Drop-oldest (`DROP_OLDEST`):** for streams where only the latest state matters (e.g., latest LTP for a position-monitoring display) — safe to discard stale ticks in favor of the newest.
   - **Sample/throttle (`SAMPLE`):** for streams feeding non-critical downstream consumers (e.g., a dashboard chart) — reduce update frequency under load (e.g. discard 50% oldest queue items) rather than dropping entirely.
   - **Degrade to lower-resolution data (`DEGRADE`):** for streams that can fall back to a coarser representation (e.g., switch from tick-level to 1-second OHLC aggregation via `TickAggregator`) under sustained load rather than processing every individual tick.
   - **Never drop (`NEVER_DROP`):** for streams tied directly to risk decisions (e.g., position/margin updates feeding the kill-switch) — these must never silently degrade; if they cannot keep up, this is a system health emergency requiring an alert with cooldown protection, not a quiet policy application.

2. **Implement explicit policy handlers per stream** via `BackpressureManager` rather than letting generic queue library defaults (blocking or throwing unhandled QueueFull exceptions) decide.

3. **Resource Isolation by Criticality Tier:**
   - Never let a `NEVER_DROP` stream share a queue or worker thread pool with a `DROP_OLDEST` or `SAMPLE` stream. Resource contention between them can allow low-priority tick bursts to starve high-priority risk handlers.
   - Assign dedicated bounded queues (e.g. `asyncio.Queue` or `collections.deque`) and worker tasks per tier.

4. **Alerting with Cooldown Protection:**
   - Emit high-priority alerts when any `NEVER_DROP` stream reaches capacity or when any stream breaches high watermarks (e.g., 80% capacity).
   - Enforce an alert rate-limiter (e.g. `RateLimitedAlert`) to prevent alert flooding during market volatility spikes.

5. **Post-Session Telemetry & Review:**
   - Record per-stream metrics (`total_pushed`, `total_dropped`, `total_sampled`, `total_degraded`, `alert_count`, `high_watermark_breaches`, `drop_rate_pct`).
   - Call `get_metrics_summary()` at trading session close to verify whether chosen policies matched real market conditions.

## Failure Modes Observed in Production

- **Generic Single Queue:** Using a single generic bounded queue for all data types and accepting whatever the library does by default when full (commonly: blocking the caller, which halts the WebSocket read loop).
- **Misclassified Risk Streams:** Applying a "safe to drop" policy to a risk-relevant stream by failing to separate position/margin updates from general tick feeds.
- **Silent Degradation:** Quietly downgrading processing without alerting or telemetry logging, causing live strategy performance to diverge from backtest without warning.
- **Alert Flooding:** Calling raw alert hooks on every dropped tick during a market crash, leading to thousands of API calls and potential alert service rate-limit lockouts.

## Production Implementation Reference

Refer to `scripts/backpressure_policy.py` for the reference implementation of `BackpressureManager`, `TickAggregator`, `RateLimitedAlert`, and `StreamMetrics`.
Refer to `scripts/test_backpressure_policy.py` for the unit test suite verifying policy execution under load.

## Notes for Agent Implementers

- Treat every numbered step above as a checkpoint, not a suggestion — skipping a step
  (especially resource isolation, rate-limited alerting, or telemetry logging) is how production backpressure failures occur.
- Verify queue depth metrics during paper trading replay at 5x historical tick rate before promoting to live trading.
