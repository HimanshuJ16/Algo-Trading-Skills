# Standards for Iceberg Order Routing

| Metric | Engineering Standard |
|---|---|
| Native Routing Preference | Native Iceberg MUST be used whenever supported by broker/exchange. |
| Synthetic Slice Randomization | Synthetic child slices MUST apply $\pm 20\%$ quantity randomization to prevent HFT detection. |
| Refill Latency Tracking | Synthetic client-side refills MUST track and log network latency penalties. |