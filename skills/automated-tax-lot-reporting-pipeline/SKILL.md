---
name: automated-tax-lot-reporting-pipeline
description: Ledger engine for automated tax lot matching and capital gains calculation supporting FIFO and HIFO strategies.
domain: tax-accounting-reporting-global
subdomain: tax-reporting
tags:
  - tax
  - reporting
  - fifo
  - hifo
  - capital-gains
  - portfolio-accounting
brokers_frameworks:
  - generic
version: 1.1.0
author: System
license: MIT
---

## When to Use

Use this skill when processing raw execution records to generate compliance-ready capital gains reports. High-frequency algorithmic trading generates massive transaction volumes; computing the cost basis of sold assets manually is impossible. 

This engine ingests trades, maintains an open ledger of "tax lots" (purchased assets), and systematically matches sell orders to these lots. It supports standard **FIFO (First-In, First-Out)** and tax-optimized **HIFO (Highest-In, First-Out)** strategies, including complex logic for splitting lots when a sell order partially consumes a purchase.

## Prerequisites

- Python 3.9+
- A data pipeline providing standardized trade records (Symbol, Timestamp, Action, Quantity, Price).

## Workflow

1. **Strategy Selection**: Configure the pipeline to use `LotMatchingStrategy.FIFO` (default) or `LotMatchingStrategy.HIFO` (for minimizing realized gains).
2. **Ingestion**: Stream `TradeRecord` instances (BUYS and SELLS) into the engine chronologically.
3. **Lot Matching (Sells)**:
   - For every SELL, the engine queries the open ledger for that symbol.
   - It sorts the available lots according to the strategy (by age for FIFO; by cost basis descending for HIFO).
   - It consumes the lots, calculating the Capital Gain (Realized PnL) for each match.
   - If a lot is larger than the sell order, it splits the lot, leaving the remainder in the open ledger.
4. **Reporting**: The engine outputs `RealizedGainRecord` items, which can be aggregated into a standard Form 8949 (Sales and Other Dispositions of Capital Assets).

## Common Pitfalls

- **Ignoring Partial Fills**: Failing to split a lot when a sell order is smaller than the original buy order, leading to double-counting of inventory.
- **Out-of-Order Ingestion**: Feeding trades into the engine out of chronological order will permanently corrupt the FIFO calculations and the ledger balances.

## Verification

Run `python scripts/test_automated_tax_lot_reporting_pipeline.py` to confirm that FIFO and HIFO correctly match different lots and compute different realized PnL totals for the same trade sequence.

## Related Skills

- `wash-sale-rule-tracking-us`
- `fifo-vs-specific-lot-tax-accounting-methods`
