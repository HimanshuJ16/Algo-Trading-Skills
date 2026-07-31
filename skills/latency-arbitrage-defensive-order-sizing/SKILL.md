---
name: latency-arbitrage-defensive-order-sizing
description: >-
  Microstructure risk mitigation engine evaluating cross-venue latency gaps, modeling adverse selection sniping probabilities, dynamically scaling down passive quote sizes, and widening bid-ask spreads.
domain: Market Microstructure & Latency
subdomain: HFT Defense & Passive Liquidity Risk
tags: ["latency-arbitrage", "adverse-selection", "sniping-risk", "defensive-order-sizing", "market-making", "microstructure", "spread-widening"]
brokers_frameworks: ["CME / Nasdaq ITCH Feed", "FIX Order Gateway", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when deploying passive liquidity-providing (market making) strategies on secondary exchanges or fragmented markets prone to **latency arbitrage (sniping)**. High-frequency traders monitor primary signal venues (e.g., CME futures or Nasdaq ITCH) and pick off stale limit quotes resting on slower secondary venues before cancel requests process. This module calculates the adverse selection sniping probability $P_{\text{snipe}}$, dynamically scales down passive order quantity $Q_{\text{defensive}} = Q_0 \times (1 - P_{\text{snipe}})$, and cancels quotes when latency gaps exceed safety thresholds.

## Prerequisites

- Market state payload (`symbol`, `base_quote_qty`, `latency_gap_ms`, `volatility_annualized`, `min_lot_size`).
- Latency budget threshold (e.g. $5.0\text{ ms}$).

## Workflow

1. **Sniping Probability Calculation**:
   - Compute adverse selection probability:
     $$P_{\text{snipe}} = 1.0 - \exp\left(-\lambda \cdot \sigma \cdot \Delta \tau\right)$$
2. **Defensive Quote Sizing & Spread Adjustment**:
   - Compute $Q_{\text{defensive}} = Q_0 \times (1.0 - P_{\text{snipe}})$.
   - If $Q_{\text{defensive}} < Q_{\text{min\_lot}} \implies$ Set $Q_{\text{defensive}} = 0$ (Cancel quote).
   - Compute spread multiplier: $W_{\text{spread}} = 1.0 + (P_{\text{snipe}} \times 2.0)$.
3. **Sniping Risk Audit**:
   - If $P_{\text{snipe}} \ge 0.50 \implies$ Flag `HIGH_SNIPING_RISK_CANCEL`.
4. **Audit Report Generation**: Output structured `DefensiveSizingReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Posting Static Large Orders During Latency Spikes**: Maintaining $10,000$ share passive quotes when cross-venue latency increases to $20\text{ ms}$, resulting in instant toxic fills.
- **Ignoring Volatility Expansion**: Sizing quotes based on static latency without incorporating volatility $\sigma$, underestimating sniping risk during fast markets.
- **Failing to Cancel Below Min Lot Size**: Leaving 1-share residual quotes resting on book, incurring transaction fee overhead.

## Verification

- Instantiate `LatencyArbitrageDefensiveSizingEngine`. Audit Low Latency Normal Market ($\Delta \tau = 1\text{ ms}$, $\sigma = 0.20$, $Q_0 = 1000$) $\implies$ verify low $P_{\text{snipe}} \approx 0.095$, $Q_{\text{defensive}} \approx 905$ shares, and approves `QUOTE_DEFENSIVELY_SIZED`. Audit High Latency Volatile Market ($\Delta \tau = 25\text{ ms}$, $\sigma = 0.80$) $\implies$ verify $P_{\text{snipe}} > 0.99$, sets $Q_{\text{defensive}} = 0$, and triggers `HIGH_SNIPING_RISK_CANCEL`.
- Run `python scripts/test_latency_arbitrage_defensive_order_sizing.py`.

## Related Skills

- `adverse-selection-measurement-for-passive-orders`
- `cross-venue-latency-arbitrage-defensive-design`
---
