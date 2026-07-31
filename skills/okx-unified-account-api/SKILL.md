---
name: okx-unified-account-api
description: >-
  OKX v5 Unified Account API integration managing HMAC-SHA256 authentication, multi-currency margin mode equity calculations, margin ratio monitoring, and order payload building.
domain: Broker & Exchange Integration
subdomain: Crypto Unified Account & Margin Management
tags: ["okx", "unified-account", "v5-api", "hmac-sha256", "multi-currency-margin", "crypto-derivatives", "margin-ratio"]
brokers_frameworks: ["OKX REST API v5", "Python HMAC & Hashlib", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when integrating trading algorithms with OKX using their v5 Unified Account API structure. OKX Unified Accounts allow cross-margin sharing across spot, futures, perpetual swaps, and options in 4 account modes (Simple, Single-currency, Multi-currency, and Portfolio Margin). Managing this API requires generating HMAC-SHA256 Base64 signatures with passphrase headers (`OK-ACCESS-KEY`, `OK-ACCESS-SIGN`, `OK-ACCESS-TIMESTAMP`, `OK-ACCESS-PASSPHRASE`), computing multi-currency discount-factored USD equity, monitoring margin ratio ($mrr$), and validating trade payloads (`/api/v5/trade/order`).

## Prerequisites

- OKX v5 API Key, Secret Key, and Passphrase.
- Account mode configured to Single/Multi-currency or Portfolio Margin.

## Workflow

1. **HMAC-SHA256 Base64 Signature Generation**:
   - Construct prehash string: `timestamp + method + requestPath + body`.
   - Compute `HMAC-SHA256(secret_key, prehash)` and encode as Base64 string.
   - Attach mandatory headers: `OK-ACCESS-KEY`, `OK-ACCESS-SIGN`, `OK-ACCESS-TIMESTAMP` (ISO UTC `YYYY-MM-DDTHH:MM:SS.sssZ`), `OK-ACCESS-PASSPHRASE`.
2. **Multi-Currency Adjusted Equity & Margin Calculation**:
   - Compute USD equivalent adjusted equity:
     $$USD\_Equity = \sum Equity_k \times Price_k \times DiscountFactor_k$$
   - Calculate Margin Ratio ($mrr = \frac{AdjEquity}{\text{MaintenanceMargin}} \times 100\%$).
3. **Margin Risk Status Audit**:
   - $mrr > 300\% \implies$ `SAFE`.
   - $100\% < mrr \le 300\% \implies$ `MARGIN_WARNING`.
   - $mrr \le 100\% \implies$ `LIQUIDATION_RISK_CALL`.
4. **Audit Report Generation**: Output structured `OKXAccountReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Timestamp Format Mismatch**: Using unix epoch timestamps instead of ISO 8601 UTC strings (`2026-07-31T13:39:00.000Z`) for `OK-ACCESS-TIMESTAMP`.
- **Ignoring Currency Discount Factors**: Summing raw token balances without applying OKX hair-cut discount tiers for multi-currency margin equity calculations.
- **Incorrect `tdMode` in Orders**: Setting `tdMode="cash"` for margin or perpetual swap orders instead of `cross` or `isolated`.

## Verification

- Instantiate `OKXUnifiedAccountEngine`. Verify HMAC-SHA256 signature generation against official test vector prehash string. Input multi-currency balances (BTC & USDT) $\implies$ verify USD equity calculation and $mrr$ status `SAFE`. Input low equity relative to maintenance margin $\implies$ verify `LIQUIDATION_RISK_CALL`.
- Run `python scripts/test_okx_unified_account_api.py`.

## Related Skills

- `binance-futures-testnet-to-mainnet-promotion`
- `kraken-websocket-v2-auth-and-subscriptions`
---
