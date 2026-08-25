# Deep Workflow Reference — graceful-shutdown-draining-in-flight-ticks

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Size the drain budget first**:
   - Read the supervisor's grace period (`terminationGracePeriodSeconds`,
     `docker stop --timeout`, `TimeoutStopSec=`).
   - `T_max_drain = resolve_drain_timeout(grace, pre_stop_sec, exit_overhead_sec)`.
   - On Kubernetes, subtract the `preStop` sleep: the countdown starts before the
     hook runs and the hook completes before `SIGTERM` arrives.
   - If the realistic drain exceeds the budget, raise the grace period. Raising
     only the drain timeout guarantees a mid-flush `SIGKILL`.

2. **OS signal registration** (main thread of the main interpreter):
   - Register handlers for `SIGTERM` (container/systemd termination) and `SIGINT`
     (keyboard interrupt); `register_signal_handlers()` returns `False` if the
     platform or thread will not allow it.
   - Treat `False` as a failed deployment gate, not a warning.
   - The handler records the signal and sets `is_shutdown_requested`; a second
     signal sets `force_immediate_exit` so an operator can abandon a wedged drain.

3. **Transition state and close ingress**:
   - Set state to `DRAINING`; ingress callbacks consult `is_accepting_ingress()`
     and reject new external market ticks.
   - Because EndpointSlice removal is concurrent with `SIGTERM`, expect a tail of
     arriving ticks; a `preStop` sleep is what actually drains the load balancer.

4. **Drain in-flight queue and flush sinks**:
   - Detach a batch atomically (under the producers' lock when one exists), call
     the sink, and only then consider the batch drained.
   - On a sink exception, restore the batch at the head of the queue, back off by
     `retry_interval_sec`, and retry until the monotonic deadline.
   - Stop when the queue is empty, the deadline expires, or immediate exit is
     requested. Items still queued at the end are reported, never discarded silently.

5. **Commit offsets after — and only after — a complete flush**:
   - Full drain: run `commit_offsets_callback`, yielding at-least-once semantics.
   - Incomplete drain: skip the commit so the restart replays the unflushed ticks.
   - A commit that itself fails marks the exit dirty; the data is durable but the
     restart will duplicate.

6. **Clean exit checkpoint**:
   - `ShutdownReport.exit_code` is `0` only for a fully drained, committed shutdown;
     `1` otherwise. Propagate it to `sys.exit()` so the orchestrator records the event.
   - Log `initial_queue_size`, `drained_items_count`, `undrained_items_count`,
     `flush_failure_count` and `offsets_committed` for post-deploy forensics.

## Worked Example — Kubernetes

With `terminationGracePeriodSeconds: 30` and a 5 s `preStop` sleep:

```
T_max_drain = resolve_drain_timeout(30.0, pre_stop_sec=5.0, exit_overhead_sec=1.0)
            = 24.0 seconds
```

```yaml
spec:
  terminationGracePeriodSeconds: 30
  containers:
    - name: tick-worker
      lifecycle:
        preStop:
          exec:
            command: ["/bin/sh", "-c", "sleep 5"]
```

The 5 s `preStop` lets EndpointSlice removal propagate before `SIGTERM` is
delivered; the remaining 24 s covers the drain, the offset commit and interpreter
exit before `SIGKILL`.

## Failure Modes to Rehearse

| Injected failure | Expected outcome |
|---|---|
| Sink raises on every write | Items stay queued, `exit_code=1`, offsets uncommitted |
| Sink fails twice then succeeds | Drain completes, order preserved, offsets committed |
| Drain exceeds deadline | Loop stops, remainder retained and reported |
| Second `SIGINT` during drain | Drain abandoned immediately, `exit_code=1` |
| Handlers registered off main thread | `register_signal_handlers()` returns `False` |
| Offset commit raises after good flush | `is_clean_exit=False`, data durable, replay expected |

## Production Implementation Reference

- Reference code: `scripts/graceful_shutdown.py`
  (`GracefulShutdownManager`, `ShutdownState`, `ShutdownReport`,
  `resolve_drain_timeout`, `PLATFORM_GRACE_PERIOD_DEFAULTS_SEC`).
- Automated unit tests: `scripts/test_graceful_shutdown.py`.
