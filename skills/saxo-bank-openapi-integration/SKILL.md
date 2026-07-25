---
name: saxo-bank-openapi-integration
description: >-
  Use when connecting to Saxo Bank OpenAPI for multi-asset trading (Equities, FX, Futures, Options, CFDs) to execute OAuth2 authentication, multi-asset instrument lookup, order placement, and portfolio position tracking.
domain: algorithmic-trading
subdomain: broker-integration
tags: ["broker-integration", "saxo-bank", "multi-asset", "openapi", "fx-trading", "global-markets"]
brokers_frameworks: ["Saxo Bank OpenAPI", "Python requests", "WebSockets"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when building multi-asset quantitative trading systems that trade global equities, foreign exchange (FX Spot/Forwards), futures, options, and CFDs via Saxo Bank's OpenAPI infrastructure. Saxo Bank provides institutional REST and streaming WebSocket endpoints across 50+ global exchanges.

## Prerequisites

- Saxo Bank developer account (Simulation / Live application key & secret).
- OAuth2 Access Token / Refresh Token.
- Asset UICs (Universal Instrument Codes) for target instruments.

## Workflow

1. **OAuth2 Bearer Authentication**:
   - Obtain Bearer token via OAuth2 PKCE / Code Authorization flow.
   - Configure target endpoint gateway (`https://gateway.saxobank.com/sim/openapi/` for SIM, or `/openapi/` for LIVE).

2. **Instrument Reference Search (`/ref/v1/instruments`)**:
   - Query instruments by keyword and `AssetType` (`FxSpot`, `Stock`, `ContractFutures`, `OptionRoot`).
   - Extract Saxo Universal Instrument Code (`Uic`).

3. **Multi-Asset Order Placement (`/trade/v1/orders`)**:
   - Construct order payload with `Uic`, `AssetType`, `OrderType` (`Market`, `Limit`, `StopIfTraded`), `OrderDuration` (`DayOrder`, `GoodTillCancel`), `Amount`, and `Price`.
   - Post to `/trade/v1/orders` with `Authorization: Bearer {TOKEN}`.

4. **Portfolio Position & Margin Ingestion (`/port/v1/positions`)**:
   - Query live portfolio position snapshot across all asset classes.

> Full step-by-step procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **AssetType Misconfiguration**: Passing `Stock` instead of `FxSpot` for currency pairs results in order rejection.
- **UIC vs Ticker Confusion**: Saxo uses numeric UICs (Universal Instrument Codes), not standard exchange tickers.
- **Account Key vs Account ID**: Saxo API calls require `AccountKey` (encrypted string), not the raw account number.

## Verification

- Query instrument search for FX and Stock symbols and verify UIC resolution.
- Construct multi-asset order payload and verify validation.
- Run `python scripts/test_saxo_client.py` and confirm 100% pass rate.

## Related Skills

- `broker-agnostic-adapter-interface`
- `multi-currency-pnl-and-fx-conversion`
- `forex-broker-integration-oanda-mt5`
---
