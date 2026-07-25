# Broker Integration Standards — api-key-least-privilege-audit-tool

| Role | Required Scopes | Forbidden Scopes | Description |
|---|---|---|---|
| `MARKET_DATA_ONLY` | `read_market_data` | `place_orders`, `withdraw` | Public market data feed reader |
| `EXECUTION_BOT` | `read_market_data`, `place_orders`, `cancel_orders` | `withdraw`, `transfer`, `account_admin` | Live order execution process |
| `PORTFOLIO_MONITOR` | `read_account_info`, `read_positions` | `place_orders`, `withdraw` | Risk & accounting auditor process |

## Category

`broker-integration` — see top-level `mappings/` directory.
