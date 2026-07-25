# Real-Time Architecture Standards — market-data-feed-arbitration-across-vendors

| Parameter | Default Value | Description |
|---|---|---|
| Max Divergence Threshold | 0.05% (5 bps) | Max allowable price difference between vendor feeds |
| Max Stale Timeout | 2.0 seconds | Max silence allowed before marking feed stale |
| Primary Fallback Preference | Primary Vendor | Default clean feed when divergence breach occurs |

## Category

`real-time-architecture` — see top-level `mappings/` directory.
