---
name: interactive-brokers-global-multi-exchange-routing
description: >-
  Quantitative order gateway engine for Interactive Brokers (IBKR / TWS API), configuring multi-exchange SmartRouting (NBBO, MaxRebate) vs Direct Routing across global venues (US, Europe, HKEX, Eurex).
domain: Global Market Integration & FX
subdomain: Broker Connectivity & IBKR SmartRouting
tags: ["ibkr", "interactive-brokers", "tws-api", "smart-routing", "nbbo", "primary-exchange", "multi-exchange-routing"]
brokers_frameworks: ["Interactive Brokers TWS API", "ib_insync", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when routing orders through Interactive Brokers (IBKR TWS API / `ib_insync`) across global equity, option, futures, and forex markets. IBKR provides access to over 150 global market centers. Order submission requires precise **IBKR Contract** definitions (`symbol`, `secType`, `currency`, `exchange`, `primaryExchange`), proper **primaryExchange disambiguation** (e.g. `primaryExchange='NASDAQ'` for AAPL), and choosing between **SmartRouting (`SMART`)** for best execution vs **Direct Exchange Routing** (e.g. `exchange='ISLAND'` or `exchange='DTB'`).

## Prerequisites

- IBKR Order Payload (`symbol`, `sec_type`: `STK`/`OPT`/`FUT`/`CASH`, `currency`, `exchange`, `primary_exchange`, `routing_mode`, `order_type`, `action`, `quantity`, `lmt_price`).
- Regional IBKR venue mapping rules (US `USD`/`SMART`, EU `EUR`/`IBIS`, HK `HKD`/`SEHK`).

## Workflow

1. **IBKR Contract Payload Ingestion**:
   - Ingest order parameters and construct IBKR `Contract` representation.
2. **Regional Venue & Symbol Format Validation**:
   - US Equities: `currency='USD'`, `exchange='SMART'`, `primaryExchange='NASDAQ'`/`'NYSE'`.
   - European Equities: `currency='EUR'`, `exchange='SMART'`, `primaryExchange='IBIS'` (Xetra)/`'FWB'`.
   - HKEX Equities: `currency='HKD'`, `exchange='SEHK'`, 5-digit numeric ticker (`00700`).
3. **SmartRouting vs Direct Routing Selection**:
   - Mode `SMART_BEST_EXECUTION`: Sets `exchange='SMART'` with primaryExchange hint.
   - Mode `DIRECT_EXCHANGE`: Sets explicit venue (e.g. `exchange='ISLAND'`).
4. **Audit Report Generation**: Output structured `IbkrRoutingReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Omitting `primaryExchange` Disambiguation**: Omitting `primaryExchange` on SmartRouted orders, causing contract ambiguity errors when tickers trade on multiple global exchanges.
- **Using Non-Numeric Tickers for HKEX**: Passing `700` instead of 5-digit string `00700` to IBKR for Hong Kong stocks.
- **Routing Unsupported Currency/Exchange Combinations**: Submitting `currency='EUR'` to `exchange='ISLAND'` (US venue), causing instant IBKR gateway rejection.

## Verification

- Instantiate `IbkrGlobalRoutingEngine`. Route US Equity (`symbol="AAPL"`, `secType="STK"`, `currency="USD"`, `exchange="SMART"`, `primaryExchange="NASDAQ"`, `mode="SMART_BEST_EXECUTION"`) $\implies$ verify engine approves `IBKR_ROUTING_VALIDATED`. Route HKEX Equity (`symbol="00700"`, `currency="HKD"`, `exchange="SEHK"`) $\implies$ verify 5-digit format validation and `IBKR_ROUTING_VALIDATED`. Audit Invalid Currency Mismatch $\implies$ verify `REJECTED_CURRENCY_MISMATCH`.
- Run `python scripts/test_interactive_brokers_global_multi_exchange_routing.py`.

## Related Skills

- `ibkr-tws-gateway-headless-launch`
- `broker-agnostic-adapter-interface`
---
