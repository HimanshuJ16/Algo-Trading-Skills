---
name: minimum-fill-size-and-lot-rounding-logic
description: >-
  Execution order lot rounding and minimum fill size engine enforcing board lot step sizes, FIX Tag 110 (MinQty), FIX Tag 1089 (MatchIncrement), and odd-lot penalty prevention.
domain: Execution Algorithms
subdomain: Exchange Order Sizing & Lot Rounding
tags: ["minimum-fill-size", "lot-rounding", "board-lot", "odd-lot", "fix-tag-110", "minqty", "match-increment", "execution-algo"]
brokers_frameworks: ["FIX Protocol 4.2/4.4", "TSE / HKEX / SGX Lot Rules", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when preparing order quantities for market routing across global equity, futures, and crypto exchanges. Global venues enforce strict **Board Lot Rules** (e.g. US Equities 100-share round lots; TSE Japan 100 shares; HKEX 500 or 1,000 shares) and **Minimum Execution Quantities** (`MinQty` / FIX Tag 110). Routing un-rounded odd-lot quantities or orders below venue minimums results in exchange order rejections or severe odd-lot execution fee penalties. This module implements configurable rounding modes (`FLOOR`, `CEIL`, `ROUND_NEAREST`), enforces FIX Tag 110 (`MinQty`) and FIX Tag 1089 (`MatchIncrement`), and prevents odd-lot penalties.

## Prerequisites

- Order rounding configuration (`symbol`, `lot_size`: e.g. 100, `min_qty`: e.g. 100, `rounding_mode`: `'FLOOR'`, `'CEIL'`, `'ROUND_NEAREST'`, `allow_odd_lots`: bool).
- Raw order payload (`order_id`, `symbol`, `raw_quantity`, `limit_price`, `available_liquidity_depth`).

## Workflow

1. **Lot Size Rounding Calculation**:
   - Apply selected rounding mode:
     - `FLOOR`: $Q_{\text{rounded}} = \lfloor \frac{Q_{\text{raw}}}{\text{lot\_size}} \rfloor \times \text{lot\_size}$.
     - `CEIL`: $Q_{\text{rounded}} = \lceil \frac{Q_{\text{raw}}}{\text{lot\_size}} \rceil \times \text{lot\_size}$.
     - `ROUND_NEAREST`: $Q_{\text{rounded}} = \text{round}\left( \frac{Q_{\text{raw}}}{\text{lot\_size}} \right) \times \text{lot\_size}$.
2. **Minimum Fill Size (`MinQty` / FIX Tag 110) Audit**:
   - If $Q_{\text{rounded}} < \text{min\_qty} \implies$ Reject order (`ORDER_REJECTED_BELOW_MIN_QTY`).
   - If `available_liquidity_depth` $< \text{min\_qty} \implies$ Trigger `MIN_QTY_DEPTH_UNSATISFIED`.
3. **Odd-Lot Policy Audit**:
   - If `allow_odd_lots == False` and $Q_{\text{raw}} \bmod \text{lot\_size} \ne 0 \implies$ Adjust to nearest board lot and flag `ODD_LOT_ADJUSTED_TO_ROUND_LOT`.
4. **Audit Report Generation**: Output structured `OrderRoundingReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Submitting Odd Lots to Asian Venues**: Sending 150 shares on TSE or HKEX where board lot is 100 or 500 shares, triggering exchange rejects or illiquid odd-lot book execution.
- **Ignoring FIX Tag 110 Depth Requirements**: Submitting block orders with `MinQty` larger than available book depth, causing perpetual execution delays.
- **Rounding Up Above Risk Limits**: Using `CEIL` rounding on large order sizes, inadvertently breaching maximum portfolio position limits.

## Verification

- Instantiate `MinimumFillSizeAndLotRoundingEngine`. Audit raw order ($Q_{\text{raw}}=275$, `lot_size=100`, `rounding_mode='FLOOR'`, `min_qty=100`) $\implies$ verify $Q_{\text{rounded}}=200$, generates FIX Tag 110 (`MinQty=100`) and FIX Tag 1089 (`MatchIncrement=100`), and approves `LOT_ROUNDING_SUCCESS`. Audit small order ($Q_{\text{raw}}=50 < \text{min\_qty}=100$) under `FLOOR` $\implies$ verify `ORDER_REJECTED_BELOW_MIN_QTY`.
- Run `python scripts/test_minimum_fill_size_and_lot_rounding_logic.py`.

## Related Skills

- `auction-only-order-types-for-illiquid-names`
- `iceberg-order-native-broker-support-vs-simulation`
---
