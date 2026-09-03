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
version: "1.3.0"
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

## When NOT to Use

Do not use this skill when:
- You need to track specific lot identification methods beyond FIFO/HIFO (e.g., Specific Identification, Average Cost)
- **You are reporting under a jurisdiction whose identification rules this engine does not model.** Canada requires a running weighted-average adjusted cost base for identical properties with no FIFO election, and the UK requires same-day then 30-day then s.104 pool matching. FIFO/HIFO output is not a valid basis for either return. See `references/standards.md`.
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

4. **Reporting**: The engine outputs `RealizedGainRecord` items, one per lot matched, which map onto Form 8949 (Sales and Other Dispositions of Capital Assets) rows.
   - Each record contains: sell_trade_id, symbol, quantity_sold, sell_price, buy_lot_id, cost_basis_price, realized_pnl, acquired_timestamp_ms, disposed_timestamp_ms
   - `acquired_timestamp_ms` / `disposed_timestamp_ms` are Form 8949 columns (b) and (c), and are what makes the Part I (short-term) / Part II (long-term) split derivable. The engine does **not** classify the holding period itself — converting epoch milliseconds to a calendar date needs a reporting timezone it does not assume. Apply the count yourself: from the day *after* acquisition through the disposal date, more than one year is long-term.
   - Columns (f)/(g), the adjustment code and amount, are not produced. Wash sales, corporate actions and fees are yours to apply.

## Common Pitfalls

- **Resuming after an oversell as though the shortfall were a rounding nuisance.** As of v1.3.0 the sell is atomic — the match is planned in full before any lot is touched, so an oversell raises with nothing consumed and no `RealizedGainRecord` written, and catching it is safe. That is exactly why you must not swallow it. A shortfall is not noise; it means the trade history feeding the engine is incomplete — a missing buy, a lot booked under the wrong symbol or account, a duplicated sell, or a transfer-in never recorded as an acquisition. Fix the input and replay. Never close the gap by inventing cost basis. (Copies of this helper older than v1.3.0 consumed lots before raising; there, an oversell really is a stop-and-rebuild condition.)
- **Assuming float quantities are exact because lot exhaustion now tolerates dust.** Lot closure is tested against `_QUANTITY_EPSILON` (1e-9), which is what lets a 0.3 position sold as 3 × 0.1 close cleanly instead of stranding 2.78e-17 and reporting an oversell. The tolerance rescues lot *closure*; it does not make the arithmetic exact. Realized PnL, proceeds and basis are still binary floats and still accumulate error over long sequences. Round to the instrument's precision before ingestion, and reconcile reported totals against broker statements rather than trusting the engine's sums to the cent.
- **Feeding trades out of chronological order.** The engine does not enforce ordering, and it re-sorts open lots on every sell. A buy that arrives late with an earlier timestamp will be matched by subsequent FIFO sells, but it cannot retroactively correct gains already realized against the wrong lot. The resulting Form 8949 is wrong in a way no later trade repairs. Sort by `timestamp_ms` upstream.
- **Reporting `realized_pnl` as the taxable figure.** The engine computes raw proceeds minus cost basis. It applies no wash-sale disallowance, no short-/long-term classification (it supplies the two timestamps, but does not apply the holding-period count), and no corporate-action basis adjustment. See `wash-sale-rule-tracking-us`.
- **Assuming HIFO output is by itself an adequate lot identification.** HIFO is not a statutory method; it is a standing instruction under specific identification, and US rules require the identification to exist *no later than the sale*, not to be derived from the executions afterwards. Treas. Reg. §1.1012-1(c)(8) recognises a standing order for securities; for digital assets, §1.1012-1(j)(3)(ii) normally requires specifying units to the custodial broker, temporarily relaxed to the taxpayer's own books and records through 31 December 2026 (Notice 2025-7, extended by Notice 2026-20). Absent an adequate identification the shares are charged against the earliest lot — FIFO. Recording the standing instruction *before* the trades is what makes HIFO output defensible; running this engine in HIFO mode at year end does not. See `fifo-vs-specific-lot-tax-accounting-methods`.
- **Pooling several wallets or accounts into one ledger for digital assets.** Treas. Reg. §1.1012-1(j) applies the identification and FIFO ordering rules to the units held in *each* wallet or account, for acquisitions and dispositions on or after 1 January 2025 — not universally across everything the taxpayer holds. One engine fed from every venue at once produces a universal-basis result that the regulation no longer permits. Instantiate one engine per wallet/account. See `crypto-transaction-tax-lot-tracking`.
- **Replaying a trade and getting a second lot.** `lot_id` is just the incoming `trade_id`, and nothing detects a repeat. Reprocessing the same BUY — a retried batch, an overlapping backfill window, an at-least-once queue — silently creates two lots with the same `lot_id`, doubling the position and making `RealizedGainRecord.buy_lot_id` ambiguous for audit. The engine is not idempotent; deduplicate by `trade_id` upstream before ingestion.
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
- **Overselling**: When sell quantity exceeds available lots, the engine raises ValueError having consumed nothing — no lot is mutated and no gain is recorded
- **First-In Transactions**: First SELL without prior BUY raises ValueError (no naked shorting support)
- **Fractional Shares**: Supported. Lot closure tolerates float dust below `_QUANTITY_EPSILON` (1e-9), so a position bought once and sold in fractional pieces closes cleanly; PnL arithmetic remains float and should still be reconciled
- **Very Large Quantities**: Engine handles large numbers but monitor for floating-point precision issues

