# Real-Time Architecture Standards — grpc-streaming-for-internal-service-communication

| Metric / Parameter | Protobuf Binary | JSON Text | Advantage |
|---|---|---|---|
| Tick Frame Size | 44 bytes | ~130 bytes | **66% Size Reduction** |
| Transport Protocol | HTTP/2 Multiplexed | HTTP/1.1 or REST | **Low Connection Latency** |
| Schema Enforcement | Strong Typing (`.proto`) | Dynamic / Schema-less | **Zero Runtime Type Mismatch** |

## Category

`real-time-architecture` — see top-level `mappings/` directory.
