---
name: coinbase-advanced-trade-api-migration
description: Quantitative broker migration adapter for translating legacy Coinbase
  Pro API requests into Coinbase Advanced Trade v3 endpoints and nested order_configuration
  payloads.
domain: Market Connectivity
subdomain: Crypto API
tags:
- coinbase
- advanced-trade
- crypto-api
- migration
- order-configuration
- broker-adapter
brokers_frameworks:
- Coinbase Advanced Trade API v3
- Coinbase Pro (Legacy)
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when upgrading algorithmic crypto trading systems from the sunsetted Coinbase Pro API (legacy v2 `/orders` endpoints) to the **Coinbase Advanced Trade API v3** (`/api/v3/brokerage/orders`). Advanced Trade replaces flat order bodies with a nested `order_configuration` schema (e.g. `limit_limit_gtc`, `market_market_ioc`) and requires updated authentication header formats.

## Prerequisites

- Active Coinbase Cloud Developer Platform (CDP) API Key & Secret.
- Knowledge of legacy Coinbase Pro order fields (`product_id`, `side`, `price`, `size`, `type`).

## Workflow

1. **Request Translation**: Pass legacy order dictionary to `CoinbaseAdvancedTradeAdapter.translate_order_request()`.
2. **Order Configuration Mapping**:
   - `limit` -> `order_configuration.limit_limit_gtc` (with `base_size`, `limit_price`, `post_only`).
   - `market` -> `order_configuration.market_market_ioc` (with `quote_size` for BUY or `base_size` for SELL).
   - `stop` -> `order_configuration.stop_limit_stop_limit_gtc` (with `stop_price`, `limit_price`, `base_size`).
3. **Payload Formatting**: Package into Advanced Trade REST endpoint payload format for `POST /api/v3/brokerage/orders`.
4. **Response Normalization**: Map Advanced Trade v3 response (`order_id`, `success`, `order_configuration`) into standardized execution format.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using Legacy Coinbase Pro API Keys**: Attempting to reuse old Coinbase Pro API keys for Advanced Trade v3 endpoints. They will return HTTP 401 Unauthorized. New CDP keys must be generated.
- **Flat vs. Nested Payload Errors**: Sending flat JSON bodies (`{"price": "50000", "size": "1"}`) to `/api/v3/brokerage/orders`. Advanced Trade requires nesting parameters under `order_configuration.<type>`.
- **Side Case Sensitivity**: Sending lowercase `buy`/`sell` strings instead of uppercase `BUY`/`SELL` required by Advanced Trade.

## Verification

- Instantiate `CoinbaseAdvancedTradeAdapter`. Translate a legacy `limit` order request. Verify that the output JSON strictly matches the Advanced Trade v3 `order_configuration.limit_limit_gtc` format. Translate a legacy `market` order and verify proper `market_market_ioc` mapping.
- Run `python scripts/test_coinbase_advanced_trade_api_migration.py`.

## Related Skills

- `broker-agnostic-adapter-interface`
- `bybit-derivatives-api-integration`
