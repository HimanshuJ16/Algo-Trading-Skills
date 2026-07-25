# Real-Time Architecture Standards — feed-handler-cpu-pinning-and-numa-awareness

| Optimization Tier | Mechanism | Latency Reduction |
|---|---|---|
| CPU Pinning | `psutil.Process().cpu_affinity([core])` | Eliminates OS context switching |
| NUMA Locality | `numactl --membind=node` | Prevents cross-socket QPI/UPI bus delays |
| Core Isolation | `isolcpus` kernel boot parameter | Reserves core exclusively for feed handler |

## Category

`real-time-architecture` — see top-level `mappings/` directory.
