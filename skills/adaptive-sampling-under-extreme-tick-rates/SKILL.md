---
name: adaptive-sampling-under-extreme-tick-rates
description: >-
  Use when a trade-tick stream exceeds a consumer's measured processing ceiling and you
  must emit 1:N samples that still preserve traded volume and notional. Not for
  order-book, quote or compliance feeds that need every message.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: real-time-architecture
  tags: real-time-architecture, adaptive-sampling, flash-crash, tick-filtering, volume-preservation, throughput-protection
  brokers_frameworks: Adaptive Tick Sampler, Python Real-Time Engine
  version: "1.2.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill for a high-rate **trade-tick** stream when a downstream strategy or analytics consumer has a measured processing ceiling and cannot safely keep every input tick. The engine monitors per-symbol arrival rate, emits every `1:N` ticks during overload, and aggregates skipped trade volume and price-volume notional into the emitted sample.

The default target of 5,000 ticks/second is an example capacity parameter, not a universal market-data standard. Calibrate it from measured CPU, queue, latency, and downstream correctness budgets.

## When NOT to Use

- Do not use sampled output for order-book reconstruction, quote protection, spread/microstructure signals, queue-position models, latency-sensitive execution, or regulatory/compliance records that require the complete feed.
- Do not use it to repair feed gaps, sequence gaps, duplicate messages, or venue recovery state. Detect and reconcile those upstream before sampling.
- Do not drop residual aggregates at shutdown, symbol removal, or feed restart; call `flush`, `flush_all`, or `reset_symbol(flush=True)` and persist the result.
- Do not treat aggregate VWAP as an OHLC bar or assume it preserves the path, timing, or extrema of skipped trades.

## Prerequisites

- A trade-tick feed with documented symbol identity, sequence semantics, event timestamps, price units, and volume units.
- A measured per-symbol processing target and an explicit policy for overload, feed restart, sequence reset, and symbol lifecycle.
- Downstream consumers that understand `SampledTick.aggregated_tick_count`, `sampling_factor`, and synthetic `is_flush` records.
- A durable handoff or audit path for emitted samples, validation errors, and flush/recovery events.
- Python 3.10+.

## Workflow

1. **Set the capacity contract**: Construct `AdaptiveTickSamplerEngine(target_max_rate_per_sec=...)` with a positive integer target. Keep `enforce_monotonic_sequence=True` unless the feed contract explicitly permits another policy.
2. **Validate feed identity upstream**: Verify symbol, sequence, timestamp, price, and volume semantics. The engine rejects empty symbols, non-finite values, non-positive prices/volumes, duplicate or decreasing sequences, and backwards event timestamps.
3. **Ingest one trade tick at a time**: Call `ingest_tick(...)`. Under the target rate it emits a passthrough aggregate; above the target it computes `k = ceil(rate / target)` with a minimum sampled factor of 2.
4. **Consume explicit metadata**: Use `mode` and `sampling_factor` to identify policy, `aggregated_tick_count` to identify how many raw trades are represented, and `is_flush` to distinguish synthetic residual output. Treat `aggregated_tick_count > 1` — not `mode` — as the test for whether `price` is a VWAP rather than a traded price, because a `PASSTHROUGH` emission also drains any residual sampled block.
5. **Handle errors fail-closed**: Route validation exceptions to the feed-integrity path. Do not retry the same malformed or duplicate tick as if it were new data.
6. **Manage lifecycle**: Call `flush(symbol)` for an individual stream, `flush_all()` during shutdown/checkpointing, and `reset_symbol(flush=True)` when a symbol leaves the stream or its sequence domain resets.
7. **Monitor quality**: Track input/output rates, sampling factor, aggregate counts, volume/notional reconciliation, validation failures, flush counts, queue latency, and downstream processing latency.

## Common Pitfalls

- **Discarding skipped volume**: Emitting only the selected tick loses traded volume and corrupts aggregate VWAP.
- **Calling the result a full feed**: A VWAP aggregate preserves volume and notional, not every price path, quote, or order-book event.
- **Keying on `mode` to detect a raw trade**: when the rate falls back below target mid-block, the draining emission reports `PASSTHROUGH` with `sampling_factor=1` while still carrying `aggregated_tick_count > 1` and a VWAP price no venue printed. Only `aggregated_tick_count` separates a raw trade from an aggregate.
- **Accepting duplicate or out-of-order data**: Repeated trades double-count volume; backwards event time corrupts rolling-rate windows.
- **Using wall-clock flush timestamps**: A flush must remain deterministic in replay; the implementation defaults to the last event timestamp unless an explicit later timestamp is supplied.
- **Leaking symbol state**: Long-lived feeds must reset inactive symbols after flushing so per-symbol dictionaries do not grow without bound.
- **Sampling before feed recovery**: Sequence gaps and venue recovery require upstream reconciliation; sampling cannot infer missing trades.

## Verification

Run the focused test suite:

```text
python -m unittest discover -s skills/adaptive-sampling-under-extreme-tick-rates/scripts
```

The tests cover passthrough, the `rate == target` boundary, overload sampling, volume/notional preservation across a burst, the `PASSTHROUGH` drain of a partial sampled block, zero timestamps, deterministic flushes, rejection of a flush timestamp preceding the last accepted tick, sorted `flush_all`, `reset_symbol` with and without a flush, duplicate/out-of-order rejection, the optional non-monotonic sequence policy, malformed tick values, invalid targets, symbol isolation, state-neutral rejection of an overflowing aggregate, and concurrent multi-threaded ingestion of a shared symbol. Production sign-off additionally requires replaying calibrated bursts and verifying downstream reconciliation against raw input totals.

## Related Skills

- `backpressure-drop-degrade-policy`
- `tick-buffering-burst-handling`
- `adaptive-batch-size-tuning-under-load`