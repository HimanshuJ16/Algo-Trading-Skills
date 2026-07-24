---
name: order-book-depth-processing-l2-l3
description: >-
  Use when processing high-frequency Level 2 and Level 3 order book depth feeds to enforce atomic thread safety, prevent crossed-book states, and compute real-time order book imbalance metrics
domain: algorithmic-trading
subdomain: real-time-architecture
tags: ["real-time-architecture", "order-book-l2-l3", "book-imbalance", "weighted-midprice", "thread-safety"]
brokers_frameworks: ["CME ITCH", "Nasdaq TotalView L3", "Coinbase L3", "Binance Depth Stream"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a high-frequency trading bot processes Level 2 (price aggregated) or Level 3 (order-by-order) market data feeds. Multi-threaded WebSocket readers or socket handlers can introduce race conditions if bid and ask updates are applied concurrently without synchronization, resulting in temporary crossed-book states (Best Bid $\ge$ Best Ask) or corrupted order book depth metrics. Implementing atomic thread-safe locking, crossed-book validation, volume-weighted midprice calculation, and order book imbalance metrics is mandatory.

## Prerequisites

- Real-time L2 or L3 WebSocket / ITCH stream handler.
- Thread-safe synchronization primitive (`threading.Lock` or atomic memory region).
- Configured depth level limit (e.g. Top 10 / Top 20 levels).

## Workflow

1. **Acquire Atomic Lock**:
   - Wrap all bid/ask depth mutations within a thread-safe mutex lock (`with self._lock:`).

2. **Process L2 / L3 Updates**:
   - For L2: Apply price level quantity updates ($Q(P) = V$). If $V = 0$, remove price level.
   - For L3: Update order ID mapping (`orders[order_id] = (side, price, size)`). Re-aggregate price levels.

3. **Validate Crossed Book State**:
   - Check top-of-book condition: If $\text{Best Bid} \ge \text{Best Ask}$, flag `CROSSED_BOOK` warning and drop un-synchronized tick.

4. **Compute Microstructure Metrics**:
   - Calculate Volume-Weighted Midprice:
     $$P_{\text{wmid}} = \frac{V_{\text{ask}} \cdot P_{\text{bid}} + V_{\text{bid}} \cdot P_{\text{ask}}}{V_{\text{bid}} + V_{\text{ask}}}$$
   - Calculate Book Imbalance Ratio ($I \in [-1, 1]$):
     $$I = \frac{V_{\text{bid}} - V_{\text{ask}}}{V_{\text{bid}} + V_{\text{ask}}}$$

5. **Expose Atomic Book Snapshot**:
   - Return immutable copy of top $N$ bids and asks for strategy decision engines.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Un-Synchronized Cross-Book Mutations**: Updating bids on thread A and asks on thread B without mutual exclusion, producing transient crossed books.
- **Memory Leaks in L3 Order ID Maps**: Failing to clean up canceled or fully filled order IDs in Level 3 order tracking.
- **Division by Zero in Imbalance Math**: Omitting zero-volume checks when calculating order book imbalance or weighted midprice.

## Verification

- Submit valid L2 depth updates and verify `compute_metrics()` produces accurate weighted midprice and imbalance.
- Submit crossed-book update ($\text{Bid} = 100.5$, $\text{Ask} = 100.0$) and verify `L2L3DepthProcessor` rejects crossed state.
- Submit L3 order add/cancel sequence and verify order ID map matches aggregated price levels.
- Run unit test suite `python scripts/test_depth_processor.py` and confirm 100% pass rate.

## Related Skills

- `market-data-snapshot-plus-delta-reconciliation`
- `producer-consumer-tick-pipeline`
- `microstructure-order-flow-imbalance`
---
