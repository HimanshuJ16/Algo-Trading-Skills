# Workflows for Coinbase Advanced Trade API Migration

## 1. Recover the full legacy request

Before writing any translation code, confirm the legacy order model still carries the
fields that hold execution semantics. Systems that logged only `product_id`, `side`,
`type`, `price` and `size` have already lost the information the migration needs:

| Legacy field | Why it matters |
|---|---|
| `stop` (`"loss"` / `"entry"`) | Sole source of `stop_direction`. Not derivable from `side`. |
| `time_in_force` (`GTC`/`GTT`/`IOC`/`FOK`) | Selects the Advanced Trade configuration key. Dropping it defaults everything to GTC. |
| `cancel_after` / expiry | Becomes the RFC3339 `end_time` of a GTD configuration. |
| `funds` | A market **buy** sized in quote currency. Must become `quote_size`. |
| `post_only` | Valid only on GTC/GTD limit configurations. |

## 2. Translate

```python
from coinbase_advanced_trade_api_migration import (
    CoinbaseAdvancedTradeAdapter, LegacyProOrderRequest,
)

adapter = CoinbaseAdvancedTradeAdapter()

body = adapter.translate_order_request(
    LegacyProOrderRequest(
        product_id="BTC-USD",
        side="buy",
        type="limit",
        size="0.1",
        price="50000.00",
        post_only=True,
        time_in_force="GTC",
        client_oid="strategy-alpha-2026-08-21-000117",  # stable, not generated
    )
)
```

```json
{
  "client_order_id": "strategy-alpha-2026-08-21-000117",
  "product_id": "BTC-USD",
  "side": "BUY",
  "order_configuration": {
    "limit_limit_gtc": {
      "base_size": "0.1",
      "limit_price": "50000.00",
      "post_only": true
    }
  }
}
```

### The three non-mechanical cases

**Stop direction.** `stop_direction` comes from the legacy `stop` field, never from
`side`:

| Legacy | Advanced Trade | Typical use |
|---|---|---|
| `side="sell"`, `stop="loss"` | `STOP_DIRECTION_STOP_DOWN` | protective stop below the market |
| `side="buy"`, `stop="entry"` | `STOP_DIRECTION_STOP_UP` | breakout entry above the market |
| `side="sell"`, `stop="entry"` | `STOP_DIRECTION_STOP_UP` | sell into strength above the market |
| `side="buy"`, `stop="loss"` | `STOP_DIRECTION_STOP_DOWN` | buy on weakness below the market |

The last two rows are the ones a side-based heuristic gets backwards.

**Time in force.**

| Legacy `type` + `time_in_force` | Advanced Trade key |
|---|---|
| `limit` + `GTC` | `limit_limit_gtc` |
| `limit` + `GTT` | `limit_limit_gtd` (+ `end_time`) |
| `limit` + `FOK` | `limit_limit_fok` (no `post_only`) |
| `limit` + `IOC` | *no equivalent* — the adapter raises; re-express by hand |
| `market` | `market_market_ioc` |
| `stop` + `GTC` | `stop_limit_stop_limit_gtc` |
| `stop` + `GTT` | `stop_limit_stop_limit_gtd` (+ `end_time`) |

**Market sizing.** BUY with `funds` → `quote_size`; BUY with `size` → `base_size`;
SELL → `base_size` only. A SELL carrying `funds` has no faithful translation and is
rejected.

## 3. Authenticate and dispatch

Mint a fresh ES256 JWT for this exact request — the `uri` claim is
`POST api.coinbase.com/api/v3/brokerage/orders`, the header carries `kid` and a `nonce`,
and the token is valid for two minutes. Send:

```
POST https://api.coinbase.com/api/v3/brokerage/orders
Authorization: Bearer <jwt>
Content-Type: application/json
```

Signing and transport are out of scope for this module; use `coinbase-advanced-py` or
your own JWT implementation.

## 4. Parse the response as a body, not a status code

```python
from coinbase_advanced_trade_api_migration import AdvancedTradeOrderRejected

try:
    accepted = adapter.parse_v3_response(http_response.json())
except AdvancedTradeOrderRejected as rejection:
    # rejection.failure_reason / .error_details / .raw_response
    ...
```

`parse_v3_response` returns `status="ACCEPTED"`. That is Coinbase acknowledging the
create request — not a statement that the order is open. Poll
`GET /api/v3/brokerage/orders/historical/{order_id}` for actual state.

## 5. Handle an ambiguous outcome

A timeout, a connection reset, or a `success: true` body with no `order_id` all leave the
same question open: was an order created?

1. Do **not** re-submit with a new `client_order_id`.
2. Query `GET /api/v3/brokerage/orders/historical/batch` filtered by the original
   `client_order_id`.
3. Re-submit the identical body — same `client_order_id` — only once the order is
   confirmed absent.

See `order-placement-idempotency`.
