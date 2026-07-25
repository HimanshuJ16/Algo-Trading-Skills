# Broker Integration Standards — broker-status-page-monitoring-integration

| Broker | Status API URL | Indicator Mapping |
|---|---|---|
| Alpaca | `https://status.alpaca.markets/api/v2/summary.json` | `none` -> OPERATIONAL, `minor` -> DEGRADED, `major`/`critical` -> MAJOR_OUTAGE |
| Coinbase | `https://status.coinbase.com/api/v2/summary.json` | `none` -> OPERATIONAL, `minor` -> DEGRADED, `major`/`critical` -> MAJOR_OUTAGE |

## Category

`broker-integration` — see top-level `mappings/` directory.
