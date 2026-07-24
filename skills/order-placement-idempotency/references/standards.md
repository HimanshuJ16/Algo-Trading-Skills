# Broker & Framework Coverage — order-placement-idempotency

| Broker / Framework | Idempotency Key Field Name & Specs |
|---|---|
| Fyers API v3 | `client_order_id` (alphanumeric string) |
| Zerodha Kite Connect | `tag` (max 20 characters) |
| ICICI Breeze API | `user_remark` / `client_order_id` |
| Upstox API v2 | `tag` parameter |
| Alpaca Trading API | `client_order_id` (UUID format or custom string) |
| IBKR API | `orderId` / `permId` tracking |

## Category

`broker-integration` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with exchange double-fill protection rules, broker client order tracking requirements, and SEBI / FINRA order audit trail rules.
