---
name: automated-tax-lot-reporting-pipeline
description: Ledger engine for automated tax lot matching and capital gains calculation
  supporting FIFO and HIFO strategies.
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
version: "1.2.0"
author: System
license: MIT
---

## When to Use

Use this skill when processing raw execution records to generate compliance-ready capital gains reports. High-frequency algorithmic trading generates massive transaction volumes; computing the cost basis of sold assets manually is impossible. 

This engine ingests trades, maintains an open ledger of "tax lots" (purchased assets), and systematically matches sell orders to these lots. It supports standard **FIFO (First-In, First-Out)** and tax-optimized **HIFO (Highest-In, First-Out)** strategies, including complex logic for splitting lots when a sell order partially consumes a purchase.

**Appropriate scenarios:**
- Processing trade executions from broker APIs or FIX feeds
- Generating Form 8949 (Sales and Other Dispositions of Capital Assets) for tax reporting
- Calculating realized PnL for portfolio performance reporting
- Implementing tax-lot-level accounting in trading systems
- Supporting both FIFO (default IRS method) and HIFO (tax optimization) strategies

## When Not to Use

Do not use this skill when:
- You need to track specific lot identification methods beyond FIFO/HIFO (e.g., Specific Identification, Average Cost)
- Your jurisdiction requires specific lot tracking methods not supported by FIFO/HIFO
- You need to handle complex corporate actions like spin-offs or mergers that require special lot treatment
- You are dealing with non-equity instruments that have different lot matching rules (options, futures)
- You require real-time lot matching with sub-millisecond latency requirements (consider specialized C++ implementations)
- You need to integrate with specific tax accounting software that requires proprietary lot ID formats

## Prerequisites

- Python 3.9+
- A data pipeline providing standardized trade records (Symbol, Timestamp, Action, Quantity, Price)
- Trade records must be processed in strict chronological order by timestamp_ms
- Understanding of tax lot accounting principles (FIFO/HIFO)
- Knowledge of your jurisdiction's tax lot identification requirements

## Workflow

1. **Strategy Selection**: Configure the pipeline to use `LotMatchingStrategy.FIFO` (default) or `LotMatchingStrategy.HIFO` (for minimizing realized gains).

2. **Ingestion**: Stream `TradeRecord` instances (BUYS and SELLS) into the engine chronologically.
   - Each trade must have: trade_id (non-empty string), symbol (non-empty string), action (BUY/SELL), quantity (>0), price (>=0), timestamp_ms (non-negative integer)
   - Trades must be processed in strictly increasing timestamp order to maintain lot accounting integrity

3. **Lot Matching (Sells)**:
   - For every SELL, the engine queries the open ledger for that symbol.
   - It sorts the available lots according to the strategy (by age for FIFO; by cost basis descending for HIFO, then by age for ties).
   - It consumes the lots, calculating the Capital Gain (Realized PnL) for each match.
   - If a lot is larger than the sell order, it splits the lot, leaving the remainder in the open ledger.
   - Fully consumed lots are automatically removed from memory to prevent memory leaks in long-running processes.

4. **Reporting**: The engine outputs `RealizedGainRecord` items, which can be aggregated into a standard Form 8949 (Sales and Other Dispositions of Capital Assets).
   - Each record contains: sell_trade_id, symbol, quantity_sold, sell_price, buy_lot_id, cost_basis_price, realized_pnl

## Common Pitfalls

- **Treating a caught `ValueError` as "the trade didn't happen".** `_handle_sell` consumes lots and appends to `self.realized_gains` *as it goes*, and only raises the oversell error after the loop. Selling 15 units against a single 10-unit lot raises — but the lot is gone and a 10-unit `RealizedGainRecord` is already committed. Catching the exception and continuing leaves the ledger silently diverged from reality. Validation errors from `_validate_trade_record` are safe to catch (they raise before any mutation); oversell errors are not. Treat an oversell as a stop-and-rebuild condition, not a skippable record.
- **Assuming a fully-covered fractional position can be sold in pieces.** Lot removal tests `remaining_quantity == 0` exactly. Buying 0.3 and selling 3 × 0.1 leaves `-2.78e-17` on the lot instead of zero, so the lot is never removed and the third sell raises `Oversold ... Remaining 2.7755575615628914e-17 units`. Round quantities to the instrument's precision before ingestion, or hold quantities in `decimal.Decimal` upstream. This bites crypto and fractional-share flows, not whole-share equity flows.
- **Feeding trades out of chronological order.** The engine does not enforce ordering, and it re-sorts open lots on every sell. A buy that arrives late with an earlier timestamp will be matched by subsequent FIFO sells, but it cannot retroactively correct gains already realized against the wrong lot. The resulting Form 8949 is wrong in a way no later trade repairs. Sort by `timestamp_ms` upstream.
- **Reporting `realized_pnl` as the taxable figure.** The engine computes raw proceeds minus cost basis. It applies no wash-sale disallowance, no holding-period split between short- and long-term, and no corporate-action basis adjustment. See `wash-sale-rule-tracking-us`.
- **Assuming HIFO output is by itself an adequate lot identification.** Computing highest-in-first-out after the fact is a reporting choice; whether it is *accepted* depends on your jurisdiction's rules for identifying lots at the time of sale, which this engine does not model or evidence. Confirm the requirement before filing — see `fifo-vs-specific-lot-tax-accounting-methods`.
- **Sharing one engine across threads.** `open_lots` and `realized_gains` are mutated without locking, and a sell is a multi-step read-modify-write. Concurrent access corrupts the ledger rather than raising. Use one engine per worker, or serialize access externally.

