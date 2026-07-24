# Broker & Framework Coverage — webhook-based-order-fill-notifications

| Broker / Webhook Service | Signature Method | Unique Execution ID Field |
|---|---|---|
| TradeStation WebAPI | HMAC-SHA256 (`X-Hub-Signature`) | `ExecutionID` / `FillID` |
| Alpaca Trading | HMAC-SHA256 (`X-Alpaca-Signature`) | `execution_id` |
| Coinbase Advanced | HMAC-SHA256 (`CB-ACCESS-SIGN`) | `trade_id` |

## Category

`broker-integration` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with web application security (OWASP Webhook Security), trade execution audit trailing, and idempotent processing standards.
