# Real-Time Architecture Standards — network-interface-level-tick-timestamping

| Timestamping Layer | Socket Option | Typical Jitter | Accuracy Tier |
|---|---|---|---|
| Hardware NIC MAC Layer | `SO_TIMESTAMPING` / Solarflare | $< 50\text{ ns}$ | Highest (Hardware PTP) |
| OS Kernel Network Stack | `SO_TIMESTAMPNS` | $1 - 10\mu\text{s}$ | Medium (Kernel Traversal) |
| Application Layer | `time.time()` | $50 - 500\mu\text{s}$ | Low (OS Context Switch) |

## Category

`real-time-architecture` — see top-level `mappings/` directory.
