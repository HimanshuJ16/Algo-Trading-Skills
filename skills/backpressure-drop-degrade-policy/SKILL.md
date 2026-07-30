---
name: backpressure-drop-degrade-policy
description: Use when a real-time pipeline's consumers fall persistently behind producers
  and the system needs an explicit, chosen policy rather than an accidental one
domain: algorithmic-trading
subdomain: real-time-architecture
tags:
- real-time-architecture
brokers_frameworks: []
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this once `tick-buffering-burst-handling` has established bounded buffers — this skill defines what happens when a buffer is persistently at capacity, i.e., sustained backpressure rather than a momentary burst. Every real-time trading system eventually falls behind producers at some point; the difference between a robust system and a fragile one is whether that moment is handled by a deliberate, chosen policy or by whatever the underlying queue/library happens to do by default (which is often "block the producer" — the worst outcome, since it propagates the slowdown back to the WebSocket read loop).

## Prerequisites

- Bounded buffers already in place (see `tick-buffering-burst-handling`)
- A per-data-type criticality classification: which data can be dropped, sampled, or degraded, and which cannot

## Workflow

1. Classify each data stream by what backpressure response is acceptable:
   - **Drop-oldest:** for streams where only the latest state matters (e.g., latest LTP for a position-monitoring display) — safe to discard stale ticks in favor of the newest.
   - **Sample/throttle:** for streams feeding non-critical downstream consumers (e.g., a dashboard chart) — reduce update frequency under load rather than dropping entirely.
   - **Degrade to lower-resolution data:** for streams that can fall back to a coarser representation (e.g., switch from tick-level to 1-second OHLC aggregation) under sustained load rather than processing every individual tick.
   - **Never drop:** for streams tied directly to risk decisions (e.g., position/margin updates feeding the kill-switch) — these must never silently degrade; if they cannot keep up, this is a system health emergency requiring an alert, not a quiet policy application.
2. Implement the chosen policy explicitly per stream rather than letting a generic queue library's default behavior (often blocking or exception-on-full) decide unintentionally.
3. Never let a "never drop" stream share a queue/thread pool with a "safe to drop" stream — resource contention between them means the safe-to-drop stream's load can degrade the never-drop stream's latency. Isolate resources (separate queues, separate worker pools) by criticality tier, mirroring the tier separation in `multi-broker-rate-limit-handling`.
4. Emit an explicit alert (not just a log line) when any "never drop" stream approaches its buffer capacity — this is a signal that the system is nearing a state where risk-critical logic cannot keep up with market data, which is materially different from a dashboard being a few seconds stale.
5. Record, per backpressure event, which policy fired and what was dropped/degraded/throttled, so post-session review can assess whether the chosen policies were appropriate for what actually happened.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Using a single generic bounded queue for all data types and accepting whatever the library does by default when full (commonly: block the caller, which is the worst option for anything upstream of a WebSocket read loop).
- Applying a "safe to drop" policy to a risk-relevant stream by not distinguishing it clearly enough at design time — this usually happens when position/margin updates are lumped in with general market data rather than treated as their own tier.
- Silent degradation with no alerting — a system that quietly downgrades from tick-level to OHLC-level processing during a busy session may be technically "working" while producing materially different (and possibly worse) signals than the strategy was validated against, and nobody notices until performance diverges from backtest.
- Treating backpressure policy as a one-time architectural decision rather than something to revisit as strategies and data volumes change.

## Verification

- Under sustained simulated overload (replay at multiples of peak historical tick rate), confirm risk-critical streams (position/margin/kill-switch) never drop data and instead trigger the defined alert.
- Confirm dashboard/non-critical streams degrade gracefully (throttled updates, coarser data) without crashing or blocking the pipeline.
- Confirm logs show which policy fired for each stream during the overload test, matching the intended design.

## Related Skills

- `producer-consumer-tick-pipeline`
- `tick-buffering-burst-handling`
- `kill-switch-and-drawdown-circuit-breakers`
