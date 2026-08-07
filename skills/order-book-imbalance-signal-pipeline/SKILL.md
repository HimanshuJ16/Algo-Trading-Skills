---
name: order-book-imbalance-signal-pipeline
description: Use when building dedicated fast-path pipelines for Level-2 order book
  imbalance (OBI) and micro-price signals, bypassing standard tick aggregation channels
  to achieve sub-microsecond signal generation.
domain: algorithmic-trading
subdomain: real-time-architecture
tags:
- real-time-architecture
- order-book-imbalance
- micro-price
- fast-path
- low-latency
- l2-book
- hft-signals
brokers_frameworks:
- Order Book Imbalance Pipeline
- Python Real-Time Engine
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when building high-frequency market-making or order-book momentum strategies dependent on Order Book Imbalance ($I$) and Micro-Price ($P_{\text{micro}}$) signals. Routing L2 book deltas through general tick-processing, database logging, or REST queues adds milliseconds of latency, missing short-lived liquidity imbalances. This skill establishes a dedicated fast-path pipeline that computes L2 imbalance signals directly in sub-microsecond time.

## Prerequisites

- Direct Level-2 top-of-book data stream (bids and asks with volume depth).
- Imbalance trigger thresholds $I_{\text{long}}$ and $I_{\text{short}}$ (e.g. $+0.60$ and $-0.60$).

## Workflow

1. **Ingest L2 Book Depth directly on Fast-Path**:
   - Intercept top-of-book bids $(P_{\text{bid}}, V_{\text{bid}})$ and asks $(P_{\text{ask}}, V_{\text{ask}})$, bypassing general OHLC logging queues.

2. **Compute Normalized Imbalance & Micro-Price**:
   - Order Book Imbalance:
     $$I = \frac{V_{\text{bid}} - V_{\text{ask}}}{V_{\text{bid}} + V_{\text{ask}}}$$
   - Volume-Weighted Micro-Price:
     $$P_{\text{micro}} = \frac{V_{\text{bid}} \cdot P_{\text{ask}} + V_{\text{ask}} \cdot P_{\text{bid}}}{V_{\text{bid}} + V_{\text{ask}}}$$

3. **Evaluate Fast-Path Imbalance Signal**:
   - $I \ge +0.60$: Emit `HIGH_BUY_PRESSURE` signal.
   - $I \le -0.60$: Emit `HIGH_SELL_PRESSURE` signal.

4. **Dispatch Directly to HFT Execution Worker**:
   - Send signal directly to execution loop, bypassing intermediate message queues.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Mixing Slow-Path Logging on Fast-Path Pipeline**: Adding blocking DB queries or JSON serialization inside the L2 imbalance calculation loop.
- **Ignoring Depth Level 2-5**: Calculating imbalance using only Level-1 top-of-book during thin market depth, causing noise false positives.
- **Dividing by Zero Volume**: Failing to guard against $V_{\text{bid}} + V_{\text{ask}} = 0$ during empty orderbook states.

## Verification

- Submit L2 book ($P_{\text{bid}}=100, V_{\text{bid}}=800$ vs $P_{\text{ask}}=101, V_{\text{ask}}=200$), verify $I = +0.60$ and `HIGH_BUY_PRESSURE` signal.
- Verify sub-microsecond calculation latency.
- Run `python scripts/test_imbalance_pipeline.py` and confirm 100% pass rate.

## Related Skills

- `binary-protocol-parsing-for-low-latency-feeds`
- `memory-mapped-ring-buffer-for-ultra-low-latency`
- `order-book-imbalance-signal-pipeline`
---
