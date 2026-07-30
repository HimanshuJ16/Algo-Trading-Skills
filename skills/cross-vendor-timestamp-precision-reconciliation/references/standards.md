# Standards for Cross-Vendor Timestamp Precision Reconciliation

| Metric | Engineering Standard |
|---|---|
| Normalized Format | All multi-vendor market data timestamps MUST be stored as 64-bit integer nanoseconds UTC ($t_{\text{ns}}$). |
| Float Precision Prohibition | Timestamp values MUST NOT be converted or stored in IEEE 754 float64 format. |
| Out-Of-Order Tolerance | Ticks with negative time deltas ($\Delta t < 0$) MUST be flagged as `OUT_OF_ORDER`. |
