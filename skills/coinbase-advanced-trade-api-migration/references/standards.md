# Standards for Coinbase Advanced Trade Migration

All rows below are taken from Coinbase's own documentation; sources are listed at the
foot of the file. Nothing here is inferred from observed behaviour.

| Area | Requirement |
|---|---|
| Endpoint | Orders are created with `POST https://api.coinbase.com/api/v3/brokerage/orders`. Required body fields: `client_order_id`, `product_id`, `side`, `order_configuration`. |
| Authentication | A JWT bearer token signed **ES256** from an **ECDSA** CDP key. Ed25519/EdDSA keys are not supported for this API. The JWT header carries `kid` and a `nonce`; the payload carries a `uri` claim of the form `{METHOD} {HOST}{PATH}`. The token expires after 2 minutes and a distinct JWT must be generated per request. Legacy Coinbase API keys were deprecated on 2025-02-05. |
| Side serialization | `side` is the enum `BUY` or `SELL`. |
| Numeric serialization | `base_size`, `quote_size`, `limit_price`, `stop_price` are decimal **strings**. They must respect the product's `base_increment` / `quote_increment` and minimum order size; this skill's adapter does not fetch product metadata and does not round. |
| Market sizing | `market_market_ioc` accepts `quote_size` **or** `base_size` for a BUY, and `base_size` only for a SELL. |
| Time in force | Time-in-force is part of the configuration key, not a separate field: `limit_limit_gtc`, `limit_limit_gtd` (requires RFC3339 `end_time`), `limit_limit_fok`, `market_market_ioc`, `market_market_fok`, `stop_limit_stop_limit_gtc`, `stop_limit_stop_limit_gtd`. There is **no** plain limit-IOC configuration; `sor_limit_ioc` is an IOC limit routed through Smart Order Routing and is a different execution instruction. |
| `post_only` | A field of `limit_limit_gtc` and `limit_limit_gtd` only. It is not part of `market_market_ioc`, `limit_limit_fok` or the stop-limit configurations. |
| Stop direction | `stop_direction` is `STOP_DIRECTION_STOP_UP` (trigger when the last trade price goes **above** `stop_price`) or `STOP_DIRECTION_STOP_DOWN` (trigger when it goes **below**). |
| Legacy stop mapping | Legacy Coinbase Pro/Exchange `stop: "loss"` triggers at or **below** `stop_price` → `STOP_DIRECTION_STOP_DOWN`. `stop: "entry"` triggers at or **above** → `STOP_DIRECTION_STOP_UP`. The mapping is independent of `side`. |
| Response shape | `{ "success": bool, "success_response": {...}, "error_response": {...}, "order_configuration": {...} }`. `success_response` carries `order_id` (required), `product_id`, `side`, `client_order_id`. `error_response` carries `message`, `error_details` and `new_order_failure_reason` (`error` and `preview_failure_reason` are deprecated). A `success: false` body is a business rejection, not a transport failure. |
| Order state | The create-order response conveys acceptance only. Live order state comes from `GET /api/v3/brokerage/orders/historical/{order_id}`; lookup by `client_order_id` is available via `GET /api/v3/brokerage/orders/historical/batch`. |

## Not covered here

- **Rate limits.** Advanced Trade's per-second REST limits are quoted inconsistently
  across secondary sources and are not restated here. Confirm the current figures in
  Coinbase's rate-limit documentation before sizing a request budget, and see
  `multi-broker-rate-limit-handling`.
- **Product increments and minimum sizes.** Per-product, and must be read from
  `GET /api/v3/brokerage/products/{product_id}` at runtime.

## Sources

- Advanced Trade REST — Create Order:
  <https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/orders/create-order>
- Advanced Trade — order management guide:
  <https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/guides/orders>
- Coinbase App API key authentication (JWT / ES256):
  <https://docs.cdp.coinbase.com/coinbase-app/authentication-authorization/api-key-authentication>
- Advanced Trade API FAQ (Pro API discontinuation; Exchange APIs unaffected):
  <https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/faq>
- Legacy Coinbase Exchange/Pro `POST /orders` (`stop`, `funds`, `size` semantics):
  <https://docs.cloud.coinbase.com/exchange/docs/apis/post-orders>
