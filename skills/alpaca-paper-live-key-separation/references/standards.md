# Broker & Framework Coverage — alpaca-paper-live-key-separation

| Broker / Environment | Base URL Endpoint | Key Prefix | Safety Rules |
|---|---|---|---|
| Alpaca Paper Trading | `https://paper-api.alpaca.markets` | `PK...` | Paper sandbox execution |
| Alpaca Live Trading | `https://api.alpaca.markets` | `AK...` | Requires `ALLOW_LIVE_TRADING=true` |
| Interactive Brokers (IBKR) | Port 7497 (Paper) vs Port 7496 (Live) | Account ID `DU...` vs `U...` | Port & account prefix separation |

## Category

`broker-integration` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with live capital loss protection rules, environment isolation standards, and institutional risk management controls.
