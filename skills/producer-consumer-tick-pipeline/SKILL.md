---
name: producer-consumer-tick-pipeline
description: Use when designing the ingestion path for a WebSocket market data feed,
  to prevent slow strategy/processing logic from blocking or dropping the socket's
  read loop
domain: algorithmic-trading
subdomain: real-time-architecture
tags:
- real-time-architecture
- websocket-ingestion
- tick-pipeline
- queue-partitioning
- thread-safety
brokers_frameworks:
- Fyers API v3 (fyers-apiv3 FyersDataSocket)
- Zerodha Kite Connect (pykiteconnect KiteTicker)
- IBKR TWS / Gateway API
- Alpaca market data streams
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a bot subscribes to a live tick/quote WebSocket feed and runs any non-trivial processing (signal computation, ML inference, DB writes) in response to ticks. If processing logic executes directly inside the WebSocket's `on_message` callback, any slowdown in processing (a slow DB write, a GC pause, an ML inference call) delays reading the next frame off the socket — and most broker WebSocket clients will disconnect or drop the connection if the read loop stalls past the broker's internal buffer/heartbeat timeout.

## When NOT to Use

- **When the tick handler is genuinely trivial** (append to an in-memory array, update a last-price dict). A queue plus a worker adds a hop and a failure mode for no benefit; the pipeline earns its place once processing can block.
- **As a substitute for a drop policy.** This skill decides *where* work happens. What to discard when the queue is persistently full is `backpressure-drop-degrade-policy`, and how to absorb a momentary spike is `tick-buffering-burst-handling`.
- **For cross-process fan-out.** An in-process `asyncio.Queue` does not survive a process boundary — see `redis-streams-multi-consumer-tick-fanout` or `kafka-based-tick-distribution-at-scale`.
- **For order/execution acknowledgements.** Those are not a droppable telemetry stream; a full queue must not silently discard a fill.

## Prerequisites

- An in-process queue (e.g., `asyncio.Queue`, a bounded thread-safe queue, or a lightweight message broker for multi-process setups)
- Clear separation between "I/O thread that owns the socket" and "worker(s) that process data"
- Knowledge of **which thread your broker SDK calls back on** — this determines whether the handoff must be thread-safe (see step 2)

## Workflow

