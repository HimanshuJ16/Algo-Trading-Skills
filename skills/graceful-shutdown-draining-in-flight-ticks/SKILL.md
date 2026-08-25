---
name: graceful-shutdown-draining-in-flight-ticks
description: Use when shutting down or redeploying trading microservices (Kubernetes
  SIGTERM, systemd restart) to trap termination signals, stop new tick ingestion,
  drain in-flight queues, flush database sinks, and commit consumer offsets.
domain: algorithmic-trading
subdomain: real-time-architecture
tags:
- real-time-architecture
- graceful-shutdown
- sigterm
- queue-drain
- data-loss-prevention
- deployment-safety
brokers_frameworks:
- Graceful Shutdown Manager
- Python Signal Handler
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when operating live trading engines and market data workers subject to planned deployments, rolling restarts, or container terminations (`SIGTERM`). Abruptly killing worker threads drops in-flight tick events, leaves database batch writes incomplete, and leaves message-consumer offsets in a state that either replays or skips data on restart. This skill traps OS termination signals, closes the ingress gate, drains queued ticks to the sink, commits offsets in the correct order, and exits with a deterministic status code.

## When NOT to Use

- **Open orders or live positions are in flight.** This skill drains *data* queues, not *order state*. A process holding working orders needs an explicit unwind/cancel decision first — see `strategy-decommissioning-and-position-unwind-procedure` and `execution-algorithm-kill-switch-integration`.
- **Unplanned failure.** Power loss, OOM-kill and `SIGKILL` deliver no signal and run no handler. Durability under those conditions comes from sink write-ahead behaviour and offset semantics, not from this skill.
- **The queue is the system of record.** If ticks are only in process memory, a drain timeout still loses them. Bound the exposure upstream with `backpressure-drop-degrade-policy`.

## Prerequisites

- Ingestion pipeline with queue worker threads or async event loops.
- A shutdown entry point on the **main thread of the main interpreter** — Python executes signal handlers only there, and `signal.signal()` raises `ValueError` if called from any other thread.
- Known supervisor grace period, because it — not the process — decides when `SIGKILL` lands. Defaults: Kubernetes `terminationGracePeriodSeconds` **30s**, `docker stop` **10s** (Linux containers), systemd `DefaultTimeoutStopSec` **90s**.
- Max drain timeout $T_{\text{max\_drain}}$ derived from that grace period, not picked arbitrarily: use `resolve_drain_timeout()`.

## Workflow

1. **Register OS Signal Traps** (main thread only):
   - Intercept `SIGINT` (Ctrl+C) and `SIGTERM` (K8s/Docker/systemd shutdown).
   - Check the return value. If registration fails you are running unsupervised — fail the deployment rather than logging and continuing.
   - The handler must only set flags. Do not flush, write or block inside it.

2. **Size the Drain Budget Against the Supervisor**:
   - Compute $T_{\text{max\_drain}}$ = grace period − preStop − exit reserve. On Kubernetes the grace-period countdown starts *before* the preStop hook runs and the hook must finish before `SIGTERM` is delivered, so preStop time comes out of the same budget.
   - If the drain needs longer than the platform default, raise `terminationGracePeriodSeconds` — do not raise the drain timeout alone, or `SIGKILL` will cut the drain mid-flush.

3. **Close the Ingress Gate**:
   - Transition state to `DRAINING` and reject new external ticks via `is_accepting_ingress()`.
   - Do not assume the signal means traffic has stopped: Kubernetes removes the Pod from EndpointSlices *at the same time* as the kubelet starts graceful shutdown, so ticks can still arrive for as long as endpoint removal takes to propagate to kube-proxy and load balancers. A `preStop` sleep is what actually closes that window.

4. **Drain In-Flight Queue Buffers**:
   - Process remaining items until the queue reaches 0 or the deadline expires, measured on a **monotonic** clock.
   - Remove items from the queue only after the sink accepts them, so a failed flush is retried rather than discarded.
   - If producer threads are still touching the queue, detach batches under the lock those producers hold.

5. **Flush Sinks, Then Commit Offsets — In That Order**:
   - Commit consumer offsets only after the sink flush has fully succeeded. Flush-then-commit gives at-least-once (a restart replays, possibly duplicating). Commit-then-flush gives at-most-once — a crash in between silently loses those ticks forever.
   - If the drain was incomplete, **skip the commit**. Uncommitted offsets are what make the unflushed ticks recoverable on restart.

6. **Deterministic Process Exit**:
   - Exit `0` only on a fully drained queue with committed offsets; exit `1` on an incomplete drain, so the orchestrator and post-deploy checks can see the data-loss event.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reporting a clean exit after the sink refused the data.** Popping a batch off the queue and *then* calling the sink means a raising flush callback destroys that batch — the queue is empty, so a naive `is_clean_exit = len(queue) == 0` check reports success while every in-flight tick is gone. Remove items only after the write is accepted.
- **Committing offsets before the flush succeeds.** This converts a recoverable restart into permanent, silent data loss, and it is invisible in logs because both operations "succeeded" independently.
- **Timing the drain with `time.time()`.** Wall clock steps under NTP correction, DST and VM resume. A backward step extends the drain past the grace period into `SIGKILL`; a forward step aborts a healthy drain early. Use `time.monotonic()`.
- **Assuming `SIGTERM` means traffic has already stopped.** On Kubernetes endpoint removal is concurrent with the signal, not before it, so new work can still arrive immediately after the handler fires.
- **Registering handlers from a worker thread.** `signal.signal()` raises `ValueError` outside the main thread of the main interpreter, and Python runs handlers only in that thread. Swallowing that error leaves the process with no graceful path at all.
- **A drain timeout larger than the platform grace period.** A 60s drain under the 30s Kubernetes default never completes; the process is killed mid-flush every deploy.
- **Unbounded drain waiting.** Blocking forever on a stuck worker guarantees a hard kill and loses more than a bounded drain would.
- **No escalation path.** An operator watching a wedged drain needs a second `SIGINT` to force exit rather than waiting out the timeout.
- **Long C-level calls delaying the handler.** Python runs handlers at bytecode boundaries, so a long-running C call (large regex, blocking native driver call) defers shutdown until it returns.

## Verification

- Enqueue 50 items, trigger a simulated `SIGTERM`, verify all 50 reach the downstream sink before exit and the queue is empty.
- With a sink callback that always raises, verify the items remain **queued**, `is_clean_exit` is `False`, `exit_code` is `1`, and offsets are **not** committed.
- With a sink that fails twice then succeeds, verify the drain recovers within the deadline and tick order is preserved.
- Verify `resolve_drain_timeout()` rejects a budget the grace period cannot cover.
- Verify handler registration fails cleanly (returns `False`) when attempted off the main thread.
- Run `python -m unittest discover -s skills/graceful-shutdown-draining-in-flight-ticks/scripts` and confirm a 100% pass rate.

## Related Skills

- `producer-consumer-tick-pipeline`
- `backpressure-drop-degrade-policy`
- `kafka-based-tick-distribution-at-scale`
- `systemd-supervision-for-trading-bots`
- `blue-green-deployment-for-live-strategy-updates`
- `strategy-decommissioning-and-position-unwind-procedure`
- `adaptive-batch-size-tuning-under-load`
- `structured-logging-for-post-incident-forensics`
