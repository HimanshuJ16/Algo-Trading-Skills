---
name: greeks-based-portfolio-hedging-automation
description: >-
  Use when managing options or multi-asset portfolios to aggregate net portfolio Greeks (Delta, Vega), evaluate risk tolerance breaches, and automatically generate rebalancing hedge orders to maintain delta/vega neutrality.
domain: algorithmic-trading
subdomain: risk-management
tags: ["risk-management", "options-hedging", "portfolio-greeks", "delta-neutral", "vega-hedging", "automated-rebalancing"]
brokers_frameworks: ["Greeks Hedging Engine", "Python NumPy"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when operating options portfolios, market-making books, or delta-neutral strategies. Price movements in underlying assets alter portfolio delta ($\Delta$) and vega ($\nu$) in real time. Unhedged delta exposure leaves the portfolio vulnerable to directional market shocks. This skill aggregates net portfolio Greeks, monitors risk tolerance bands, and automatically calculates required hedge orders (using underlying stocks, futures, or options) to restore risk neutrality.

## Prerequisites

- Individual position Greeks ($\Delta_i, \nu_i$) and quantities.
- Maximum allowed net delta dollar exposure $\Delta_{\text{max\_usd}}$ and net vega limit $\nu_{\text{max}}$.

## Workflow

1. **Aggregate Net Portfolio Greeks**:
   $$\Delta_{\text{net\_usd}} = \sum_{i=1}^N \text{Qty}_i \times \Delta_i \times S_i, \quad \nu_{\text{net}} = \sum_{i=1}^N \text{Qty}_i \times \nu_i$$

2. **Evaluate Hedge Trigger Bands**:
   Check if $|\Delta_{\text{net\_usd}}| > \Delta_{\text{threshold}}$ or $|\nu_{\text{net}}| > \nu_{\text{threshold}}$.

3. **Calculate Automated Hedge Order**:
   $$\text{HedgeShares} = -\left( \frac{\Delta_{\text{net\_usd}}}{S_{\text{hedge}}} \right)$$

4. **Emit Rebalancing Orders & Audit Trail**:
   Generate trade order payload for execution and record audit log.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Over-Hedging High Frequency Friction**: Rebalancing hedge orders on tiny delta drifts, accumulating excessive bid-ask spread cross costs.
- **Ignoring Futures Multiplier**: Calculating index futures hedge shares without multiplying by contract multiplier (e.g. 50x for NIFTY or 50x for E-mini).

## Verification

- Submit options portfolio with $+\$150,000$ net delta breach ($S=\$100$), verify $-\text{1,500}$ share hedge order generation.
- Run `python scripts/test_greeks_hedging_engine.py` and confirm 100% pass rate.

## Related Skills

- `options-greeks-risk-management`
- `options-backtesting-with-realistic-iv-surface`
- `real-time-greeks-recalculation-on-market-moves`
---
