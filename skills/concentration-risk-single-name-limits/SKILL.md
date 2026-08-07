---
name: concentration-risk-single-name-limits
description: Quantitative pre-trade risk engine for enforcing single-name NAV exposure
  caps, liquidity ADV constraints, order downsizing, and calculating portfolio Herfindahl-Hirschman
  Index (HHI).
domain: Risk Management
subdomain: Portfolio Risk & Limits
tags:
- concentration-risk
- single-name-limit
- hhi
- adv-limit
- pre-trade-risk
- position-sizing
brokers_frameworks:
- NumPy
- Generic Risk Engine
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill to enforce pre-trade concentration risk limits across equity, futures, or crypto portfolios. Concentrating too much capital in a single security or issuer creates extreme idiosyncratic risk (e.g. unexpected earnings crash, regulatory action) and market impact risk during liquidation. This module validates proposed orders against maximum % NAV limits and % ADV (Average Daily Volume) constraints, automatically downsizing or rejecting non-compliant orders.

## Prerequisites

- Portfolio Net Asset Value (NAV) and current position market values.
- 20-day Average Daily Volume (ADV) and market price for each security.

## Workflow

1. **Pre-Trade Evaluation**: Submit proposed order (`symbol`, `side`, `quantity`, `price`) to `SingleNameConcentrationLimiter`.
2. **NAV Concentration Check**:
   - $\text{New Position Value} = \text{Current Value} + (\text{Quantity} \times \text{Price})$.
   - $\text{NAV Weight} = \frac{\text{New Position Value}}{\text{NAV}}$.
   - Check if $\text{NAV Weight} \le \text{Max NAV Limit}$ (e.g. 5%).
3. **ADV Liquidity Check**:
   - Check if $\text{Quantity} \le \text{Max ADV Limit} \times \text{ADV}$.
4. **Order Downsizing**: If an order exceeds limits, automatically downsize the quantity to the maximum allowable share count (or trigger hard rejection if requested).
5. **Portfolio HHI Calculation**: Compute Herfindahl-Hirschman Index ($HHI = \sum w_i^2$) and Effective Assets ($N_{eff} = 1 / HHI$).

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Offsetting Futures/Derivatives**: Calculating single-name equity concentration without factoring in single-stock futures or options delta.
- **Static Share Limits in Volatile Markets**: Hardcoding maximum share counts instead of dynamic % ADV limits. As market volume fluctuates, static share limits can cause severe market impact.
- **Evaluating Post-Trade Only**: Checking concentration after order execution when the position is already over-allocated. Concentration limits MUST be enforced pre-trade.

## Verification

- Instantiate `SingleNameConcentrationLimiter` with a 5% NAV limit and 10% ADV limit. Submit an order for AAPL that would push NAV weight to 8% and consume 15% ADV. Verify that the limiter downsizes the order to satisfy both the 5% NAV and 10% ADV bounds. Calculate portfolio HHI across 10 equal-weighted positions and verify $HHI = 0.10$ ($N_{eff} = 10.0$).
- Run `python scripts/test_single_name_concentration_limiter.py`.

## Related Skills

- `portfolio-level-stop-loss-independent-of-strategy-stops`
- `strategy-capacity-estimation-before-scaling-capital`
