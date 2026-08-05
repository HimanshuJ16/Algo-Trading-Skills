---
name: saxo-bank-openapi-integration
description: >-
  Production-grade client for Saxo Bank OpenAPI covering multi-asset UIC instrument resolution, order routing (FX Spot, Equities, Futures, Options), and portfolio position tracking across simulation and live environments.
domain: Broker Integration & Connectivity
subdomain: Multi-Asset OpenAPI Connectivity
tags: ["saxo-bank", "openapi", "multi-asset", "fx-spot", "options-trading", "uic-resolution"]
brokers_frameworks: ["Saxo Bank OpenAPI REST", "OAuth2 Bearer Token", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when connecting algorithmic trading systems to Saxo Bank using their multi-asset OpenAPI REST endpoints. Saxo Bank OpenAPI enables cross-asset trading across FX Spot, Global Stocks, Contract Futures, Options, and Stock CFDs. Unlike single-asset brokers, Saxo Bank identifies instruments via numeric Unique Instrument Codes (UICs) and requires explicit `AssetType` declarations on order payloads.

## Prerequisites

- Saxo OpenAPI OAuth2 Access Token (`access_token`) and Account Key (`account_key`).
- Target environment (`is_simulation`: True for `gateway.saxobank.com/sim/openapi`, False for live `gateway.saxobank.com/openapi`).

## Workflow

1. **UIC Instrument Resolution**:
   - Query `/ref/v1/instruments?Keywords={ticker}&AssetTypes={asset_type}` to resolve ticker symbols to Saxo numeric UICs.
2. **Multi-Asset Order Placement**:
   - Issue POST request to `/trade/v1/orders` specifying `AccountKey`, `Uic`, `AssetType`, `BuySell`, `Amount`, `OrderType`, and `OrderDuration`.
3. **Position & P&L Retrieval**:
   - Query `/port/v1/positions?AccountKey={account_key}` to parse positions, average open prices, and unrealized P&L.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Unresolved UIC Identifiers**: Passing ticker string identifiers directly into order endpoints instead of performing UIC resolution first.
- **Incorrect AssetType Enum Values**: Passing string `"Equity"` instead of Saxo's exact enum string `"Stock"` or `"FxSpot"`.
- **Token Expiration During Active Trading**: Failing to refresh the 24-hour OAuth2 bearer token before executing live trade orders.

## Verification

- Instantiate `SaxoBankOpenAPIClient`. Search instrument "EURUSD" (FxSpot) $\implies$ verify UIC 211 returned. Place limit buy order $\implies$ verify OrderId returned with status "Placed". Query positions $\implies$ verify unrealized P&L parsed.
- Run `python scripts/test_saxo_client.py`.

## Related Skills

- `broker-agnostic-adapter-interface`
- `sandbox-credential-leakage-prevention`
---
