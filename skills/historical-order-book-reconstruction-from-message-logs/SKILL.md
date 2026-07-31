---
name: historical-order-book-reconstruction-from-message-logs
description: >-
  Quantitative market microstructure engine for replaying raw Level 3 (L3 ITCH) message logs (Add, Cancel, Execute, Replace) to reconstruct Level 2 (L2) aggregated order book depth and BBO states.
domain: Data Management Global
subdomain: Market Microstructure & Order Book Reconstruction
tags: ["order-book", "level-3-itch", "level-2-depth", "message-reconstruction", "market-microstructure", "bbo", "bid-ask-spread"]
brokers_frameworks: ["NASDAQ ITCH 5.0", "CME MDP 3.0", "LOBSTER Data", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in market microstructure research, high-frequency backtesting, and queue position simulation. Raw exchange market-by-order (L3) message feeds (NASDAQ ITCH 5.0, CME MDP 3.0) publish individual order lifecycle events (`ADD`, `CANCEL`, `EXECUTE`, `REPLACE`). To backtest limit order strategies without lookahead bias, algorithms must reconstruct the full Level 2 (L2) aggregated price-level order book and Best Bid/Offer (BBO) state tick-by-tick.

## Prerequisites

- Message log event stream (`order_id`, `msg_type`: `ADD`, `CANCEL`, `EXECUTE`, `REPLACE`, `side`: `BUY`/`SELL`, `price`, `quantity`).
- Target book depth level count (e.g. top 5 or 10 price levels).

## Workflow

1. **Level 3 (L3) Message Parsing & Order Map Maintenance**:
   - Maintain active order map: `order_id` $\rightarrow$ `{side, price, quantity}`.
   - Process `ADD`: Insert order into map.
   - Process `CANCEL`: Reduce or delete order from map.
   - Process `EXECUTE`: Reduce filled quantity or remove filled order.
   - Process `REPLACE`: Update price/quantity of existing order.
2. **Level 2 (L2) Aggregated Book Snapshotting**:
   - Aggregate active L3 orders into sorted Bids (descending price) and Asks (ascending price).
   - Calculate Best Bid ($P_{\text{bid}}$), Best Ask ($P_{\text{ask}}$), Mid-Price, and Spread.
3. **Book Integrity & Crossed-Book Audit**:
   - Audit for crossed books ($P_{\text{bid}} \ge P_{\text{ask}}$).
4. **Audit Report Generation**: Output structured `OrderBookReconstructionReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Un-Indexed Order ID Lookups**: Using array scans instead of hash maps (`dict`) for order ID lookups, causing $O(N^2)$ slowdowns during multi-million message replays.
- **Ignoring Partial Cancellations**: Treating `CANCEL` events as full order deletions when the message specifies a partial size reduction.
- **Failing to Audit Crossed Books**: Processing order book snapshots without checking for inverted prices caused by out-of-order message ingestion.

## Verification

- Instantiate `HistoricalOrderBookReconstructEngine`. Replay 4 messages: `ADD Buy ID1 @ 100.0 Qty 10`, `ADD Buy ID2 @ 100.0 Qty 5`, `ADD Sell ID3 @ 101.0 Qty 8`, `CANCEL Buy ID1 Qty 4`. Verify Best Bid $= 100.0$ (Qty $11$), Best Ask $= 101.0$ (Qty $8$), Mid-Price $= 100.50$, Spread $= 1.00$.
- Run `python scripts/test_historical_order_book_reconstruction_from_message_logs.py`.

## Related Skills

- `historical-tick-data-storage-and-compaction`
- `order-book-microstructure-signal-research`
---