## Decision Points

- **Strategy Selection**: Choose FIFO for regulatory compliance (IRS default) or HIFO for tax optimization (minimizing realized gains)
- **Error Handling**: Decide whether to catch validation errors (invalid trade data) or let them propagate upstream
- **Memory Monitoring**: Use `get_open_lot_count()` and `get_total_open_lot_count()` to monitor ledger size in long-running processes
- **Timestamp Validation**: Ensure your data source provides monotonic timestamps to prevent ledger corruption
- **Handling Unmatched Sells**: Determine business logic for when sells exceed available lots (engine raises ValueError)

## Edge Cases

- **Partial Lot Fills**: When sell quantity < lot quantity, the lot is split and remainder retained
- **Exact Lot Fills**: When sell quantity = lot quantity, the lot is fully consumed and removed from memory
- **Multi-Lot Fills**: When sell quantity spans multiple lots, lots are consumed in strategy order until filled
- **Zero Quantity Trades**: Rejected during validation (quantity must be positive)
- **Negative Prices**: Rejected during validation (price must be non-negative)
- **Invalid Timestamps**: Rejected during validation (timestamp must be non-negative integer)
- **Missing Required Fields**: Rejected during validation (all fields must be present and valid)
- **Out-of-Order Processing**: While engine doesn't prevent this, results will be incorrect - external sorting required
- **Overselling**: When sell quantity > available lots, engine raises ValueError after consuming all available lots
- **First-In Transactions**: First SELL without prior BUY raises ValueError (no naked shorting support)
- **Fractional Shares**: Supported with floating-point precision monitoring recommended for high-volume scenarios
- **Very Large Quantities**: Engine handles large numbers but monitor for floating-point precision issues

## Failure Modes

- **Invalid Input Data**: Engine raises ValueError with descriptive message for any invalid TradeRecord parameter
- **Memory Exhaustion**: Prevented by automatic removal of fully settled lots; monitor open lot count via get_total_open_lot_count()
- **Ledger Corruption**: Caused by out-of-order trade processing; externally ensure chronological ordering
- **Numerical Precision**: Floating-point errors possible in extreme high-frequency scenarios; consider decimal module for financial precision if needed
- **Thread Safety**: Engine is not thread-safe; external synchronization required for concurrent access
- **Timestamp Non-Uniqueness**: Engine handles duplicate timestamps correctly (stable sort maintains insertion order for ties in HIFO)

## Recovery

- **Invalid Trade Records**: Validation `ValueError`s are raised by `_validate_trade_record` *before* any ledger mutation, so these are safe to catch, log for manual review, and skip — the ledger is unchanged.
- **Oversell Errors**: An oversell `ValueError` is raised *after* lots have been consumed and gains committed, so it is **not** safe to catch and continue. Stop the pipeline and rebuild the ledger from the last known good checkpoint; resuming leaves the ledger diverged from the trade history.
- **Memory Growth Alerts**: Monitor open lot count; if growing unexpectedly, investigate for bugs in lot consumption logic
- **Incorrect Lot Accounting**: Re-process trades from known good state after fixing root cause (requires persistent storage of raw trades)
- **Engine Restart**: Upon restart, rebuild ledger by reprocessing all trades from beginning of period (requires trade persistence)
- **Data Gaps**: If missing trades detected, rebuild ledger from last known good checkpoint or full history

## Verification

Run `python scripts/test_automated_tax_lot_reporting_pipeline.py` to confirm:
- FIFO and HIFO correctly match different lots and compute different realized PnL totals for the same trade sequence
- Input validation properly rejects invalid trade records with descriptive error messages
- Memory management correctly removes fully settled lots to prevent leaks
- Edge cases like partial fills, multi-lot consumes, and exact lot fills work correctly
- Error conditions (overselling, sells without buys) properly raise exceptions
- Both strategies produce mathematically correct PnL calculations

## Related Skills

- `wash-sale-rule-tracking-us`: For tracking wash sale violations that affect tax lot accounting
- `fifo-vs-specific-lot-tax-accounting-methods`: For understanding differences between tax lot identification methods
- `sec-rule-15c3-5-risk-controls-us`: For pre-trade risk controls that complement lot accounting
- `execution-realistic-simulation`: For validating trading strategies with realistic market simulation
- `portfolio-construction-with-transaction-cost-awareness`: For incorporating transaction costs in portfolio decisions

## Implementation Notes

The engine provides:
- Deterministic behavior for identical input sequences
- Automatic memory cleanup of settled lots
- Comprehensive input validation with descriptive error messages
- Clear separation of concerns between matching strategy and ledger management
- Monitoring capabilities for production observability
- Thread-unsafe design requiring external synchronization for concurrent use
- Support for fractional share quantities
- Configurable FIFO/HIFO strategies with extensible design pattern