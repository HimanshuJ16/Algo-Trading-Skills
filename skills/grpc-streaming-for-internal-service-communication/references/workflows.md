# Deep Workflow Reference — grpc-streaming-for-internal-service-communication

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Protocol Buffer Schema Definition (`.proto`)**:
   - Define message types with explicit field tag IDs.
   - Define bi-directional RPC stream services (`rpc StreamTicks(stream TickRequest) returns (stream TickResponse)`).

2. **HTTP/2 gRPC Channel Establishment**:
   - Initialize persistent connection (`IDLE` $\to$ `CONNECTING` $\to$ `READY`).

3. **Binary Streaming Transmission**:
   - Pack tick data into compact binary frames (44 bytes for standard price/volume tick).
   - Push binary frames continuously over HTTP/2 stream multiplexing.

4. **Serialization Benchmark & Overhead Audit**:
   - Compare Protobuf vs JSON size and serialization latency to verify 50%+ reduction in byte overhead.

## Production Implementation Reference

- Reference code: `scripts/grpc_stream_engine.py` (`GRPCStreamingMarketDataEngine`, `ProtobufTickFrame`, `GRPCChannelState`).
- Automated unit tests: `scripts/test_grpc_stream_engine.py`.
