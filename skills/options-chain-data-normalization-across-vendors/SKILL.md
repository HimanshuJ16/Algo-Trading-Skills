---
name: options-chain-data-normalization-across-vendors
description: >-
  Options chain data normalization engine mapping heterogeneous vendor feeds (Polygon, IBKR, Bloomberg, OPRA) into standardized OCC OSI 21-character symbology, computing mid-prices, and auditing data integrity.
domain: Data Management & Normalization
subdomain: Global Options Market Data Pipelines
tags: ["options-normalization", "osi-symbology", "occ-format", "options-chain", "ibkr-api", "polygon-options", "bloomberg-options", "market-data"]
brokers_frameworks: ["OCC Option Symbology Initiative (OSI)", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when ingesting options chain market data across multiple brokers and vendors (e.g. Polygon.io, Interactive Brokers, Bloomberg, OPRA). Vendor feeds deliver options chains using different symbology formats, date representations, and field structures. This engine translates proprietary vendor inputs into standard OCC 21-character Option Symbology Initiative (OSI) strings (`AAPL  240119C00150000`), computes mid-prices and bid-ask spreads, and audits contract data integrity.

## Prerequisites

- Raw vendor option contract data payload (`vendor_name`: `'POLYGON'`, `'IBKR'`, `'BLOOMBERG'`, `'OPRA'`, raw fields).
- OCC OSI specification (6-char ticker + YYMMDD + C/P + 8-digit price * 1000).

## Workflow

1. **Vendor Payload Parsing & Symbology Translation**:
   - Parse vendor contract fields:
     - **Polygon**: `O:AAPL240119C00150000` $\implies$ standard OSI.
     - **IBKR**: `{'symbol': 'AAPL', 'expiry': '20240119', 'right': 'C', 'strike': 150.0}` $\implies$ standard OSI.
     - **Bloomberg**: `AAPL US 01/19/24 C150 Equity` $\implies$ standard OSI.
2. **Standard OSI Symbol Generation**:
   - Construct 21-character OSI key:
     $$\text{OSI} = \text{Ticker}_{\text{pad6}} + \text{YYMMDD} + \text{Type} + \text{Strike}_{\text{pad8}}$$
3. **Price Normalization & Data Quality Audit**:
   - Calculate Mid-Price = $(Bid + Ask) / 2$, Spread = $Ask - Bid$.
   - Audit integrity: verify $Bid \le Ask$ and $Strike > 0$. Flag `INVALID_BID_ASK` if inverted quotes occur.
4. **Audit Report Generation**: Output structured `OptionsNormalizationReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Incorrect Strike Price Scale Padding**: Failing to scale strike price $\times 1000$ and zero-pad to 8 digits in OSI format (e.g. $150.00 \implies 00150000$).
- **Space-Padding Misalignment**: Omitting trailing space padding for underlying tickers under 6 characters (e.g. `AAPL` must be `AAPL  `).
- **Ignoring Date Format Discrepancies**: Mixing up `YYYYMMDD` (IBKR) vs `YYMMDD` (OSI) vs `MM/DD/YY` (Bloomberg).

## Verification

- Instantiate `OptionsChainNormalizationEngine`. Ingest raw Polygon contract `O:NVDA240621P00450000` $\implies$ verify OSI translation `NVDA  240621P00450000`. Ingest raw IBKR contract $\implies$ verify exact mid-price and spread calculations. Audit inverted quote ($Bid > Ask$) $\implies$ verify quality alert `INVALID_BID_ASK`.
- Run `python scripts/test_options_chain_data_normalization_across_vendors.py`.

## Related Skills

- `reference-data-symbol-mapping-across-vendors`
- `options-backtesting-with-realistic-iv-surface`
---
