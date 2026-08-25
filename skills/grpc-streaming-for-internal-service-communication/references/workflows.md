# Deep Workflow Reference — grpc-streaming-for-internal-service-communication

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Protocol Buffer schema definition (`.proto`)**:
   - Define message types with explicit field numbers. Keep hot fields in 1-15 (one-byte
     key); 16-2047 cost two bytes per occurrence.
   - Define the bi-directional service, e.g.
     `rpc StreamTicks(stream TickRequest) returns (stream TickResponse)`.
   - Record every retired field number in a `reserved` clause, together with its name.
     Field numbers are permanent; reuse decodes silently into the wrong field rather
     than failing.

2. **HTTP/2 channel establishment**:
   - Drive `IDLE` → `CONNECTING` → `READY`. Handle `CONNECTING` → `TRANSIENT_FAILURE`
     (handshake failure) and `READY` → `TRANSIENT_FAILURE` (mid-stream failure).
   - Handle `READY` → `IDLE`: a `GOAWAY` received with no pending RPCs drops the channel
     to `IDLE` with no error surfaced on the stream.
   - Treat `SHUTDOWN` as terminal. Replace the channel; do not attempt to revive it.
   - Set keepalive explicitly on both ends. Client `KEEPALIVE_TIME` is disabled by
     default, so a half-open connection produces silence rather than an error; the
     server's `PERMIT_KEEPALIVE_TIME` (5 minutes) bounds how fast you may ping before
     it responds with `GOAWAY` / `too_many_pings`.

3. **Reconnection policy**:
   - gRPC retries from `TRANSIENT_FAILURE` on the reference schedule: 1 s initial,
     $\times 1.6$, $\pm 20\%$ jitter, capped at 120 s, reset when the server's `SETTINGS`
     frame arrives.
   - Layer an application-level staleness limit on top. The transport's own recovery
     may take two minutes; decide independently how long the strategy may run on a
     stale risk or position link before halting.

4. **Binary streaming transmission**:
   - Push encoded frames continuously over the open stream rather than polling.
   - Carry a monotonic `sequence_id` and verify continuity receive-side.
   - Bound batches by **bytes**, not by tick count: the common 4 MiB receive limit is
     hit exactly when volume spikes.
   - Expect flow-control stalls at the 65,535-octet initial window: a slow consumer
     throttles a fast producer silently, as latency rather than as an exception.

5. **Serialization benchmark and overhead audit**:
   - Compare against compact JSON (`separators=(",", ":")`); `json.dumps` defaults add
     ~8% of cosmetic whitespace to the baseline.
   - Average over many iterations — a single `perf_counter` pair around a sub-microsecond
     encode measures clock resolution, not encoding cost.
   - Measure the encoded-size *distribution*, not one sample: proto3 varints and default
     omission make the size value-dependent (43 bytes for the reference tick, 0 bytes
     all-default, 55 bytes at maximum field values).

## Production Implementation Reference

- Reference code: `scripts/grpc_stream_engine.py`
  (`GRPCStreamingMarketDataEngine`, `ProtobufTickFrame`, `GRPCChannelState`,
  `LEGAL_CHANNEL_TRANSITIONS`).
- Automated unit tests: `scripts/test_grpc_stream_engine.py`.

**Scope of the reference code.** It models the channel lifecycle and payload economics
without depending on the `grpc` runtime or a compiled `.proto`. `ProtobufTickFrame` uses
fixed-width `struct` packing — a 44-byte stand-in, not the protobuf wire format;
`proto3_wire_size()` computes the true encoded size. `stream_push_frame` is a loopback:
it verifies the codec and the producer's sequence discipline, not network loss. Real
receive-side gap recovery belongs to `sequence-number-gap-detection-for-feeds`.
