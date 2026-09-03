# Pre-Flight / Sign-off Checklist — grpc-streaming-for-internal-service-communication

Use this before considering the skill's implementation complete.

## Schema

- [ ] **Field numbers permanent:** every retired field number and name is in a `reserved` clause; no in-use field has been renumbered.
- [ ] **Hot fields in 1-15:** high-frequency fields use one-byte keys.
- [ ] **Silent-corruption review:** no field number has been reused across a type change within the mutually wire-compatible set (`int32`/`uint32`/`int64`/`uint64`/`bool`, `fixed64`/`sfixed64`).

## Channel lifecycle

- [ ] **State machine:** `READY` is confirmed before streaming; `READY` → `TRANSIENT_FAILURE` and `READY` → `IDLE` (on `GOAWAY`) are both handled.
- [ ] **`SHUTDOWN` treated as terminal:** the channel is replaced, never revived.
- [ ] **Keepalive set on both ends:** client `KEEPALIVE_TIME` is explicitly configured (it is disabled by default) and is no more aggressive than the server's `PERMIT_KEEPALIVE_TIME` (5 min default).
- [ ] **Silence is a failure:** prolonged absence of frames on a tick stream raises an alert; the code does not wait indefinitely on a half-open connection.
- [ ] **Staleness limit independent of transport:** a maximum tolerable disconnection is enforced in the application, given gRPC's 120 s backoff cap.
- [ ] **Fallback path exists:** polling, secondary feed, or cached state covers the non-`READY` window.

## Streaming integrity

- [ ] **Sequence continuity checked receive-side:** `stream_integrity_report()` (or equivalent) reports gaps, duplicates, and out-of-order frames.
- [ ] **Retention bounded:** received-frame buffers have a maximum size and cannot grow without limit.
- [ ] **Batches bounded by bytes:** batch size respects the 4 MiB default receive limit rather than a fixed tick count.
- [ ] **Backpressure observable:** flow-control stalls at the 65,535-octet window are measured as latency, not assumed absent.

## Payload economics

- [ ] **Benchmark is fair:** JSON baseline uses compact separators; timings are averaged over many iterations.
- [ ] **Size claim matches measurement:** the reduction quoted for your schema is measured, not assumed (~66%, ~2.9$\times$, for the reference tick — not "3-5$\times$").
- [ ] **Size distribution, not one sample:** proto3 varints and default omission make the encoded size value-dependent.

## Validation

- [ ] **Input validation:** non-finite prices, negative volume, and out-of-range `uint32`/`uint64` fields are rejected at the boundary.
- [ ] **Framing errors are loud:** truncated or over-long buffers raise a descriptive error, not an opaque `struct.error`.
- [ ] **Automated testing:** run `python -m unittest discover -s skills/grpc-streaming-for-internal-service-communication/scripts` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
