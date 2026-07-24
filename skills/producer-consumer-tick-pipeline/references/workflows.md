# Deep Workflow Reference — producer-consumer-tick-pipeline

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Zero-Blocking WebSocket Callback:**
   - Restrict `on_message` WebSocket callbacks strictly to message reception and `put_nowait()` queue pushes ($\le 0.1 \text{ms}$).
   - Disallow signal processing, ML inference, or database writes inside the socket callback.

2. **Symbol-Hash Work Partitioning:**
   - Partition tick distribution across $N$ worker queues using `hash(symbol) % N`.
   - Guarantee strict in-order tick sequence processing for any single instrument without cross-thread race conditions.

3. **Bounded Queue Backpressure Protection:**
   - Size worker queues with explicit maximum bounds (`maxsize`).
   - Track dropped ticks (`total_ticks_dropped`) and feed metrics into `backpressure-drop-degrade-policy`.

4. **Pipeline Telemetry Monitoring:**
   - Expose `PipelineMetrics` tracking queue depth, max queue depth, processing latency, and throughput ticks/sec.

## Failure Modes Observed in Production

- **In-Callback Strategy Execution:** Executing ML inference or DB writes inside `on_message`, causing socket buffer overruns and connection drops during market spikes.
- **Round-Robin Worker Partitioning:** Partitioning worker tasks using round-robin, introducing out-of-order tick processing for individual symbols.
- **Unbounded Memory Queues:** Allowing queues to grow infinitely, causing silent host Out-Of-Memory (OOM) crashes during volatility bursts.

## Production Implementation Reference

- Reference code: `scripts/tick_pipeline.py` (`SymbolPartitionedTickPipeline`, `PipelineMetrics`).
- Automated unit tests: `scripts/test_tick_pipeline.py`.
