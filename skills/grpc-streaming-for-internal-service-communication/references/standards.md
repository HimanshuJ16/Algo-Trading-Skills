# Real-Time Architecture Standards — grpc-streaming-for-internal-service-communication

## Measured payload comparison

Six-field tick message (`uint64 sequence_id`, `uint64 timestamp_ns`, `uint32 symbol_id`,
`double bid_price`, `double ask_price`, `double volume`), sample values as in
`scripts/test_grpc_stream_engine.py`.

| Metric / Parameter | Protobuf (proto3 wire) | JSON Text | Note |
|---|---|---|---|
| Encoded size, sample tick | 43 bytes | 126 bytes (compact) / 137 bytes (`json.dumps` defaults) | **66% smaller, i.e. ~2.9$\times$** — not an order of magnitude |
| Encoded size, all-default frame | 0 bytes | 126 bytes | proto3 omits default-valued fields entirely |
| Encoded size, maximum field values | 55 bytes | — | varint width is value-dependent; size is not fixed |
| Fixed-width `struct` stand-in (`>QQIddd`) | 44 bytes | — | a property of the simulation in `scripts/`, **not** of protobuf |
| Transport | HTTP/2, multiplexed streams | HTTP/1.1 request-response | one connection, no per-request setup |
| Schema | `.proto`, compile-time typed | schema-less | see the type-compatibility caveat below |

Reproduce with `benchmark_protobuf_vs_json()`; the sizes above are asserted in the
unit tests. Payloads dominated by `double` fields have a hard floor: a double is
8 bytes on the wire under any encoding.

## Protobuf schema-evolution rules

Field numbers are permanent once a message type is in use. Deleting a field requires
`reserved` on both the number and the name. The dangerous case is not a decode error
but a **silent** one: `int32`, `uint32`, `int64`, `uint64` and `bool` are mutually
wire-compatible, and `fixed64`/`sfixed64` likewise, so a reused number decodes cleanly
into the wrong field. proto3 also preserves unknown fields rather than rejecting them,
so an unrecognised field is not a signal that anything is wrong.

## gRPC channel connectivity states

| State | Meaning | Legal successors |
|---|---|---|
| `IDLE` | No connection attempt in progress (no pending RPCs) | `CONNECTING`, `SHUTDOWN` |
| `CONNECTING` | Name resolution / TCP / TLS / HTTP-2 handshake in progress | `READY`, `TRANSIENT_FAILURE`, `SHUTDOWN` |
| `READY` | Handshake complete, RPCs can proceed | `IDLE` (idle timeout or `GOAWAY` with no pending RPCs), `TRANSIENT_FAILURE`, `SHUTDOWN` |
| `TRANSIENT_FAILURE` | Transient error; retries with backoff | `CONNECTING`, `SHUTDOWN` |
| `SHUTDOWN` | Terminal — channels that enter never leave | none |

## gRPC reference reconnect backoff

| Parameter | Value |
|---|---|
| `INITIAL_BACKOFF` | 1 second |
| `MULTIPLIER` | 1.6 |
| `JITTER` | 0.2 |
| `MAX_BACKOFF` | 120 seconds |
| `MIN_CONNECT_TIMEOUT` | 20 seconds |

Backoff resets when the server's `SETTINGS` frame is received. **The 120 s cap is the
number that matters for trading**: it bounds how stale a risk-limit or position link
may become before the transport alone recovers it.

## gRPC keepalive defaults

| Parameter | Client default | Server default |
|---|---|---|
| `KEEPALIVE_TIME` | `INT_MAX` (**disabled**) | 7200000 ms (2 hours) |
| `KEEPALIVE_TIMEOUT` | 20000 ms | 20000 ms |
| `KEEPALIVE_WITHOUT_CALLS` / `PERMIT_KEEPALIVE_WITHOUT_CALLS` | 0 (false) | 0 (false) |
| `PERMIT_KEEPALIVE_TIME` | — | 300000 ms (5 minutes) |

Pinging more often than the server's `PERMIT_KEEPALIVE_TIME` causes the server to send
`GOAWAY` with debug data `too_many_pings`.

## HTTP/2 transport parameters

| Parameter | Value | Consequence |
|---|---|---|
| `SETTINGS_INITIAL_WINDOW_SIZE` | 65,535 octets initially | A fast producer stalls at the window boundary until `WINDOW_UPDATE`; backpressure presents as latency, not as an error |
| `SETTINGS_MAX_CONCURRENT_STREAMS` | no limit initially; spec recommends $\geq 100$ | Peers may impose a limit; excess streams are refused |
| TCP head-of-line blocking | **not addressed by HTTP/2** (RFC 9113 §5.1.2) | One lost segment stalls every multiplexed stream on that connection |
| Default max receive message size | 4 MiB (4,194,304 bytes) in common gRPC implementations, configurable | Oversized batches fail with `RESOURCE_EXHAUSTED` |

## Sources

- Protocol Buffers encoding guide — varints, I64 wire type, one-byte keys for field numbers 1-15, omission of default values: https://protobuf.dev/programming-guides/encoding/
- Protocol Buffers proto3 language guide, "Updating A Message Type" — field-number permanence, `reserved`, scalar wire compatibility, unknown-field preservation: https://protobuf.dev/programming-guides/proto3/
- gRPC channel connectivity semantics: https://github.com/grpc/grpc/blob/master/doc/connectivity-semantics-and-api.md
- gRPC connection backoff protocol: https://github.com/grpc/grpc/blob/master/doc/connection-backoff.md
- gRPC keepalive guide: https://grpc.io/docs/guides/keepalive/
- RFC 9113, HTTP/2 — §5.1.2 (TCP head-of-line blocking not addressed), §6.5.2 (`SETTINGS_INITIAL_WINDOW_SIZE`, `SETTINGS_MAX_CONCURRENT_STREAMS`): https://www.rfc-editor.org/rfc/rfc9113.html

The 4 MiB default receive limit is widely documented across gRPC implementations and
issue trackers rather than in a single normative specification; treat it as an
implementation default to verify for your runtime, not as a protocol guarantee.

## Category

`real-time-architecture` — see top-level `mappings/` directory.
