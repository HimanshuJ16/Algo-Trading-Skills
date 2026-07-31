---
name: order-book-microstructure-signal-research
description: >-
  Order book microstructure signal research engine calculating Order Flow Imbalance (OFI), Volume-Weighted Micro-Price, and Depth Imbalance, and evaluating predictive Information Coefficients (IC).
domain: Market Microstructure & Signal Research
subdomain: High-Frequency Order Book Signal Analytics
tags: ["microstructure", "ofi", "order-flow-imbalance", "micro-price", "depth-imbalance", "hft-signals", "quant-research"]
brokers_frameworks: ["CME / Nasdaq L2 Feed Spec", "Python Dataclasses", "Math"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when researching or backtesting high-frequency market microstructure signals derived from limit order book dynamics. Traditional technical indicators (RSI, MACD) are too slow for high-frequency trading. Order Flow Imbalance ($OFI$), Depth Volume Imbalance ($VOI$), and Micro-Price deviation ($\Delta P_{\text{micro}}$) capture immediate supply/demand imbalances at the top of the book. This engine computes rolling microstructure signals and measures their predictive Information Coefficient ($IC$) against short-horizon forward returns.

## Prerequisites

- Sequence of order book ticks (`timestamp_ns`, `bid_price`, `bid_qty`, `ask_price`, `ask_qty`).
- Target forward return horizon $k$ (e.g. 5 ticks / 100ms).

## Workflow

1. **Microstructure Feature Extraction**:
   - **Order Flow Imbalance ($OFI$)**:
     $$OFI_t = \Delta V_{\text{bid}, t} - \Delta V_{\text{ask}, t}$$
   - **Depth Volume Imbalance ($VOI$)**:
     $$VOI_t = \frac{V_{\text{bid}, t} - V_{\text{ask}, t}}{V_{\text{bid}, t} + V_{\text{ask}, t}}$$
   - **Micro-Price Deviation ($\Delta P_{\text{micro}}$)**:
     $$P_{\text{micro}} = \frac{V_{\text{bid}} P_{\text{ask}} + V_{\text{ask}} P_{\text{bid}}}{V_{\text{bid}} + V_{\text{ask}}}, \quad \Delta P_{\text{micro}} = P_{\text{micro}} - \frac{P_{\text{bid}} + P_{\text{ask}}}{2}$$
2. **Forward Return & Information Coefficient Calculation**:
   - Compute forward return $R_{t+k} = \frac{P_{t+k} - P_t}{P_t}$.
   - Calculate Pearson correlation coefficient $IC = \text{Corr}(OFI_t, R_{t+k})$.
3. **Signal Efficacy Audit**:
   - Calculate Signal Hit Ratio (percentage of times positive $OFI$ predicts positive $R_{t+k}$).
4. **Audit Report Generation**: Output structured `MicrostructureSignalReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Confusing Contemporaneous Correlation with Predictive Lead**: Assuming high in-sample correlation between OFI and simultaneous price moves translates to forward predictive lead.
- **Ignoring Micro-Price Division by Zero**: Failing to guard against zero volume in thin order book environments.
- **Overfitting Short Horizon Noise**: Researching horizons shorter than exchange round-trip latency.

## Verification

- Instantiate `OrderBookMicrostructureSignalResearchEngine`. Feed synthetic L2 tick sequence with rising bid volume $\implies$ verify positive $OFI$, positive $VOI$, and positive Micro-Price deviation. Compute forward $IC$ $\implies$ verify $IC > 0.30$.
- Run `python scripts/test_order_book_microstructure_signal_research.py`.

## Related Skills

- `order-book-imbalance-signal-pipeline`
- `order-book-depth-processing-l2-l3`
---
