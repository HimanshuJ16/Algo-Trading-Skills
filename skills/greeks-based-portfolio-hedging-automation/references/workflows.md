# Deep Workflow Reference — greeks-based-portfolio-hedging-automation

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Aggregate Net Portfolio Greeks**:
   $$\Delta_{\text{net\_usd}} = \sum \text{Qty}_i \cdot \Delta_i \cdot S_i, \quad \nu_{\text{net}} = \sum \text{Qty}_i \cdot \nu_i$$
2. **Evaluate Hedge Trigger Bands**: Check if $|\Delta_{\text{net\_usd}}| > \Delta_{\text{max\_usd}}$.
3. **Calculate Automated Hedge Order**: $\text{HedgeShares} = -(\Delta_{\text{net\_usd}} / S_{\text{hedge}})$.
4. **Emit Rebalancing Orders & Audit Trail**: Submit hedge orders to execution system.

## Production Implementation Reference

- Reference code: `scripts/greeks_hedging_engine.py` (`GreeksPortfolioHedgingEngine`, `NetGreeksSummary`, `HedgeOrder`).
- Automated unit tests: `scripts/test_greeks_hedging_engine.py`.
