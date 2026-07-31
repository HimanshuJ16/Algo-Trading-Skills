---
name: market-maker-vs-taker-strategy-classification
description: >-
  Quantitative strategy classification engine categorizing trading algorithms as Pure Maker, Pure Taker, or Hybrid based on execution volume ratios, fee schedule attribution, and rebate capture.
domain: Market Microstructure Latency
subdomain: Strategy Classification & Fee Optimization
tags: ["market-microstructure", "maker-vs-taker", "strategy-classification", "exchange-fees", "maker-rebates", "effective-fee-bps", "post-only"]
brokers_frameworks: ["CME Fee Schedules", "Binance VIP Tiers", "Kraken Fee Tiers", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when auditing trading algorithm performance, optimizing exchange fee tier placements, and evaluating market microstructure impact. Exchanges charge significantly lower fees or pay liquidity rebates for **Maker Orders** (passive limit orders resting on the book) compared to **Taker Orders** (aggressive market/IOC orders removing liquidity). This module audits executed trade logs, calculates the Maker Volume Ratio ($R_{\text{maker}}$) and effective fee rate in basis points ($\text{Fee}_{\text{effective\_bps}}$), and classifies algorithms into `PURE_MAKER_STRATEGY`, `PURE_TAKER_STRATEGY`, or `HYBRID_MAKER_TAKER_STRATEGY`.

## Prerequisites

- Executed trade log payload (`trade_id`, `symbol`, `is_maker`: boolean, `executed_price`, `quantity`, `fee_paid_usd`).
- Exchange fee schedule specification (`maker_fee_rate`: e.g. $-0.0001$ rebate, `taker_fee_rate`: e.g. $+0.0005$ fee).

## Workflow

1. **Trade Volume Decomposition**:
   - Calculate total volume ($V_{\text{total}}$), passive maker volume ($V_{\text{maker}}$), and aggressive taker volume ($V_{\text{taker}}$).
2. **Maker Ratio Calculation**:
   - Compute Maker Volume Ratio:
     $$R_{\text{maker}} = \frac{V_{\text{maker}}}{V_{\text{total}}}$$
3. **Strategy Classification**:
   - If $R_{\text{maker}} \ge 0.80 \implies$ Classify as `PURE_MAKER_STRATEGY`.
   - If $R_{\text{maker}} \le 0.20 \implies$ Classify as `PURE_TAKER_STRATEGY`.
   - If $0.20 < R_{\text{maker}} < 0.80 \implies$ Classify as `HYBRID_MAKER_TAKER_STRATEGY`.
4. **Effective Fee Rate & Rebate Audit**:
   - Compute net fees paid ($F_{\text{net}}$) and gross notional ($N_{\text{total}}$).
   - Calculate effective fee rate in bps: $\text{Fee}_{\text{effective\_bps}} = \frac{F_{\text{net}}}{N_{\text{total}}} \times 10,000$.
5. **Audit Report Generation**: Output structured `StrategyClassificationReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Post-Only Orders in Maker Algos**: Submitting limit orders without "Post-Only" flags, causing accidental aggressive Taker fills when prices cross.
- **Miscalculating Net Fees Under Negative Rebates**: Failing to account for negative fee rates (rebates) earned by high-volume Maker algorithms.
- **Unaware Taker Fee Drag on High-Frequency Algos**: Running high-frequency momentum strategies with high Taker ratios ($R_{\text{maker}} < 0.10$), eroding alpha through exchange taker fees.

## Verification

- Instantiate `MarketMakerVsTakerClassifierEngine`. Audit 100 executed trades (90 Maker trades, 10 Taker trades, total notional $\$1,000,000$, net fees $-\$100.00$ rebate) $\implies$ verify $R_{\text{maker}} = 0.90$, classifies as `PURE_MAKER_STRATEGY`, calculates negative effective fee rate ($-1.0\text{ bps}$ rebate), and approves `STRATEGY_CLASSIFICATION_SUCCESS`.
- Run `python scripts/test_market_maker_vs_taker_strategy_classification.py`.

## Related Skills

- `exchange-fee-tier-and-rebate-structure-analysis`
- `adverse-selection-measurement-for-passive-orders`
---
