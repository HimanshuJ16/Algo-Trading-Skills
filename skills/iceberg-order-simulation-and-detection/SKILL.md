---
name: iceberg-order-simulation-and-detection
description: >-
  Quantitative market microstructure engine for detecting hidden institutional Iceberg orders by tracking trade volume vs visible book depth discrepancies and repeated price-level refills.
domain: Market Microstructure & Latency
subdomain: Order Flow Toxicity & Iceberg Detection
tags: ["iceberg-detection", "hidden-liquidity", "market-microstructure", "level-2-depth", "order-flow", "institutional-accumulation"]
brokers_frameworks: ["Level 2 Order Book Feeds", "Trade Print Logs", "Bookmap / Sierra Chart", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in high-frequency signal research, order book toxicity monitoring, and institutional volume tracking. Institutional traders deploy hidden "Iceberg" orders to quietly accumulate or distribute large positions without alerting the market. By monitoring trade prints against Level 2 resting depth, this engine detects when cumulative executed volume at a price level exceeds initial displayed depth ($V_{\text{cum}} > 1.5 \times Q_0$), estimating hidden iceberg capacity and classifying `BULLISH_HIDDEN_BUY` vs `BEARISH_HIDDEN_SELL` signals.

## Prerequisites

- Trade print stream (`price`, `quantity`, `side`, `timestamp_nanos`).
- Level 2 order book snapshot stream (`bids`, `asks`, `timestamp_nanos`).
- Detection threshold parameters (`min_volume_ratio = 1.5`, `min_refill_count = 2`).

## Workflow

1. **Trade Print & Book Depth Ingestion**:
   - Ingest tick-by-tick trade executions and track corresponding resting depth at price level $P$.
2. **Cumulative Volume vs Initial Depth Discrepancy Tracking**:
   - Maintain cumulative executed volume $V_{\text{cum}} = \sum Q_{\text{trade}}$ at price $P$.
   - Track depth refills $N_{\text{refills}}$ when depth is consumed but immediately restored.
3. **Iceberg Detection & Capacity Estimation**:
   - If $V_{\text{cum}} \ge 1.5 \times Q_0$ and $N_{\text{refills}} \ge 2$, confirm Iceberg Order presence!
   - Estimate Hidden Size: $\hat{Q}_{\text{hidden}} = V_{\text{cum}} - Q_0$.
4. **Signal Classification**:
   - Side = BUY $\implies$ `BULLISH_HIDDEN_BUY` (Institutional Support Floor).
   - Side = SELL $\implies$ `BEARISH_HIDDEN_SELL` (Institutional Resistance Ceiling).
5. **Audit Report Generation**: Output structured `IcebergDetectionReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Confusing Market Orders with Icebergs**: Flagging a single large aggressive market sweep as an iceberg, rather than requiring repeated refills over time.
- **Ignoring Price Changes**: Failing to reset volume trackers when price moves away from the level, leading to false iceberg detections across multiple prices.
- **Failing to Estimate Hidden Capacity**: Detecting an iceberg without calculating $\hat{Q}_{\text{hidden}}$, missing key input for execution algorithms.

## Verification

- Instantiate `IcebergDetectorEngine`. Simulate price level $\$100.00$ with initial bid depth $Q_0 = 500$. Inject 4 trades of $400$ shares each (Total $1,600$ shares traded) while depth refills $\implies$ verify engine detects `BULLISH_HIDDEN_BUY` iceberg with $\hat{Q}_{\text{hidden}} = 1,100$ shares and $100\%$ confidence.
- Run `python scripts/test_iceberg_order_simulation_and_detection.py`.

## Related Skills

- `historical-order-book-reconstruction-from-message-logs`
- `order-book-microstructure-signal-research`
---
