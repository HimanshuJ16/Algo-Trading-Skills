# Broker Integration Standards — broker-side-order-throttle-detection

| Parameter | Specification | Description |
|---|---|---|
| Window Size | 50 orders | Rolling sample count for baseline $\mu, \sigma$ |
| Anomaly Threshold | $Z \ge 3.0$ | Statistical $3\sigma$ latency spike cutoff |
| Absolute Latency Floor | 500 ms | Hard ceiling for silent broker-side throttling |
| Min / Max Backoff | 100 ms to 2000 ms | Adaptive order pacing delay range |

## Category

`broker-integration` — see top-level `mappings/` directory.
