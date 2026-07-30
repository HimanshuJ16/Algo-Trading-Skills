---
name: deribit-crypto-options-api
description: >-
  Quantitative Deribit crypto options API engine for JSON-RPC 2.0 formatting, inverse option premium conversions (BTC/ETH to USD), portfolio Greeks aggregation, and margin safety audits.
domain: Decentralized Finance (DeFi) & Crypto Derivatives
subdomain: Crypto Options Trading
tags: ["deribit", "crypto-options", "json-rpc-2.0", "inverse-options", "btc-options", "eth-options", "option-greeks", "mark-iv"]
brokers_frameworks: ["Deribit API v2", "JSON-RPC 2.0", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in crypto quantitative trading systems, options market making bots, and delta-hedging algorithms trading on Deribit ($80\%+$ global crypto options market share). Deribit options utilize **Inverse Contract Specs** where option premiums and margin requirements are denominated in the underlying cryptocurrency (`BTC` or `ETH`), rather than USD. This module formats JSON-RPC 2.0 requests, normalizes inverse option prices to USD, and aggregates portfolio Greeks ($\Delta$, $\Gamma$, Vega).

## Prerequisites

- Deribit API v2 credentials (`client_id`, `client_secret`) or Testnet endpoint.
- Instrument symbol string (e.g. `BTC-28MAR26-60000-C`).
- Underlying index price (e.g. BTC/USD index = \$65,000).

## Workflow

1. **JSON-RPC 2.0 Payload Formatting**:
   - Construct JSON-RPC request (`jsonrpc: "2.0"`, `method: "public/ticker"`, `params: {instrument_name}`).
2. **Inverse Premium to USD Conversion**:
   - $P_{\text{USD}} = P_{\text{coin}} \times S_{\text{index\_usd}}$.
3. **Portfolio Greeks Aggregation**:
   - Delta ($Coin$) = $\text{Qty} \times \text{Delta}_{\text{option}}$.
   - Delta ($USD$) = $\Delta(Coin) \times S_{\text{index\_usd}}$.
4. **Order Dispatch & Margin Audit**:
   - Verify initial margin ($IM_{\text{btc}} \le \text{Available Balance}$) before dispatching `private/buy` or `private/sell`.
5. **Audit Report Generation**: Output structured `DeribitOptionsOrderReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Conflating Premium Quotes in USD**: Treating Deribit premium quotes as USD instead of BTC/ETH, mis-pricing option premiums by a factor of \$65,000.
- **Ignoring Inverse Delta Drift**: Failing to account for coin price movements shifting USD-denominated delta exposure in inverse option contracts.
- **WebSocket Reconnection Drops**: Failing to handle WebSocket session ping/pong heartbeats during fast market volatility.

## Verification

- Instantiate `DeribitCryptoOptionsApiEngine`. Query ticker for `BTC-28MAR26-60000-C` ($P_{\text{btc}} = 0.05$ BTC, Index Price = \$60,000). Verify engine converts premium to \$3,000 USD. Calculate delta exposure for 10 call options ($\delta = 0.60$). Verify coin delta = 6.0 BTC, USD delta = \$360,000.
- Run `python scripts/test_deribit_crypto_options_api.py`.

## Related Skills

- `options-implied-volatility-surface-construction`
- `crypto-exchange-api-integration`
---
