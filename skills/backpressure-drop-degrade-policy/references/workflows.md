# Deep Workflow Reference — backpressure-drop-degrade-policy

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## 0. The caller's contract

`BackpressureManager.handle_full()` returns a `BackpressureDecision`:

| Field | Meaning |
|---|---|
| `accepted` | **Check this.** True if the item is in the queue or folded into a bar. |
| `action` | `ENQUEUED_AFTER_DROP`, `ENQUEUED_AFTER_SAMPLE`, `THROTTLED`, `AGGREGATED`, `REJECTED_OVERFLOW` |
| `emitted_item` | Completed OHLC bar under `DEGRADE`, else `None` |
| `rejected_item` | The item, when `accepted` is False — so it is never simply lost |
| `discarded_count` | Items evicted to make room |

`BackpressureDecision.__bool__` returns `accepted`, so `if not manager.handle_full(...)`
is a valid guard.

## Full Procedure

1. **Classify each data stream** by what backpressure response is acceptable:
   - **`DROP_OLDEST`:** only the latest state matters (e.g. latest LTP for a
     position-monitoring display). Evicts one oldest item, admits the new one.
   - **`SAMPLE`:** non-critical consumers (e.g. a dashboard chart). Admits 1 of
     every `sample_keep_every_n` items under overload and leaves the existing
     backlog intact. Throttling admission — *not* flushing the buffer.
   - **`DEGRADE`:** can fall back to a coarser representation. Folds ticks into
     fixed-interval OHLC bars via `TickAggregator` and emits completed bars.
   - **`NEVER_DROP`:** tied to risk decisions (position/margin feeding the
     kill-switch). At capacity it returns `accepted=False`, alerts, and invokes
     `on_never_drop_overflow`. It cannot store the item — that is the caller's
     problem, deliberately made visible.

2. **Declare every stream.** An undeclared stream raises `UnknownStreamError`.
   `strict_unknown_stream=False` restores the old permissive default, but then
   emits a warning alert on first use. Silently defaulting an unrecognised stream
   to a drop policy is how a mistyped risk-stream name becomes a data-loss path.

3. **Resource isolation by criticality tier:**
   - Never let a `NEVER_DROP` stream share a queue or worker pool with a
     `DROP_OLDEST` or `SAMPLE` stream. Contention lets low-priority tick bursts
     starve high-priority risk handlers.
   - Assign dedicated bounded queues and worker tasks per tier.

4. **Call `observe()` on every push.** `handle_full()` fires only once the queue
   is *already* full, which is too late to be an early warning. `observe()`
   records the push and emits a watermark alert at `high_watermark_pct`
   (default 80%), at `CRITICAL` severity for `NEVER_DROP` streams and `WARNING`
   for the rest.

5. **Alerting with cooldown protection:**
   - Pass a real out-of-band `alert_fn`; the default only writes a log warning,
     and SKILL.md requires more than a log line for risk streams.
   - `RateLimitedAlert` enforces a per-stream cooldown so a volatility spike
     cannot flood the alert sink. It uses a monotonic clock, so a wall-clock
     adjustment mid-session cannot suppress or duplicate alerts.
   - The alert callback is invoked **outside** the manager's lock, and an
     exception raised by the sink is logged rather than propagated — a failing
     alert service must never take down the trading pipeline.

6. **Post-session telemetry & review:**
   - `get_metrics_summary()` reports `total_observed`, `overflow_events`,
     `total_dropped`, `total_sampled`, `total_degraded`, `total_discarded`,
     `never_drop_overflows`, `alert_count`, `high_watermark_breaches`,
     `drop_rate_pct`.
   - `drop_rate_pct` is computed against `total_observed` (real pushes), not
     against overflow events. It is `None` — not `0.0` — when nothing was
     observed, because "no data" must not read as "healthy".
   - `never_drop_overflows` is the number that should be zero. Anything else is
     an incident.

## Threading model

- All state mutation happens under a re-entrant lock.
- Every pop is individually guarded. `deque` documents that individual appends
  and pops are thread-safe, but a *sequence* of them is not atomic: reading
  `len(queue)` and then popping that many times races with a concurrent consumer
  and raises `IndexError: pop from an empty deque`, killing the producer thread.
- A `deque(maxlen=N)` silently discards from the opposite end when appended to
  while full. That is an implicit drop-oldest policy chosen by the data
  structure, not by you. Route pushes through the manager.

## Worked Example

```python
import collections
from backpressure_policy import BackpressureManager, NEVER_DROP, DROP_OLDEST, SAMPLE

manager = BackpressureManager(
    policy_by_stream={"risk": NEVER_DROP, "ltp": DROP_OLDEST, "ui": SAMPLE},
    alert_fn=pager.send,                 # real out-of-band alerting
    high_watermark_pct=0.8,
    sample_keep_every_n=4,               # UI updates at 1/4 rate under load
    on_never_drop_overflow=kill_switch.trip,
)

risk_q = collections.deque(maxlen=10_000)   # dedicated queue, dedicated worker

def on_risk_message(msg):
    manager.observe("risk", risk_q)          # early warning before capacity
    if len(risk_q) == risk_q.maxlen:
        decision = manager.handle_full("risk", risk_q, msg)
        if not decision.accepted:
            escalate(decision.rejected_item)  # never silently discarded
    else:
        risk_q.append(msg)
```

## Failure Modes Observed in Production

- **Silent rejection:** an overflow handler that returns `None` for both success
  and rejection, so the caller cannot tell that risk data was dropped.
- **Defaulted stream policy:** a mistyped stream name inheriting a drop policy.
- **Crash on the overflow path:** unguarded repeated `popleft()` raising
  `IndexError` against a live consumer and killing the WebSocket read loop.
- **Backlog flush mislabelled as sampling:** discarding a fraction of the whole
  queue per overflow event rather than throttling admission.
- **Generic single queue:** one bounded queue for all data types, accepting
  whatever the library does when full (commonly blocking the caller).
- **Zero-filled ticks:** a missing price silently becoming `0.0` inside an OHLC
  bar during degraded mode.
- **Alert flooding:** calling a raw alert hook on every dropped tick during a
  crash, triggering alert-service rate limits.

## Production Implementation Reference

- `scripts/backpressure_policy.py` — `BackpressureManager`, `BackpressureDecision`,
  `TickAggregator`, `RateLimitedAlert`, `StreamMetrics`, and the error types
  `UnknownStreamError` / `NeverDropOverflowError`.
- `scripts/test_backpressure_policy.py` — unit tests covering policy execution,
  rejection visibility, guarded pops, and telemetry semantics.
- `references/standards.md` — queue semantics relied upon, and regulatory scope.

## Notes for Agent Implementers

- Treat every numbered step as a checkpoint. Skipping resource isolation,
  `observe()`, rate-limited alerting, or telemetry is how production backpressure
  failures happen.
- Never describe a `NEVER_DROP` stream as guaranteed lossless. The guarantee is
  that loss is *surfaced*, not that it cannot occur — if the consumer cannot keep
  up, something has to give, and the point is that you find out.
- Verify queue depth metrics during paper-trading replay at multiples of peak
  historical tick rate before promoting to live trading.
