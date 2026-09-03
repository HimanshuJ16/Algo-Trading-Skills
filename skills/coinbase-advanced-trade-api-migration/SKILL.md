---
name: coinbase-advanced-trade-api-migration
description: >-
  Use when porting an order path from the retired Coinbase Pro API onto Coinbase
  Advanced Trade v3, where time-in-force and stop direction fold into a nested
  order_configuration key rather than flat fields.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: global-market-integration
  tags: coinbase, advanced-trade, crypto-api, migration, order-configuration, broker-adapter
  brokers_frameworks: "Coinbase Advanced Trade API v3; Coinbase Pro (Legacy)"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a trading system that spoke to the retired Coinbase Pro API
(`POST /orders` on `api.pro.coinbase.com`, later `api.exchange.coinbase.com`) has to
place the same orders through the **Coinbase Advanced Trade API v3**
(`POST https://api.coinbase.com/api/v3/brokerage/orders`). Coinbase discontinued the
Pro API in favour of Advanced Trade and retired the Pro platform itself in November
2023; the Exchange (institutional) APIs are a separate product that continues to exist.

The migration looks like a field rename and mostly is. It covers the parts that are not,
because each one silently changes what an order does rather than failing loudly:

1. A flat body becomes a nested `order_configuration` keyed by *type and time-in-force
   together* — so a dropped `time_in_force` turns an IOC order into a resting one.
2. The legacy `stop` field (`loss` / `entry`) becomes `stop_direction`, and the mapping
   does not follow from `side`.
3. A legacy market buy sized in quote currency (`funds`) must become `quote_size`, never
   `base_size`.
4. Authentication changes scheme entirely, not just credentials.

## When NOT to Use

- **When the official SDK is adequate.** `coinbase-advanced-py` handles JWT minting,
  transport and retries. Hand-rolling the payload is justified by a specific control
  requirement — an existing order model you are porting, or a translation layer you want
  under test — not by default.
- **For authentication.** This skill's module builds request *bodies* only. Advanced
  Trade auth is a per-request ES256 JWT, a scheme this module deliberately does not
  implement; see Prerequisites.
- **For Coinbase Exchange (institutional) or Coinbase International.** Those are
  different APIs with their own order schemas. Nothing here applies to them.
- **As a market-data or WebSocket migration.** Advanced Trade's WebSocket feed has its
  own auth and channel model. See `websocket-reconnection-with-state-recovery`.
- **For order types with no legacy counterpart** — `trigger_bracket_*`, `twap_limit_gtd`,
  `sor_limit_ioc`, `scaled_limit_gtc`. These are new instructions to design deliberately,
  not the output of a translation.

## Prerequisites

- A **CDP API key created as ECDSA**. Advanced Trade authenticates with a JWT signed
  ES256; Coinbase's documentation states Ed25519/EdDSA keys are **not** supported for
  this API surface. Legacy Coinbase API keys were deprecated on 5 February 2025, and
  legacy Pro keys were deactivated when Pro was retired — an old key/secret/passphrase
  triple cannot be made to work here.
- A JWT minted **per request**, carrying `kid` and a `nonce` in its header and a `uri`
  claim of the form `{METHOD} {HOST}{PATH}`, sent as `Authorization: Bearer <jwt>`. The
  token expires two minutes after issue; one long-lived token reused across a session
  will start failing mid-run.
- The legacy order fields you are porting, **including** `stop`, `time_in_force`,
  `cancel_after`/`funds` — not just `product_id`/`side`/`type`/`price`/`size`. If your
  order model never persisted `stop` and `time_in_force`, recovering them is the first
  migration task, before any translation code.
- Python 3.9+. Standard library only (`decimal`, `uuid`, `logging`). No HTTP client is
  bundled: the module translates, the caller signs and sends.

## Workflow

1. **Populate `LegacyProOrderRequest` from the legacy body, not from a summary of it.**
   `stop`, `funds`, `time_in_force` and `end_time` are the fields most order models drop,
   and they are exactly the ones that carry execution semantics.

