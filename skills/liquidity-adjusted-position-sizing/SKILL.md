---
name: liquidity-adjusted-position-sizing
description: Use when calculating portfolio position sizes to cap allocations by Average
  Daily Volume (ADV) and order book depth, enforcing Days-to-Liquidate (DTL) limits
  and preventing market impact lockup.
domain: algorithmic-trading
subdomain: risk-management
tags:
- risk-management
- position-sizing
- liquidity-adjustment
- adv-cap
- days-to-liquidate
- market-impact
brokers_frameworks:
- Liquidity Position Sizer Engine
- Python NumPy
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when sizing portfolio positions across multi-asset universes containing varying liquidity profiles (large-caps, micro-caps, thinly traded options/futures). Standard fixed-fractional allocation rules ($5\%$ of portfolio NAV) ignore liquidity constraints. Taking a large position in a low-ADV stock makes exiting during stress impossible without suffering severe market impact. This skill caps position sizes relative to Average Daily Volume (ADV) and Days-to-Liquidate (DTL).

## Prerequisites

- Target capital allocation $V_{\text{capital\_target}}$ ($).
- 20-day Average Daily Volume (ADV) in shares and price $S$.
- Max allowed market participation rate $\alpha$ (e.g. $10\%$ of ADV) and max DTL threshold $DTL_{\text{max}}$ (e.g. 1.0 day).

## Workflow

1. **Calculate Maximum Liquidity Cap**:
   $$\text{MaxShares}_{\text{adv}} = \alpha \times \text{ADV}_{20d} \times DTL_{\text{max}}$$

2. **Evaluate Days-to-Liquidate (DTL)**:
   $$DTL = \frac{\text{TargetShares}}{\alpha \times \text{ADV}_{20d}}$$

3. **Apply Liquidity-Adjusted Position Size**:
   $$\text{FinalShares} = \min\left(\frac{V_{\text{capital\_target}}}{S}, \text{MaxShares}_{\text{adv}}\right)$$

4. **Emit Liquidity Scaling Audit**: Record reduction factor if target allocation was scaled down.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Off-Peak Liquidity Drops**: Sizing positions based on 20-day average ADV during a low-volume holiday period.
- **Relying Only on Daily Volume**: Ignoring top-of-book L2 depth when placing large market/ioc orders.

## Verification

- Submit $\$500,000$ target position on stock with $\$100,000$ ADV ($\alpha=10\%$, $DTL_{\text{max}}=1.0$), verify position is capped at $\$10,000$ ($10\%$ ADV).
- Run `python scripts/test_liquidity_position_sizer.py` and confirm 100% pass rate.

## Related Skills

- `position-sizing-and-portfolio-optimization`
- `transaction-cost-analysis-tca-integration`
- `concentration-risk-single-name-limits`
---
