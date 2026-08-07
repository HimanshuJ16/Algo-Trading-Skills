---
name: eurex-market-data-and-order-api
description: Quantitative derivatives venue engine for parsing Eurex T7 EMDI multicast
  market data depth feeds, formatting T7 ETI binary order entry payloads, and enforcing
  futures contract tick rules and price reasonability bands.
domain: Venue Integration & Protocols
subdomain: European Derivatives (Eurex T7)
tags:
- eurex
- t7-eti
- t7-emdi
- futures-trading
- euro-stoxx-50
- euro-bund
- binary-protocol
- fix-5.0-sp2
brokers_frameworks:
- Eurex T7 ETI
- T7 EMDI Multicast
- Python Dataclasses
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in European derivatives trading desks, high-frequency futures market making, and cross-asset hedging engines connecting to Eurex. Eurex is Europe's leading financial derivatives exchange, hosting flagship contracts like EURO STOXX 50 futures (`FESX`), Euro-Bund futures (`FGBL`), and Euro-Bobl futures (`FGBM`). Market data is ingested via **T7 EMDI** (Enhanced Market Data Interface) UDP multicast feeds, while order entry is conducted via **T7 ETI** (Enhanced Trading Interface) using binary FIX 5.0 SP2 semantics.

## Prerequisites

- T7 ETI session credentials (`session_id`, `party_id_session`, `sender_comp_id`).
- Eurex futures contract symbol (e.g. `FESX_202609` - EURO STOXX 50 Sep 2026 Future).
- Active Eurex price reasonability limits (e.g. 50 index points for `FESX`).

## Workflow

1. **T7 EMDI Market Data Parsing**:
   - Parse order book depth levels (Best Bid/Ask, Depth L2-L5, Mid-Price, Imbalance).
2. **Eurex Contract Tick Rule & Value Calculation**:
   - `FESX`: Minimum tick = $1.0\text{ point}$, contract multiplier = $€10.00/\text{point}$.
   - `FGBL`: Minimum tick = $0.01\%$, contract multiplier = $€1,000.00/\text{point}$.
3. **Price Reasonability Band Audit**:
   - Audit order price $P$ against current mid-price $P_{\text{mid}}$ ($\Delta P \le \text{Max Reasonability Deviation}$).
   - If price exceeds band $\implies$ Reject order (`PRICE_REASONABILITY_BREACH`).
4. **T7 ETI Binary Message Construction**:
   - Build binary header (`template_id: 10100` NewOrderSingle, `session_id`, `sequence_no`).
5. **Audit Report Generation**: Output structured `EurexOrderExecutionReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Submitting Off-Tick Prices**: Submitting an order price of 4850.5 on `FESX` (which has a 1.0 full-point tick step), triggering immediate T7 ETI rejection.
- **Fat-Finger Price Breaches**: Submitting limit orders outside Eurex price reasonability bands during market spikes.
- **Un-Reconciled Private ETI Fills with Public EMDI**: Failing to correlate ETI execution report `ExecID` with public EMDI trade prints.

## Verification

- Instantiate `EurexMarketDataAndOrderApiEngine`. Parse EMDI depth for `FESX_202609` (Best Bid = 4850.0, Best Ask = 4851.0, Mid = 4850.5). Construct BUY order for 10 contracts @ 4851.0. Verify engine validates 1.0 tick step, passes price reasonability check, calculates nominal value (€485,100), and formats T7 ETI binary payload. Submit off-tick price (4851.5). Verify engine flags `INVALID_TICK_SIZE`.
- Run `python scripts/test_eurex_market_data_and_order_api.py`.

## Related Skills

- `deutsche-borse-xetra-api-integration`
- `synthetic-continuous-futures-contract-construction`
---
