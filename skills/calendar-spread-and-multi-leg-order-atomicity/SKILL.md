---
name: calendar-spread-and-multi-leg-order-atomicity
description: >-
  Use when executing multi-leg strategies (like calendar spreads) on exchanges that do not support native combo orders. Implements algorithmic atomicity and legging-risk management.
domain: algorithmic-trading
subdomain: execution-algorithms
tags: ["execution", "multi-leg", "atomicity", "legging-risk", "calendar-spread"]
brokers_frameworks: ["Generic Execution"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when executing multi-leg derivatives strategies (e.g., selling near-month, buying far-month options) across exchanges that lack native "combo" or "spread" order types. Executing these legs independently exposes the portfolio to **Legging Risk**—where one leg is filled but the market moves away before the second leg fills, leaving the portfolio with unintended directional Delta risk.

## Prerequisites

- Two or more correlated instruments forming a spread strategy.
- A live connection to a broker API that provides order-status callbacks (Fills, Rejects).
- A predefined maximum allowable "slippage" tolerance for the net spread price.

## Workflow

1. **Spread Definition**: Define the target net spread price and the limit prices for individual legs.
2. **Anchor Leg Execution**: The engine places a limit order for the most illiquid leg first (the "anchor" leg).
3. **Triggered Execution**: Upon a partial or full fill of the anchor leg, the engine immediately fires an IOC (Immediate or Cancel) order for the hedging leg.
4. **Legging Risk Mitigation**: If the hedging leg cannot be filled at the target price, the engine automatically adjusts the limit price up to the maximum slippage tolerance. If still unfilled, it fires a critical alert for manual/algorithmic hedging.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Market Order Hedging**: Using Market Orders to complete the second leg guarantees execution but opens the strategy to catastrophic slippage during flash crashes.
- **Executing the Liquid Leg First**: Filling the highly liquid SPY leg first, then finding out the illiquid corporate bond leg has no bids. Always execute the illiquid leg first.
- **Ignoring Partial Fills**: Failing to proportionately size the second leg based on the *partial* fill quantity of the first leg.

## Verification

- Simulate an illiquid anchor leg filling 50%, followed by the hedging leg filling 50%. Ensure the remaining 50% anchor leg is managed correctly.
- Run `python scripts/test_calendar_spread_and_multi_leg_order_atomicity.py` and confirm 100% pass rate.

## Related Skills

- `execution-algo-behavior-under-halted-instrument`
- `smart-order-routing-across-venues`
