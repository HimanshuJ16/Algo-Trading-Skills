---
name: idx-indonesia-stock-exchange-api
description: >-
  Quantitative market gateway engine for Indonesia Stock Exchange (IDX / BEI JATS system), enforcing 4-letter tickers, IDX Fraksi Harga tick sizes, and 100-share Board Lots.
domain: Global Market Integration & FX
subdomain: Southeast Asian Market Connectivity & IDX Gateway
tags: ["idx", "indonesia-exchange", "bei", "jats-system", "fraksi-harga", "board-lot", "arb-ara"]
brokers_frameworks: ["JATS (Jakarta Automated Trading System)", "IDX FIX Gateway", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in Southeast Asian market execution algorithms, IDX gateways, and JATS trading systems. Trading on the Indonesia Stock Exchange (IDX / BEI) requires strict adherence to JATS order rules: 4-letter ticker symbols (e.g. `BBCA` Bank Central Asia, `TLKM` Telkom), market board designations (`RG` Regular, `TN` Cash, `NG` Negotiated), dynamic tick sizes based on **Fraksi Harga IDX**, and mandatory **100-share Board Lot** multiples.

## Prerequisites

- IDX order request (`ticker`, `board_type`: `RG`/`TN`/`NG`, `side`, `price`, `quantity`, `reference_price`).
- Official IDX Fraksi Harga tick size schedule.

## Workflow

1. **4-Letter Ticker & Board Type Normalization**:
   - Format ticker to 4 uppercase letters (`BBCA`, `TLKM`).
   - Validate board market type (`RG`, `TN`, `NG`).
2. **IDX Fraksi Harga (Price Fraction / Tick Size) Audit**:
   - Compute dynamic tick size $\Delta P$ based on price tier:
     - $P < \text{Rp } 200 \implies \Delta P = \text{Rp } 1$.
     - $\text{Rp } 200 \le P < \text{Rp } 500 \implies \Delta P = \text{Rp } 2$.
     - $\text{Rp } 500 \le P < \text{Rp } 2,000 \implies \Delta P = \text{Rp } 5$.
     - $\text{Rp } 2,000 \le P < \text{Rp } 5,000 \implies \Delta P = \text{Rp } 10$.
     - $P \ge \text{Rp } 5,000 \implies \Delta P = \text{Rp } 25$.
   - Verify order price is an exact integer multiple of $\Delta P$.
3. **Board Lot Sizing (1 Lot = 100 Shares)**:
   - For `RG` and `TN` markets, verify `quantity` is a multiple of $100$ shares ($1\text{ Lot}$).
4. **Auto-Rejection (ARA / ARB) Price Audit**:
   - Audit price movement within $\pm 25\%$ of reference price.
5. **Audit Report Generation**: Output structured `IdxOrderReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Submitting Invalid Tickers**: Submitting 5-letter or numeric tickers to JATS, causing instant gateway rejection.
- **Ignoring Price-Dependent Fraksi Harga**: Placing orders at Rp 10,005 for a Rp 10,000 stock (where tick size is Rp 25), violating IDX rules.
- **Odd-Lot Submission to Regular Market**: Routing non-100-share multiples to the Regular (`RG`) market board.

## Verification

- Instantiate `IdxStockExchangeApiEngine`. Route Bank Central Asia order (`ticker="BBCA"`, `board="RG"`, Price $=\text{Rp } 10,000$, Qty $=500$ shares / 5 Lots, Ref Price $=\text{Rp } 10,000$). Verify engine validates 4-letter ticker, calculates tick size $\Delta P = \text{Rp } 25$, confirms 5-lot size multiplier, and approves `IDX_ORDER_VALIDATED`.
- Run `python scripts/test_idx_indonesia_stock_exchange_api.py`.

## Related Skills

- `exchange-tick-size-regime-tracking`
- `minimum-fill-size-and-lot-rounding-logic`
---
