---
name: producer-consumer-tick-pipeline
description: >-
  Use when designing the ingestion path for a WebSocket market data feed, to prevent slow strategy/processing logic from blocking or dropping the socket's read loop
domain: algorithmic-trading
subdomain: real-time-architecture
tags: ["real-time-architecture", "websocket-streaming-apis-\u2014-fyers", "kite", "ibkr"]
brokers_frameworks: ["WebSocket streaming APIs \u2014 Fyers", "Kite", "IBKR", "Alpaca market data streams"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a bot subscribes to a live tick/quote WebSocket feed and runs any non-trivial processing (signal computation, ML inference, DB writes) in response to ticks. If processing logic executes directly inside the WebSocket's `on_message` callback, any slowdown in processing (a slow DB write, a GC pause, an ML inference call) delays reading the next frame off the socket — and most broker WebSocket clients will disconnect or drop the connection if the read loop stalls past the broker's internal buffer/heartbeat timeout.

## Prerequisites

- An in-process queue (e.g., `asyncio.Queue`, a bounded thread-safe queue, or a lightweight message broker for multi-process setups)
- Clear separation between "I/O thread that owns the socket" and "worker(s) that process data"

## Workflow

1. The WebSocket callback's only job is: parse the minimum needed to route the message, then push it onto a queue and return immediately. Do not perform strategy logic, DB writes, or any blocking call inside this callback.
2. Size the queue as bounded, not unbounded — an unbounded queue under sustained backlog just delays an out-of-memory crash instead of surfacing the backlog as a decision to make (see `tick-buffering-burst-handling` and `backpressure-drop-degrade-policy` for what to do when the queue fills).
3. Run one or more consumer workers (async tasks or separate threads/processes depending on whether processing is I/O-bound or CPU-bound) that pull from the queue and perform the actual signal computation.
4. If using multiple consumer workers for parallelism, partition work by instrument/symbol (not round-robin) so all ticks for a given symbol are processed in order by the same worker — round-robin partitioning across workers reintroduces the exact ordering bugs (e.g., processing a later tick before an earlier one) that the queue was meant to prevent.
5. For multi-process architectures (e.g., separate Node.js WebSocket relay feeding a Python strategy engine, as in a shared-state setup with Redis/PostgreSQL), use a proper pub-sub or message broker (Redis pub-sub, a lightweight queue) rather than polling a shared DB table for new ticks — DB polling adds latency proportional to poll interval and adds unnecessary load.
6. Instrument the queue depth as a live metric — this is the single most useful signal for detecting the pipeline falling behind before it causes a dropped connection or missed signal (feed into `backpressure-drop-degrade-policy`).

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Writing strategy logic directly in the `on_message`/`on_tick` handler because "it's simpler" during prototyping, then never refactoring before going live — this pattern works fine on a quiet market and fails specifically during the high-volatility bursts when correct signals matter most.
- Using an unbounded queue and treating memory growth as "not a problem yet" — by the time it becomes a visible problem it is usually during a volatility spike, the worst time for the bot to OOM-crash.
- Partitioning consumer workers by round-robin instead of by symbol, causing out-of-order processing for a single instrument's tick sequence.
- Not distinguishing between I/O-bound processing (safe to run many async consumers) and CPU-bound processing like ML inference (needs a process pool, since Python's GIL means CPU-bound work in threads doesn't actually parallelize).

## Verification

- Under a simulated tick burst (replay a recorded high-volatility session at accelerated speed), confirm the WebSocket connection does not disconnect and no heartbeat timeout is triggered, even while the consumer queue temporarily grows.
- Confirm ticks for a single symbol are processed in strictly increasing timestamp order in logs/output, even under multi-worker consumption.
- Confirm queue-depth metrics are visible in logs/monitoring and correlate sensibly with market volatility (rising during bursts, draining after).

## Related Skills

- `tick-buffering-burst-handling`
- `backpressure-drop-degrade-policy`
- `websocket-reconnect-without-duplicate-subscriptions`
