---
name: backpressure-drop-degrade-policy
description: Use when a real-time pipeline's consumers fall persistently behind producers
  and the system needs an explicit, chosen drop/sample/degrade policy per stream rather
  than an accidental one inherited from a queue library default
domain: algorithmic-trading
subdomain: real-time-architecture
tags:
- real-time-architecture
- backpressure
- queue-overflow
- drop-policy
- degradation
- tick-pipeline
brokers_frameworks:
- Python asyncio.Queue / collections.deque
- ZeroMQ
- Apache Kafka / Redis Streams
- RxPY / ReactiveX
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this once `tick-buffering-burst-handling` has established bounded buffers — this skill defines what happens when a buffer is persistently at capacity, i.e., sustained backpressure rather than a momentary burst. Every real-time trading system eventually falls behind producers; the difference between a robust system and a fragile one is whether that moment is handled by a deliberate, chosen policy or by whatever the underlying queue/library happens to do by default (often "block the producer" — the worst outcome, since it propagates the slowdown back to the WebSocket read loop).

## When NOT to Use

- **For momentary bursts.** A short spike that a bounded buffer absorbs is a buffering problem, not a policy problem — see `tick-buffering-burst-handling`.
- **When the consumer is merely slow-to-start.** Fix the consumer or scale it out before deciding what to throw away; a drop policy applied to a fixable bottleneck permanently degrades data quality to paper over a solvable issue.
- **As a substitute for capacity planning.** If a stream is *always* at its watermark, the policy is masking undersized infrastructure — see `capacity-planning-for-symbol-universe-growth`.
- **For order/execution acknowledgements.** Those are not a droppable telemetry stream; reconciliation, not sampling, is the correct response to falling behind.

## Prerequisites

- Bounded buffers already in place (see `tick-buffering-burst-handling`)
- A per-data-type criticality classification: which data can be dropped, sampled, or degraded, and which cannot

## The Caller's Contract

`handle_full()` returns a `BackpressureDecision`. **Check `decision.accepted`.**

A `NEVER_DROP` stream at capacity returns `accepted=False` with the item in `decision.rejected_item` — the manager cannot store it for you and will not pretend it did. Treating that return as "handled" silently discards risk-critical data, which is precisely the failure this skill exists to prevent. Wire `on_never_drop_overflow` to your emergency path, or set `strict_never_drop=True` to raise.

## Workflow

1. Classify each data stream by what backpressure response is acceptable:
   - **Drop-oldest:** for streams where only the latest state matters (e.g., latest LTP for a position-monitoring display) — safe to discard stale ticks in favor of the newest.
   - **Sample/throttle:** for streams feeding non-critical downstream consumers (e.g., a dashboard chart) — admit 1 of every N items under load, leaving the existing backlog intact. Throttling admission is not the same as flushing the buffer: discarding half the queue on every overflow destroys far more data than the load actually requires.
   - **Degrade to lower-resolution data:** for streams that can fall back to a coarser representation (e.g., tick-level to 1-second OHLC) under sustained load.
   - **Never drop:** for streams tied directly to risk decisions (e.g., position/margin updates feeding the kill-switch). If they cannot keep up, that is a system health emergency requiring an alert and an explicit rejection the caller must handle — not a quiet policy application.
2. Declare **every** stream's policy explicitly. An undeclared stream raises `UnknownStreamError` by default rather than falling back to a drop policy — a mistyped risk-stream name must not silently become a data-loss path.
3. Call `observe()` on every push, not just on overflow. `handle_full()` alone cannot warn you early, because by the time it runs the queue is already full — for a `NEVER_DROP` stream that is already too late.
4. Never let a "never drop" stream share a queue/thread pool with a "safe to drop" stream — resource contention means the safe-to-drop stream's load can degrade the never-drop stream's latency. Isolate by criticality tier, mirroring the tier separation in `multi-broker-rate-limit-handling`.
5. Emit an explicit alert (not just a log line) when any "never drop" stream approaches capacity. Pass a real out-of-band `alert_fn`; the default only writes a log warning.
6. Record, per backpressure event, which policy fired and what was dropped/degraded/throttled, so post-session review can assess whether the chosen policies matched what actually happened.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring the return value.** If `handle_full()` returns "nothing to do" for both a successful drop-oldest and a rejected risk item, the caller cannot distinguish them and the risk item is lost. Check `accepted`.
- **A bounded deque is already an implicit policy.** `collections.deque(maxlen=N)` silently discards from the opposite end when you append to a full deque — Python documents this. If you append directly, you have chosen drop-oldest by accident. Route pushes through the manager.
- **Compound queue operations are not atomic.** `deque` guarantees individual appends and pops are thread-safe, but reading `len(queue)` and then popping that many times races with a concurrent consumer and raises `IndexError: pop from an empty deque` — crashing the producer thread, which is usually the WebSocket read loop. Guard every pop.
- **Defaulting an unknown stream.** Applying a "safe to drop" policy to a risk-relevant stream, usually because position/margin updates were lumped in with general market data or a stream name was mistyped.
- **Flushing the backlog in the name of "sampling."** Discarding a fixed fraction of the queue per overflow event is a different, far more destructive behavior than reducing update frequency.
- **Silent degradation with no alerting** — a system that quietly downgrades from tick-level to OHLC-level processing may be technically "working" while producing materially different signals than the strategy was validated against, and nobody notices until live performance diverges from backtest.
- **Zero-filling missing tick fields.** Substituting `0.0` for an absent price corrupts every OHLC bar built from it. Reject the tick instead.
- **Misleading telemetry.** A drop rate computed against overflow events rather than total pushes reads near 100% under load and tells you nothing; a rate of `0.0` when nothing was observed reads as healthy when it means "no data".
- **Treating backpressure policy as a one-time decision** rather than something to revisit as strategies and data volumes change.

## Verification

- Run `python -m unittest discover -s skills/backpressure-drop-degrade-policy/scripts` — 100% pass rate (38 tests).
- Under sustained simulated overload (replay at multiples of peak historical tick rate), confirm risk-critical streams return `accepted=False` and trigger the defined alert rather than silently discarding.
- Confirm an undeclared stream name raises `UnknownStreamError` instead of being assigned a drop policy.
- Confirm the overflow path does not raise when a consumer drains the queue concurrently.
- Confirm dashboard/non-critical streams throttle admission while leaving the existing backlog intact.
- Confirm `get_metrics_summary()` reports drop rates against observed pushes, and `None` (not `0.0`) when nothing was observed.

## Related Skills

- `producer-consumer-tick-pipeline`
- `tick-buffering-burst-handling`
- `kill-switch-and-drawdown-circuit-breakers`
- `graceful-degradation-priority-during-partial-outage`
- `capacity-planning-for-symbol-universe-growth`
