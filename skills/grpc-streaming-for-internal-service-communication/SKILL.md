---
name: grpc-streaming-for-internal-service-communication
description: Use when connecting internal microservices (Order Gateway, Risk Engine,
  Portfolio Manager) to replace REST JSON polling with gRPC bi-directional HTTP/2
  binary streaming for low-latency, strongly-typed data communication.
domain: algorithmic-trading
subdomain: real-time-architecture
tags:
- real-time-architecture
- grpc
- protobuf
- http2
- streaming
- low-latency
- microservices
brokers_frameworks:
- gRPC Engine
- Protocol Buffers
- Python asyncio
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when designing internal microservice communications between trading components (e.g. Strategy Engine $\longleftrightarrow$ Risk Service $\longleftrightarrow$ Execution Gateway). Traditional REST/JSON polling incurs repeated HTTP connection setup, text-based JSON parsing CPU cost, and polling latency bounded below by the poll interval. gRPC over HTTP/2 provides binary Protocol Buffer serialization, multiplexed bi-directional streaming over one connection, and push semantics that remove the poll interval from the latency budget.

## When NOT to Use

- **Fan-out to many independent consumers, or any consumer that needs replay.** A gRPC stream is point-to-point and has no retained log: a consumer that restarts has lost everything sent while it was down. Use `kafka-based-tick-distribution-at-scale` or `redis-streams-multi-consumer-tick-fanout` when the same tick must reach several services or survive a consumer restart.
- **Cross-firm or broker-facing links.** Counterparties expose FIX, REST, or WebSocket; gRPC is an internal-topology choice. See `fix-protocol-session-management-across-venues`.
- **Where the bottleneck is not serialization or polling.** Binary framing saves a few microseconds of CPU per message and ~65% of bytes on a tick this size. If the strategy's latency is dominated by network RTT, GC pauses, or model inference, gRPC changes nothing measurable — quantify first with `strategy-latency-budget-decomposition`.
- **As the sole transport for a risk-critical control path.** A channel in `TRANSIENT_FAILURE` backs off up to 120 s (see Pitfalls). A kill switch reachable only over gRPC is a kill switch that can be two minutes away.

## Prerequisites

- Protobuf compiler (`protoc`) or a Python gRPC runtime, plus a versioned `.proto` schema for tick data and order updates.
- An explicit keepalive policy on **both** ends: client `KEEPALIVE_TIME` (disabled by default) and server `PERMIT_KEEPALIVE_TIME` (5 minutes by default) must be set consistently, or the server will terminate the connection.
- A decided message-size budget: gRPC implementations default to a 4 MiB maximum receive size, which caps how many ticks may be batched into a single message.
- A fallback path (polling, secondary venue feed, or cached last-known state) for the window in which the channel is not `READY`.

## Workflow

1. **Define the Protocol Buffer schema (`.proto`)**:
   - Assign field numbers deliberately. Numbers 1-15 cost a one-byte key; 16-2047 cost two. Put high-frequency fields in the 1-15 range.
   - **Decision point — a field number is permanent.** Deleting a field requires `reserved` on both the number and the name. Reusing the number later is what produces silent corruption, not a compile error.

2. **Establish the gRPC HTTP/2 streaming channel**:
   - Drive the documented state machine: `IDLE` → `CONNECTING` → `READY`, with `READY` → `TRANSIENT_FAILURE` on any mid-stream failure and `READY` → `IDLE` on `GOAWAY` with no pending RPCs. `SHUTDOWN` is terminal — a shut-down channel is never reused, it is replaced.
   - **Decision point — classify the disconnect before reconnecting.** `TRANSIENT_FAILURE` self-heals through gRPC's own backoff. A `GOAWAY` with `too_many_pings` does not: it means your keepalive is more aggressive than the server permits, and retrying unchanged reproduces it immediately.

3. **Stream binary frames**:
   - Push encoded frames continuously over the open stream instead of issuing repeated HTTP GET/POST polls.
   - **Decision point — a stalled stream is not an error.** HTTP/2 flow control silently stops a fast producer at the window boundary until the consumer sends `WINDOW_UPDATE`. Backpressure shows up as latency, never as an exception; measure it explicitly (`backpressure-drop-degrade-policy`).
   - Carry a monotonic `sequence_id` on every frame and check continuity receive-side, so a gap is detected rather than inferred from a P&L discrepancy.

