# Real-Time Architecture Standards — kafka-based-tick-distribution-at-scale

| Parameter | Specification | Description |
|---|---|---|
| Partition Strategy | Symbol Key Hashing | MD5(symbol) % N_partitions |
| Producer Linger Delay | 5 ms | Throughput vs latency trade-off |
| Compression Codec | Snappy / LZ4 | High-speed compression |
| Offset Commit | Manual post-batch commit | Prevents tick loss on consumer failure |

## Category

`real-time-architecture` — see top-level `mappings/` directory.
