# Deep Workflow Reference — producer-consumer-tick-pipeline

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Zero-Blocking WebSocket Callback:**
   - Restrict `on_message` WebSocket callbacks strictly to message reception and a queue push.
   - Disallow signal processing, ML inference, or database writes inside the socket callback.
   - The callback budget is a design target you must measure on your own hardware, not a
     guaranteed property of the code: assert it with a benchmark rather than assuming it.

2. **Thread-Correct Handoff:**
   - Determine the thread the SDK's callback runs on. `asyncio.Queue` is documented as
     not thread-safe, and `put_nowait` from a foreign thread completes the consumer's
     future through `loop.call_soon`, which does not wake a loop parked in `select()`.
   - From a non-loop thread, hand off with `loop.call_soon_threadsafe` (what
     `SymbolPartitionedTickPipeline.submit_threadsafe` does) or use a `queue.Queue`
     drained by a loop-side reader.
   - Bound the handoff. `call_soon_threadsafe` has no capacity of its own, so admission
     control on the producer side (`max_pending_handoffs`) is what keeps the unbounded
     queue from reappearing inside the loop's ready list.

3. **Stable Symbol Work Partitioning:**
   - Partition tick distribution across $N$ worker queues using `zlib.crc32(symbol) % N`.
   - Do **not** use `hash(symbol) % N`: `hash()` of a `str` is salted by `PYTHONHASHSEED`,
     so the mapping changes on every restart and differs between processes.
   - One consumer per queue is what makes the ordering guarantee hold; two consumers on
     one queue reintroduce the interleaving the partitioning was meant to remove.

4. **Bounded Queue Backpressure Protection:**
   - Size worker queues with explicit maximum bounds (`maxsize`). `asyncio.Queue(maxsize=0)`
     is unbounded — validate the configured value instead of trusting the constructor.
   - Track dropped ticks (`total_ticks_dropped`) and feed metrics into `backpressure-drop-degrade-policy`.
   - Rate-limit the drop warning: a saturated queue drops on every arriving tick, and one
     log line per drop makes the logging the next bottleneck.

5. **Tick-Level Failure Isolation:**
   - An exception from `process_fn` is a failed tick, not a failed worker: count it
     (`total_ticks_failed`), log it with a traceback, call `task_done()`, and keep consuming.
   - Skipping `task_done()` on the failure path leaves `queue.join()` blocked forever, which
     turns a graceful drain into a hang.

6. **Pipeline Telemetry Monitoring:**
   - Expose `PipelineMetrics` tracking queue depth, max queue depth, processing latency,
     queue wait time, drops, failures, and undrained ticks.
   - Queue wait (arrival → start of processing) is the staleness signal. Processing latency
     alone stays flat while a backlog builds.

7. **Graceful Drain on Shutdown:**
   - `stop_consumers(drain=True)` stops admission, lets workers finish the queued backlog,
     and counts anything still queued at `drain_timeout` as `total_ticks_undrained`.
   - Stop the producer first: ticks submitted after shutdown has begun have no consumer left.

## Known Failure Modes

- **In-Callback Strategy Execution:** Executing ML inference or DB writes inside `on_message`, causing socket buffer overruns and connection drops during market spikes.
- **Cross-Thread Queue Push:** Pushing onto an `asyncio.Queue` from the SDK's callback thread. Nothing raises and the depth metric looks right, but consumption stalls until the loop wakes for an unrelated reason — a 2.7 s stall has been measured this way.
- **Round-Robin Worker Partitioning:** Partitioning worker tasks using round-robin, introducing out-of-order tick processing for individual symbols.
- **Salted-Hash Partitioning:** `hash(symbol) % N` reshuffles the symbol→worker mapping on restart, so ordering guarantees do not survive a redeploy or extend across processes.
- **Unbounded Memory Queues:** Allowing queues to grow infinitely (including via `maxsize=0`), causing silent host Out-Of-Memory (OOM) crashes during volatility bursts.
- **Worker Death on One Bad Tick:** An unhandled exception ending the consumer task, silently stopping every symbol in that partition.
- **Cancel-on-Shutdown:** Cancelling workers at deploy time, discarding the queued backlog with nothing counting it.

## Production Implementation Reference

- Reference code: `scripts/tick_pipeline.py` (`SymbolPartitionedTickPipeline`, `PipelineMetrics`).
- Automated unit tests: `scripts/test_tick_pipeline.py`.

## Sources

- Python documentation, `asyncio` queues — "This class is not thread safe."
  https://docs.python.org/3/library/asyncio-queue.html
- Python documentation, `PYTHONHASHSEED` — string hash randomization per process.
  https://docs.python.org/3/using/cmdline.html#envvar-PYTHONHASHSEED
- `pykiteconnect` `kiteconnect/ticker.py` — Twisted/Autobahn client; `connect(threaded=True)`
  runs `reactor.run` on a background daemon thread.
  https://github.com/zerodha/pykiteconnect/blob/master/kiteconnect/ticker.py
- Fyers API v3 Python sample code — `FyersDataSocket` runs its socket on a background
  thread and invokes `onmessage` from it.
  https://github.com/FyersDev/fyers-api-sample-code/tree/sample_v3/v3/python/websocket/data_socket
