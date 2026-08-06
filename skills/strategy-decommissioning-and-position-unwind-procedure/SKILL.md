---
name: strategy-decommissioning-and-position-unwind-procedure
description: >-
  Production-grade Strategy Decommissioning & Position Unwind Engine managing entry signal hard blocks, orderly VWAP/TWAP position liquidation slicing, market impact minimization, and treasury capital return workflows.
domain: Investment Governance & Capital Allocation
subdomain: Strategy Lifecycle Decommissioning & Position Unwind
tags: ["strategy-decommissioning", "position-unwind", "liquidation-slicing", "market-impact-minimization", "order-entry-block", "treasury-return"]
brokers_frameworks: ["Multi-Strategy Liquidation Framework", "VWAP/TWAP Execution Slicing", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when retiring a quantitative trading strategy due to alpha decay, persistent underperformance, risk breaches, or strategy committee decisions. Abruptly dumping all portfolio positions via market orders causes severe market impact slippage and implementation shortfall. This engine transitions strategy state through an orderly lifecycle (`ORDER_ENTRY_BLOCKED` $\to$ `UNWIND_IN_PROGRESS` $\to$ `FULLY_UNWOUND`), hard-blocks new entry signals, slices positions into ADV-constrained liquidation orders ($\le 10\%$ ADV per slice), and tracks net realized PnL before returning capital to fund treasury.

## Prerequisites

- Strategy position inventory (`StrategyPosition`: `symbol`, `quantity`, `market_price`, `avg_daily_volume`, `max_adv_slice_pct`).
- Strategy identifier (`strategy_id`).

## Workflow

1. **Hard Block Entry Signals**:
   - Initiate decommissioning; set state to `ORDER_ENTRY_BLOCKED` (`new_entries_allowed = False`).
2. **Orderly Liquidation Slicing**:
   - Calculate position liquidation slices constrained by ADV participation limit ($\le 10\%$ ADV).
   - Long positions $\implies$ SELL slices; Short positions $\implies$ BUY slices.
3. **Fill Execution & Inventory Tracking**:
   - Record slice execution fills, realized PnL, and remaining position quantities (`record_slice_execution()`).
4. **Treasury Capital Return**:
   - When all positions reach 0, transition state to `FULLY_UNWOUND` and initiate treasury capital return.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Allowing New Entry Signals During Unwind**: Failing to hard-block strategy entry signals, causing new positions to open while the unwind engine is trying to liquidate.
- **Unconstrained Liquidation Slices**: Sending 100% of position size in a single market order, incurring severe market impact slippage.
- **Unreconciled Residual Positions**: Marking a strategy as decommissioned while small residual position fractions remain open.

## Verification

- Instantiate `StrategyDecommissioningEngine`. Load long AAPL ($1000$ shares, $5000$ ADV) and short MSFT ($-500$ shares, $2000$ ADV). Initiate decommissioning $\implies$ verify `new_entries_allowed=False`. Generate liquidation slices $\implies$ verify AAPL SELL slice of $500$ shares ($10\%$ ADV) and MSFT BUY slice of $200$ shares ($10\%$ ADV). Record full executions $\implies$ verify state transitions to `FULLY_UNWOUND`.
- Run `python scripts/test_strategy_decommissioning_and_position_unwind_procedure.py`.

## Related Skills

- `strategy-lifecycle-retirement-criteria`
- `strategy-committee-governance-for-capital-allocation-decisions`
---