## Failure Modes

- **Invalid Input Data**: Engine raises ValueError with descriptive message for any invalid TradeRecord parameter
- **Memory Exhaustion**: Prevented by automatic removal of fully settled lots; monitor open lot count via get_total_open_lot_count()
- **Ledger Corruption**: Caused by out-of-order trade processing; externally ensure chronological ordering
- **Numerical Precision**: Quantities are floats; lot closure is epsilon-tolerant but reported PnL, proceeds and basis are not exact. Non-finite inputs (NaN, ±inf) are rejected at validation, so a NaN can no longer reach `realized_pnl`. Carry `decimal.Decimal` upstream where exact cent-level arithmetic is required
- **Thread Safety**: Engine is not thread-safe; external synchronization required for concurrent access
- **Timestamp Non-Uniqueness**: Engine handles duplicate timestamps correctly (stable sort maintains insertion order for ties in HIFO)

## Recovery

- **Invalid Trade Records**: Validation `ValueError`s are raised by `_validate_trade_record` *before* any ledger mutation, so these are safe to catch, log for manual review, and skip — the ledger is unchanged.
- **Oversell Errors**: An oversell `ValueError` is raised *before* any lot is consumed or gain committed, so the ledger is unchanged and catching it is safe. It is still not something to skip past: the shortfall means the input trade history is wrong. Quarantine the symbol, find the missing or misfiled acquisition, and replay — do not continue processing further sells for that symbol against a ledger you already know disagrees with reality.
- **Memory Growth Alerts**: Monitor open lot count; if growing unexpectedly, investigate for bugs in lot consumption logic
- **Incorrect Lot Accounting**: Re-process trades from known good state after fixing root cause (requires persistent storage of raw trades)
- **Engine Restart**: Upon restart, rebuild ledger by reprocessing all trades from beginning of period (requires trade persistence)
- **Data Gaps**: If missing trades detected, rebuild ledger from last known good checkpoint or full history

## Verification

Run `python -m unittest discover -s skills/automated-tax-lot-reporting-pipeline/scripts` to confirm:
- FIFO and HIFO correctly match different lots and compute different realized PnL totals for the same trade sequence
- Input validation properly rejects invalid trade records with descriptive error messages, including NaN, ±inf and boolean values
- Memory management correctly removes fully settled lots to prevent leaks
- Edge cases like partial fills, multi-lot consumes, and exact lot fills work correctly
- Error conditions (overselling, sells without buys) properly raise exceptions
- **A rejected sell mutates nothing**: an oversell leaves every lot at its prior quantity, writes no `RealizedGainRecord`, and leaves the ledger usable for a corrected sell
- **A fractional position closes cleanly**: 0.3 bought and sold as 3 × 0.1 leaves zero open lots and totals to exactly $3,000 PnL on a $10,000 move
- Every gain record carries the acquisition and disposal timestamps Form 8949 columns (b) and (c) require, following the matched lot under HIFO rather than lot age
- Both strategies produce mathematically correct PnL calculations

## Related Skills

- `wash-sale-rule-tracking-us`: For tracking wash sale violations that affect tax lot accounting
- `fifo-vs-specific-lot-tax-accounting-methods`: For understanding differences between tax lot identification methods
- `crypto-transaction-tax-lot-tracking`: For the per-wallet lot ledger that digital assets require from 1 January 2025
- `sec-rule-15c3-5-risk-controls-us`: For pre-trade risk controls that complement lot accounting
- `execution-realistic-simulation`: For validating trading strategies with realistic market simulation
- `portfolio-construction-with-transaction-cost-awareness`: For incorporating transaction costs in portfolio decisions

## Implementation Notes

The engine provides:
- Deterministic behavior for identical input sequences
- Atomic sells: a match is planned in full before any lot is mutated, so a rejected sell leaves the ledger untouched
- Automatic memory cleanup of settled lots, with epsilon-tolerant closure so float dust cannot strand a depleted lot
- Comprehensive input validation with descriptive error messages, rejecting NaN, ±infinity and boolean values before they reach a gain record
- Clear separation of concerns between matching strategy and ledger management
- Monitoring capabilities for production observability
- Thread-unsafe design requiring external synchronization for concurrent use
- Support for fractional share quantities
- Configurable FIFO/HIFO strategies with extensible design pattern