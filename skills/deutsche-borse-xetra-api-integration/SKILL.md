---
name: deutsche-borse-xetra-api-integration
description: "Quantitative venue integration engine for Deutsche B\xF6rse Xetra T7\
  \ Enhanced Trading Interface (ETI), validating MiFID II RTS 28 order parameters,\
  \ T7 tick size regimes, and binary message formatting."
domain: Venue Integration & Protocols
subdomain: European Exchange Integration (Xetra/Eurex)
tags:
- xetra
- t7-eti
- deutsche-borse
- mifid-ii
- tick-size-regime
- binary-protocol
- european-equities
brokers_frameworks:
- "Deutsche B\xF6rse T7 ETI"
- FIX 5.0 SP2
- Python Dataclasses
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in European algorithmic trading systems, broker execution adapters, and high-frequency market making engines connecting to Deutsche Börse Xetra and Eurex. Xetra is Europe's premier electronic venue for equities and ETFs. Order entry is conducted via the **T7 Enhanced Trading Interface (ETI)** using binary FIX 5.0 SP2 semantics. Orders must strictly conform to Xetra tick size regimes and include MiFID II Direct Electronic Access (DEA) short codes and account classification tags (`P` Proprietary, `A` Agent, `M` Market Maker).

## Prerequisites

- T7 ETI session credentials (`session_id`, `party_id_session`, `sender_comp_id`).
- Instrument ISIN / WKN identifier (e.g. `DE0007100000` - Mercedes-Benz Group AG).
- Active Xetra tick size regime table.

## Workflow

1. **T7 ETI Message Construction**:
   - Format binary/FIX 5.0 SP2 header (`msg_type: 10100`, `session_id`, `seq_num`, `sending_time_ns`).
2. **MiFID II Parameter Validation**:
   - Verify `account_type` (`P`, `A`, `M`) and `mifid_short_code` (DEA Trader ID).
3. **Xetra Tick Size Audit**:
   - Audit order price $P$ against Xetra price-band tick rules ($P < €10 \implies \text{Tick}=€0.001$; $€10 \le P < €50 \implies \text{Tick}=€0.005$; $P \ge €50 \implies \text{Tick}=€0.01$).
   - If price violates tick step $\implies$ Reject order (`INVALID_TICK_SIZE`).
4. **Order Execution & T7 Response Processing**: Output structured `XetraOrderExecutionReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Submitting Off-Tick Prices**: Submitting an order for €50.002 on a stock requiring €0.01 tick steps, causing immediate T7 ETI rejection (`10013 Invalid Price Step`).
- **Missing MiFID II DEA Tags**: Omitting mandatory `mifid_short_code` or `account_type` attributes, triggering regulatory compliance rejection.
- **Conflating ETI with Market Data**: Attempting to receive L2 market data on ETI order entry sessions instead of T7 MDI (Market Data Interface) multicast.

## Verification

- Instantiate `DeutscheBorseXetraApiEngine`. Construct order for `DE0007100000` (Price = €62.50, Qty = 500, Account = `'P'`, DEA ShortCode = 99201). Verify engine validates tick size (€0.01 step), formats T7 ETI binary header, and approves dispatch (`STATUS_OK`). Construct order for €62.503 (off-tick). Verify engine flags `INVALID_TICK_SIZE` rejection.
- Run `python scripts/test_deutsche_borse_xetra_api_integration.py`.

## Related Skills

- `exchange-tick-size-regime-tracking`
- `order-to-trade-ratio-fee-penalty-avoidance`
---