2. **Translate with `CoinbaseAdvancedTradeAdapter.translate_order_request()`.** It returns
   the create-order body. Every case it cannot express faithfully raises `ValueError`
   rather than choosing a default:

   - `limit` + GTC → `limit_limit_gtc` (`base_size`, `limit_price`, `post_only`).
   - `limit` + FOK → `limit_limit_fok`. `post_only` with FOK is rejected: an order that
     must fill in full immediately while never taking liquidity cannot fill at all.
   - `limit` + GTT → `limit_limit_gtd`, and `end_time` (RFC3339) is then required.
   - `limit` + IOC → **rejected.** Advanced Trade has no plain limit-IOC configuration.
     The only IOC limit variant, `sor_limit_ioc`, routes through Smart Order Routing and
     is a different execution instruction. Re-express such orders by hand.
   - `market` → `market_market_ioc`: `funds` → `quote_size` (BUY only), `size` →
     `base_size`. A SELL with `funds` is rejected — Advanced Trade sizes market sells in
     base units only, so there is nothing to translate it to.
   - `stop` → `stop_limit_stop_limit_gtc` / `_gtd`, with `stop_direction` taken from the
     legacy `stop` field: `loss` → `STOP_DIRECTION_STOP_DOWN`, `entry` →
     `STOP_DIRECTION_STOP_UP`. A stop order without `stop` is rejected.

3. **Decide the `client_order_id` before you send, not after a failure.** Supply
   `client_oid`. The adapter generates a UUID when it is absent and logs a warning,
   because a generated id is fresh on every call — so a retry after a timeout submits a
   *second distinct order*. A stable id is what makes re-submission safe.

4. **Send the body yourself** to `POST /api/v3/brokerage/orders` with the bearer JWT.

5. **Parse with `parse_v3_response()`, and read the body, not the status code.** A
   rejection arrives as `success: false` inside a response that may still be HTTP 200.
   The adapter raises `AdvancedTradeOrderRejected` (a `RuntimeError` subclass) carrying
   `failure_reason`, `error_details` and `raw_response` so the caller can classify the
   rejection. On success it returns `status="ACCEPTED"` — acceptance, not a live order
   state.

6. **Reconcile before any re-submission.** If the response was lost, ambiguous, or
   reported success without an `order_id`, query
   `GET /api/v3/brokerage/orders/historical/batch` by `client_order_id` and confirm the
   order's absence before sending again. See `order-placement-idempotency`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Inferring `stop_direction` from `side`.** BUY → `STOP_UP` / SELL → `STOP_DOWN` is
  right for the two common cases and inverts the trigger on the other two: a sell
  stop-*entry* sits above the market and a buy stop-*loss* sits below it. An inverted
  stop does not error — it triggers on the wrong side of the price, which for a
  protective stop means it never fires when it is needed.
- **Dropping `time_in_force` during the flatten-to-nested rewrite.** It disappears from
  the top level and reappears inside the configuration key, so a mechanical field-by-field
  port loses it and defaults everything to GTC. A legacy IOC or FOK order then rests on
  the book, holding exposure the strategy believes it never took.
- **Sending a market buy's quote amount as `base_size`.** Legacy `funds="500"` means 500
  USD; as `base_size` it means 500 BTC. Advanced Trade accepts either sizing field for a
  BUY, so nothing rejects the mistake at the schema level.
- **Reusing legacy Coinbase Pro API keys.** They cannot authenticate against Advanced
  Trade, which needs an ECDSA CDP key and a per-request ES256 JWT rather than
  `CB-ACCESS-KEY`/`CB-ACCESS-SIGN`/`CB-ACCESS-PASSPHRASE` HMAC headers.
- **Treating HTTP 200 as an accepted order.** Advanced Trade returns business rejections
  in the body with `success: false`; a client that only checks the status code will record
  rejected orders as live positions.
- **Retrying a timed-out submission with a newly generated `client_order_id`.** The first
  request may have been accepted before the response was lost. A fresh id defeats the one
  duplicate-protection mechanism the API gives you; reconcile by `client_order_id` first.
- **Formatting sizes with `str()` on a float.** `str(1e-8)` is `'1e-08'`, which is not a
  decimal string. The adapter renders every numeric field through `decimal.Decimal` and
  `format(d, 'f')`.
- **Assuming the adapter rounds to the product's increments.** It does not — it never
  fetches product metadata. Sizes and prices must already respect `base_increment`,
  `quote_increment` and the product's minimum size, or Coinbase rejects the order.

## Verification

- Translate a legacy sell stop-*entry* (`side="sell"`, `stop="entry"`) and confirm the
  output carries `STOP_DIRECTION_STOP_UP`, not `STOP_DOWN`.
- Translate a legacy limit order with `time_in_force="IOC"` and confirm it raises rather
  than producing `limit_limit_gtc`.
- Translate a legacy market buy with `funds` and confirm `quote_size` — not `base_size`.
- Parse a `{"success": false, ...}` body and confirm `failure_reason` and `error_details`
  survive onto the raised exception.
- Run `python -m unittest discover -s skills/coinbase-advanced-trade-api-migration/scripts`.

## Related Skills

- `order-placement-idempotency`
- `broker-agnostic-adapter-interface`
- `broker-api-versioning-migration-playbook`
- `crypto-exchange-api-integration`