4. **Benchmark serialization and payload overhead against your own schema**:
   - Compare against **compact** JSON (`separators=(",", ":")`). Benchmarking against `json.dumps` defaults inflates the JSON baseline by ~8% in cosmetic whitespace and flatters the result.
   - Average timings over many iterations; a single `perf_counter` pair around a sub-microsecond encode measures clock resolution.
   - **Decision point — expect roughly a 60-70% byte reduction on a tick-shaped message, not an order of magnitude.** For the six-field tick in `scripts/`, protobuf encodes to 43 bytes against 126 bytes of compact JSON: 66% smaller, but only ~2.9$\times$. Payloads dominated by `double` fields cannot compress much further, because a double costs 8 bytes on the wire regardless of encoding.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Assuming a tag-number mistake fails loudly**: it does not. `int32`, `uint32`, `int64`, `uint64` and `bool` are all mutually wire-compatible, so a reused or renumbered field decodes successfully into the wrong field and the service acts on a plausible wrong value — an order quantity read out of a venue id. Reserve deleted numbers; never renumber a field that is in use.
- **Leaving client keepalive at its default**: gRPC client `KEEPALIVE_TIME` defaults to `INT_MAX`, i.e. off. On a half-open TCP connection the stream simply stops delivering — no error, no state change — and a risk engine waits indefinitely for ticks that will never arrive. Set keepalive explicitly and treat prolonged silence on a tick stream as a failure.
- **Setting keepalive more aggressively than the server permits**: the server's `PERMIT_KEEPALIVE_TIME` defaults to 5 minutes; ping faster than that and the server sends `GOAWAY` with debug data `too_many_pings` and drops the connection. Tune both ends together, or aggressive client keepalive becomes a self-inflicted outage.
- **Treating reconnection as instantaneous**: gRPC retries from `TRANSIENT_FAILURE` with exponential backoff — 1 s initial, $\times 1.6$ per attempt, $\pm 20\%$ jitter, capped at 120 s. A strategy whose risk-limit link is in late backoff is trading against limits it cannot refresh. Enforce a maximum tolerable disconnection independently of the transport, and halt on breach.
- **Head-of-line blocking on TCP**: HTTP/2 multiplexing removes application-level head-of-line blocking between streams, but RFC 9113 is explicit that TCP head-of-line blocking is not addressed by the protocol. One lost segment stalls every stream on that connection. Do not collapse an entire trading estate onto a single TCP connection without pooling or separating latency-critical paths.
- **Batching ticks past the message-size limit**: with a 4 MiB default receive limit, a batching producer that grows its batch under load eventually trips `RESOURCE_EXHAUSTED` — precisely when load is highest. Bound the batch by bytes, not by tick count.
- **Reading a fixed struct size as the protobuf size**: proto3 uses varints and omits default-valued fields, so encoded size depends on the values. The same schema that encodes to 43 bytes here encodes to 0 bytes when every field is zero and 55 bytes at maximum field values. Size budgets must use the measured distribution, not one sample.

## Verification

- Round-trip `ProtobufTickFrame` and confirm the fixed-width frame is exactly 44 bytes (`>QQIddd`), while `proto3_wire_size()` returns 43 for the sample tick, 0 for an all-default frame, and 55 at maximum field values — demonstrating that protobuf size is value-dependent.
- Stream 1,000 sequential frames and assert `stream_integrity_report()` reports `frames_sent == frames_received == 1000` with `sequence_gap_events == 0` and `is_contiguous` true. Then push the sequence `1, 2, 5, 5, 4` and confirm one gap event, two missing frames, and two out-of-order frames.
- Confirm `benchmark_protobuf_vs_json` reports 126 compact-JSON bytes against 44 binary bytes (>50% reduction, ratio below $3\times$) and averages timings over the requested iteration count.
- Negative checks: a truncated or over-long buffer, a `NaN`/`Inf` price, a negative volume, a `symbol_id` above $2^{32}-1$, and `iterations=0` must each raise. A push on an `IDLE`, `TRANSIENT_FAILURE`, or `SHUTDOWN` channel must raise rather than silently drop the frame, and `SHUTDOWN` must be unrecoverable.
- Confirm the backoff schedule matches the gRPC reference: 1.0 s, 1.6 s, 2.56 s, capped at 120 s.
- Run `python -m unittest discover -s skills/grpc-streaming-for-internal-service-communication/scripts` and confirm 100% pass rate.

## Related Skills

- `kafka-based-tick-distribution-at-scale`
- `producer-consumer-tick-pipeline`
- `multi-exchange-feed-normalization`
- `sequence-number-gap-detection-for-feeds`
- `backpressure-drop-degrade-policy`
- `graceful-degradation-to-polling-fallback`
- `circuit-breaker-for-downstream-service-calls`
