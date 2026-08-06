---
name: supply-chain-data-for-earnings-prediction
description: >-
  Production-grade Supply Chain Data for Earnings Prediction Engine modeling upstream supplier revenue lead signals, downstream customer inventory accumulation (Bullwhip Effect), customer concentration weights, and consensus EPS surprise Z-score signals.
domain: Alternative Data Research & Alpha Signals
subdomain: Supply Chain Data Intelligence
tags: ["supply-chain", "alternative-data", "earnings-prediction", "lead-lag", "bullwhip-effect", "earnings-surprise"]
brokers_frameworks: ["Alternative Data Pipeline", "Pandas", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when forecasting quarterly corporate earnings (EPS/Revenue) using alternative supply chain data. Upstream supplier revenues lead customer cost of goods sold (COGS) and unit production volumes by 1 to 3 months. Conversely, downstream customer inventory accumulation signals impending order cancellations (the Bullwhip Effect). This engine combines upstream supplier growth and downstream customer inventory trends to construct normalized earnings surprise Z-score signals (`BUY_EARNINGS_SURPRISE`, `SELL_EARNINGS_DISAPPOINTMENT`, `NEUTRAL`) before quarterly earnings releases.

## Prerequisites

- Target company ticker and consensus analyst EPS growth estimates.
- Upstream key suppliers' reported quarterly revenue growth rates.
- Downstream key customers' inventory growth rates and customer concentration weighting.

## Workflow

1. **Lead-Lag Data Alignment**:
   - Align supplier revenue growth ($t-\tau$) with target company earnings period ($t$).
2. **Upstream & Downstream Signal Integration**:
   - Compute implied target revenue growth: $\text{Implied} = (0.70 \times \text{Supplier Growth}) - (0.30 \times \text{Customer Inventory Growth})$.
3. **Consensus Gap & Z-Score Computation**:
   - Calculate gap vs market consensus: $\text{Gap} = \text{Implied Growth} - \text{Consensus Growth}$.
   - Compute Z-score signal value: $Z = \frac{\text{Gap}}{\sigma_{\text{consensus}}}$.
4. **Signal Classification**:
   - $Z \ge 1.0 \implies$ `BUY_EARNINGS_SURPRISE`.
   - $Z \le -1.0 \implies$ `SELL_EARNINGS_DISAPPOINTMENT`.
5. **Execution Output**: Output structured `SignalResult`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring the Bullwhip Effect**: Treating customer inventory accumulation as positive demand rather than a leading indicator of order cancellations.
- **Look-Ahead Bias in Supplier Data**: Utilizing supplier earnings reports before their actual SEC filing public availability date.
- **Unweighted Customer Concentration**: Weighting small component suppliers equally with sole-source critical suppliers.

## Verification

- Instantiate `SupplyChainDataForEarningsPredictionEngine`. Evaluate $25\%$ supplier growth vs $10\%$ consensus $\implies$ verify `directional_signal = BUY_EARNINGS_SURPRISE` and estimated revenue growth $= 16.0\%$. Evaluate $-10\%$ supplier growth and $20\%$ customer inventory buildup $\implies$ verify `SELL_EARNINGS_DISAPPOINTMENT`.
- Run `python scripts/test_supply_chain_data_for_earnings_prediction.py`.

## Related Skills

- `credit-card-transaction-data-signal-construction`
- `alternative-data-vendor-due-diligence-checklist`
---