1. The WebSocket callback's only job is: parse the minimum needed to route the message, then push it onto a queue and return immediately. Do not perform strategy logic, DB writes, or any blocking call inside this callback.
2. Establish which thread the SDK's callback runs on before choosing the queue. `asyncio.Queue` is **not thread-safe**, and most Python broker SDKs call back off the event loop: `pykiteconnect`'s `KiteTicker` fires `on_ticks` on the Twisted reactor thread (a background daemon thread under `connect(threaded=True)`), and `fyers-apiv3`'s `FyersDataSocket` calls `onmessage` from its own socket thread, whereas `alpaca-py`'s stream is asyncio-native and calls back on the loop. From a foreign thread, hand off with `loop.call_soon_threadsafe` (or use a `queue.Queue` plus a loop-side reader) — a bare `put_nowait` from another thread enqueues the tick without waking the loop, so it is not processed until the loop wakes for some unrelated reason.
3. Size the queue as bounded, not unbounded — an unbounded queue under sustained backlog just delays an out-of-memory crash instead of surfacing the backlog as a decision to make (see `tick-buffering-burst-handling` and `backpressure-drop-degrade-policy` for what to do when the queue fills). Note that `asyncio.Queue(maxsize=0)` means *unbounded*, so validate the configured bound rather than trusting the constructor. Bound the cross-thread handoff too: `call_soon_threadsafe` has no capacity limit of its own, and an unchecked handoff simply relocates the unbounded queue into the loop's ready list.
4. Run one or more consumer workers (async tasks or separate threads/processes depending on whether processing is I/O-bound or CPU-bound) that pull from the queue and perform the actual signal computation.
5. If using multiple consumer workers for parallelism, partition work by instrument/symbol (not round-robin) so all ticks for a given symbol are processed in order by the same worker — round-robin partitioning across workers reintroduces the exact ordering bugs (e.g., processing a later tick before an earlier one) that the queue was meant to prevent. Partition with a **stable** hash (`zlib.crc32`, `hashlib`), not the builtin `hash()`: `hash(str)` is salted by `PYTHONHASHSEED` and differs per process, so a restart or a second process reshuffles symbols across workers.
6. Treat a raised exception in a worker as a tick-level failure, not a worker-level one: log it, count it, mark the queue item done, and keep consuming. A worker that dies on one bad tick silently stops processing every symbol in its partition, and skipping `task_done()` leaves `queue.join()` waiting forever.
7. For multi-process architectures (e.g., separate Node.js WebSocket relay feeding a Python strategy engine, as in a shared-state setup with Redis/PostgreSQL), use a proper pub-sub or message broker (Redis pub-sub, a lightweight queue) rather than polling a shared DB table for new ticks — DB polling adds latency proportional to poll interval and adds unnecessary load.
8. Instrument the queue depth as a live metric — this is the single most useful signal for detecting the pipeline falling behind before it causes a dropped connection or missed signal (feed into `backpressure-drop-degrade-policy`). Track queue *wait* time separately from processing time: processing latency alone looks healthy while ticks sit in a backlog going stale.
9. Drain on shutdown rather than cancelling mid-flight, so a deploy or restart does not silently discard queued ticks — and count whatever could not be drained inside the timeout (see `graceful-shutdown-draining-in-flight-ticks`).

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Writing strategy logic directly in the `on_message`/`on_tick` handler because "it's simpler" during prototyping, then never refactoring before going live — this pattern works fine on a quiet market and fails specifically during the high-volatility bursts when correct signals matter most.
- Pushing onto an `asyncio.Queue` directly from the broker SDK's callback thread. It does not raise, the queue depth looks correct, and the tick still gets processed *eventually* — so the bug reads as "occasional latency" rather than as a threading error. Measured against this skill's previous implementation, a tick pushed while the loop was parked in `select()` waited 2.7 s to be picked up.
- Using an unbounded queue and treating memory growth as "not a problem yet" — by the time it becomes a visible problem it is usually during a volatility spike, the worst time for the bot to OOM-crash. Passing `maxsize=0` to `asyncio.Queue` to mean "no queue limit needed here" produces exactly that unbounded queue.
- Partitioning consumer workers by round-robin instead of by symbol, causing out-of-order processing for a single instrument's tick sequence — or partitioning by symbol with the builtin `hash()`, which is stable within one process and reshuffles on the next restart.
- Not distinguishing between I/O-bound processing (safe to run many async consumers) and CPU-bound processing like ML inference (needs a process pool, since Python's GIL means CPU-bound work in threads doesn't actually parallelize).
- Logging every dropped tick. A saturated queue drops every tick that arrives, so the log write itself becomes the next bottleneck; rate-limit the warning and report an aggregate count.
- Cancelling worker tasks on shutdown and reporting a clean stop. The queued ticks are gone and nothing counted them.

## Verification

- Under a simulated tick burst (replay a recorded high-volatility session at accelerated speed), confirm the WebSocket connection does not disconnect and no heartbeat timeout is triggered, even while the consumer queue temporarily grows.
- Confirm ticks for a single symbol are processed in strictly increasing timestamp order in logs/output, even under multi-worker consumption.
- Push a tick from a non-loop thread while the event loop is otherwise idle and confirm it is processed within milliseconds, not on the next unrelated loop wake-up.
- Confirm the symbol-to-worker mapping is identical across two separate process launches (run it under differing `PYTHONHASHSEED` values).
- Raise an exception inside the processing function and confirm the worker keeps consuming, the failure is counted, and `queue.join()` still completes.
- Confirm queue-depth and queue-wait metrics are visible in logs/monitoring and correlate sensibly with market volatility (rising during bursts, draining after).
- Stop the pipeline with a backlog queued and confirm the backlog is processed, or that whatever was discarded is reported.
- Run the unit suite and confirm every test passes:
  `python -m unittest discover -s skills/producer-consumer-tick-pipeline/scripts`.

## Related Skills

- `tick-buffering-burst-handling`
- `backpressure-drop-degrade-policy`
- `websocket-reconnect-without-duplicate-subscriptions`
- `graceful-shutdown-draining-in-flight-ticks`
- `redis-streams-multi-consumer-tick-fanout`
