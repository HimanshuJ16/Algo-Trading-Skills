# Pre-Flight / Sign-off Checklist — producer-consumer-tick-pipeline

Use this before considering the skill's implementation complete.

- [ ] **Zero-Blocking Callback:** Confirm `on_message` performs no synchronous processing, and measure its execution time on the target host rather than assuming a budget.
- [ ] **Thread-Correct Handoff:** Confirm which thread the broker SDK calls back on, and that a non-loop callback uses `submit_threadsafe` / `loop.call_soon_threadsafe` rather than a direct `put_nowait`.
- [ ] **Bounded Handoff:** Confirm `max_pending_handoffs` is set so the cross-thread path cannot grow without limit.
- [ ] **Stable Partitioning:** Confirm `SymbolPartitionedTickPipeline` routes a symbol's ticks to the same worker, and that the mapping is identical across two process launches with different `PYTHONHASHSEED` values.
- [ ] **Bounded Queue Protection:** Confirm queues use explicit `maxsize` limits $\ge 1$ (0 means unbounded) and that dropped ticks are counted.
- [ ] **Drop Log Throttling:** Confirm a saturated queue does not emit one log line per dropped tick.
- [ ] **Failure Isolation:** Confirm an exception in `process_fn` is counted in `total_ticks_failed`, leaves the worker consuming, and does not leave `queue.join()` hanging.
- [ ] **Telemetry Monitoring:** Confirm `PipelineMetrics` tracks queue depth, queue wait time, processing latency, drops, failures, and undrained ticks — and that queue wait is monitored, not just processing latency.
- [ ] **Graceful Shutdown:** Confirm `stop_consumers(drain=True)` processes the queued backlog and that anything discarded at timeout is reported.
- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/producer-consumer-tick-pipeline/scripts` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
