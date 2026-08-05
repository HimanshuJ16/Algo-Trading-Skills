# Standards for Sandbox Credential Leakage Prevention

| Environment | Permitted API Key Prefixes | Disallowed Target URL Keywords |
|---|---|---|
| SANDBOX | `PK_`, `PAPER_`, `testnet_`, `sim_` | `api.alpaca.markets`, `api.binance.com` |
| PRODUCTION | `AK_`, `LIVE_`, `prod_` | `paper-api.alpaca.markets`, `testnet.binance.vision` |
