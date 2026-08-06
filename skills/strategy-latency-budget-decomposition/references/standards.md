# Standards for Strategy Latency Budget Decomposition

| Pipeline Stage | Standard Microsecond Budget SLA |
|---|---|
| Ingress Network | $\le 2.0 \mu\text{s}$ |
| Market Data Decode | $\le 3.0 \mu\text{s}$ |
| Signal Computation | $\le 10.0 \mu\text{s}$ |
| Pre-Trade Risk Check | $\le 5.0 \mu\text{s}$ |
| Egress Order Encode | $\le 5.0 \mu\text{s}$ |
| Total Tick-to-Trade SLA | $\le 25.0 \mu\text{s}$ |
