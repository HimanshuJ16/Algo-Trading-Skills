# Broker Integration Standards — multi-account-same-strategy-fan-out

| Parameter | Specification | Description |
|---|---|---|
| Allocation Sizing | Pro-Rata NAV Weighting | $Q_i = Q_{\text{master}} \times (\text{NAV}_i / \text{TotalNAV})$ |
| Min Order Quantity | 1 share | Floor to prevent 0 share allocations |
| Client Order ID Format | `CLORD_{account_id}_{timestamp}_{seq}` | Collision-free sub-account identifier |
| Dispatch Pattern | Parallel / Asynchronous Pool | Prevents execution latency skew across sub-accounts |

## Category

`broker-integration` — see top-level `mappings/` directory.
