# Standards for ASX Connectivity

| Protocol | Latency Tier | Primary Use Case | Required Topology |
|---|---|---|---|
| **FIX 5.0 SP2** | Milliseconds | Standard algo execution, Drop Copy, Reporting. | ASX Net or ALC |
| **OUCH** | Microseconds | High-Frequency Trading (HFT) order entry. | ALC Co-Location |
| **ITCH** | Microseconds | Market by Order (MBO) full depth data. | ALC Co-Location |

## Category
`global-market-integration`
