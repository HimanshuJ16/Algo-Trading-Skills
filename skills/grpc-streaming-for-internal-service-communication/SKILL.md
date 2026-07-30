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
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when designing internal microservice communications between trading components (e.g. Strategy Engine $\longleftrightarrow$ Risk Service $\longleftrightarrow$ Execution Gateway). Traditional REST/JSON polling incurs high HTTP connection setup overhead, text-based JSON parsing CPU costs, and polling latency. gRPC over HTTP/2 provides binary Protocol Buffer (protobuf) serialization, multiplexed bi-directional streaming, and sub-millisecond inter-service data transmission.

## Prerequisites

- Protobuf compiler (`protoc`) or Python gRPC runtime.
- Defined `.proto` schema for tick data and order updates.

## Workflow

1. **Define Protocol Buffer Schema (`.proto`)**:
   - Define strongly-typed message structs (`TickRequest`, `TickResponse`, `OrderUpdateStream`).

2. **Establish gRPC HTTP/2 Streaming Channel**:
   - Initialize persistent bi-directional gRPC channel between services.

3. **Continuous Binary Push**:
   - Push binary-encoded protobuf frames over open HTTP/2 stream instead of executing repeated HTTP GET/POST polling.

4. **Benchmark Serialization & Payload Overhead**:
   - Verify protobuf binary size is 3x to 5x smaller than equivalent JSON text payload.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Unmanaged Channel Reconnection**: Failing to handle gRPC channel state changes (`IDLE`, `CONNECTING`, `READY`, `TRANSIENT_FAILURE`).
- **Head-of-Line Blocking on TCP**: Squeezing all internal services into a single TCP connection under heavy network saturation without connection pooling.
- **Ignoring Protobuf Backward Compatibility**: Modifying field tag numbers instead of appending new fields, causing binary deserialization errors in active services.

## Verification

- Benchmark Protobuf vs JSON serialization size and verify 50%+ reduction in byte overhead.
- Simulate bi-directional gRPC stream transmission of 1,000 tick updates and verify zero frame drops.
- Run `python scripts/test_grpc_stream_engine.py` and confirm 100% pass rate.

## Related Skills

- `kafka-based-tick-distribution-at-scale`
- `producer-consumer-tick-pipeline`
- `multi-exchange-feed-normalization`
---
