# Standards for Coinbase Advanced Trade Migration

| Metric | Engineering Standard |
|---|---|
| Side Serialization | Order side MUST be uppercase (`BUY` or `SELL`). Lowercase strings are rejected by Advanced Trade v3. |
| Numeric Precision | `base_size`, `quote_size`, and `limit_price` MUST be formatted as strings with exact decimal precision matching product specifications. |
| Order Type Mapping | `limit` maps to `limit_limit_gtc`, `market` maps to `market_market_ioc`, and `stop` maps to `stop_limit_stop_limit_gtc`. |
