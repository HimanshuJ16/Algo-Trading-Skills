# Broker Integration Standards — broker-side-order-throttle-detection

| Parameter | Specification | Description |
|---|---|---|
| EWMA Alpha ($\alpha$) | 0.1 to 0.2 | Smoothing factor giving 10-20% weight to newest latency samples |
| Anomaly Threshold | $Z \ge 3.0$ | Statistical $3\sigma$ latency spike cutoff (clamped) |
| Min Variance Clamp | 1.0 ms² | Hard floor to prevent zero-variance division on highly deterministic networks |
| Absolute Latency Floor | 500 ms | Hard ceiling for silent broker-side throttling |
| Min / Max Backoff | 10 ms to 2000 ms | Adaptive order pacing delay limits (AIMD bounds) |
| AIMD Multiplier | x2.0 | Factor to increase backoff when throttled (Multiplicative Decrease of dispatch rate) |
| AIMD Additive Step | -20 ms | Amount to reduce backoff per normal ACK (Additive Increase of dispatch rate) |

## Category

`broker-integration` — see top-level `mappings/` directory.
