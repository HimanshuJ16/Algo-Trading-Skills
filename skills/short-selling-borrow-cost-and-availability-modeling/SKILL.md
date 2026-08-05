---
name: short-selling-borrow-cost-and-availability-modeling
description: >-
  Production-grade short-selling borrow fee rate modeler, locate availability validator, and hard-to-borrow (HTB) cost estimator calculating daily interest drag and short squeeze availability risk.
domain: Market Microstructure & Portfolio Risk
subdomain: Securities Lending & Short Sale Borrow Cost
tags: ["short-selling", "borrow-cost", "hard-to-borrow", "htb-rate", "locate-availability", "securities-lending"]
brokers_frameworks: ["Securities Lending Borrow Rates", "Interactive Brokers Stock Loan API", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when developing, backtesting, or executing quantitative short-selling or market-neutral equity strategies. Short selling requires borrowing shares from a broker or lending desk. While liquid General Collateral (GC) equities incur negligible borrow rates ($\approx 0.25\% - 0.50\%$), Hard-To-Borrow (HTB) securities with high short interest can incur annualized borrow rates exceeding $20\% - 500\%$. This engine validates share locate availability before order routing and calculates daily borrow cost drag.

## Prerequisites

- Securities borrow status (`BorrowStatus`: `ticker`, `utilization_rate`: 0.0 to 1.0, `available_shares`).
- Short trade specification (`ShortTrade`: `ticker`, `shares`, `entry_price`, `days_held`).

## Workflow

1. **Locate Availability Verification**:
   - Check if requested shares $\le \text{available\_shares}$ and utilization $< 100\%$. If unavailable, reject short order.
2. **Dynamic Borrow Rate Calculation**:
   - For General Collateral ($\text{utilization} \le 80\%$), apply baseline GC rate (e.g. 0.25%).
   - For Hard-To-Borrow ($\text{utilization} > 80\%$), scale rate linearly from base HTB rate to max HTB rate ($5\% \to 50\%$).
3. **Daily Borrow Drag Calculation**:
   - Calculate total dollar borrow fee: $\text{Cost} = \text{Notional} \times \frac{\text{Rate}}{365} \times \text{Days Held}$.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Borrow Costs in Backtests**: Backtesting short strategies assuming zero borrow fee drag, overestimating strategy returns on HTB small-cap stocks.
- **Unchecked Short Share Locates**: Routing short sell orders without verifying available share inventory, leading to broker order rejections.
- **Failing to Account for Recall Risk**: Assuming short positions can be held indefinitely without modeling sudden lender recalls during short squeezes.

## Verification

- Instantiate `BorrowCostModeler`. Check short availability for GC stock ("AAPL") $\implies$ verify `can_short = True`. Check availability for fully utilized HTB stock ("MEME") $\implies$ verify `can_short = False`. Calculate annualized borrow rate for HTB stock ("GME" at 90% utilization) $\implies$ verify scaled rate $17.5\%$. Calculate 30-day borrow cost $\implies$ verify exact dollar drag.
- Run `python scripts/test_borrow_cost_modeler.py`.

## Related Skills

- `sec-rule-15c3-5-risk-controls-us`
- `portfolio-construction-with-transaction-cost-awareness`
---
