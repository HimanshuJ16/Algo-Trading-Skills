# Broker Integration Standards — sandbox-credential-leakage-prevention

| Broker | Sandbox Key Prefix | Production Key Prefix | Sandbox Gateway | Production Gateway |
|---|---|---|---|---|
| Alpaca | `PK...` | `AK...` | `paper-api.alpaca.markets` | `api.alpaca.markets` |
| Binance | `testnet_` | `live_` | `testnet.binance.vision` | `api.binance.com` |
| Saxo Bank | `sim_` | `prod_` | `gateway.saxobank.com/sim` | `gateway.saxobank.com/openapi` |

## Category

`broker-integration` — see top-level `mappings/` directory.
