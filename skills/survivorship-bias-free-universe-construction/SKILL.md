---
name: survivorship-bias-free-universe-construction
description: >-
  Use when building backtesting datasets to reconstruct point-in-time constituent universes including delisted and bankrupt instruments, preventing survivorship bias in historical performance evaluation
domain: algorithmic-trading
subdomain: backtesting-methodology
tags: ["backtesting-methodology", "survivorship-bias", "point-in-time", "delisted-stocks", "universe-selection"]
brokers_frameworks: ["CRSP", "Sharadar", "Norgate Data", "QuantConnect Data"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever constructing historical asset universes for quantitative backtesting (such as S&P 500, Russell 2000, NIFTY 50, or crypto indices). Evaluating strategies using only current index constituents ignores companies that suffered severe drawdowns, went bankrupt, or were delisted (e.g., Lehman Brothers, Enron, Wirecard, FTX). This introduces catastrophic survivorship bias, inflating backtested Sharpe ratios by 20% to 50%. Implementing point-in-time constituent membership queries and explicit terminal delisting settlement rules is mandatory.

## Prerequisites

- Historical instrument database with explicit `listing_date`, `delisting_date`, and `delisting_reason`.
- Historical index membership change log.
- Delisting price/recovery value settlement rules (`BANKRUPTCY` $\rightarrow$ $0.0$, `MERGER` $\rightarrow$ acquisition offer price).

## Workflow

1. **Register Point-in-Time Instrument Metadata**:
   - Store all historical constituents with `listing_date`, `delisting_date`, and `delisting_reason`.

2. **Query Point-in-Time Active Universe**:
   - For backtest date $T$, execute `get_active_universe(as_of_date=T)`.
   - Filter instruments satisfying: $\text{listing\_date} \le T \le \text{delisting\_date}$.

3. **Handle Delisting Event Settlement**:
   - When backtest simulation reaches an instrument's `delisting_date`:
     - If `delisting_reason == "BANKRUPTCY"`: Liquidate open position at recovery value (default $0.0$).
     - If `delisting_reason == "MERGER_ACQUISITION"`: Liquidate open position at acquisition price.

4. **Execute Survivorship Bias Audit**:
   - Run `audit_universe_bias(start_date, end_date)`. Verify that delisted instruments constitute a non-zero portion of historical candidate pools.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using Current Index Snapshot**: Querying current S&P 500 constituents and applying them backwards across 10 years of history.
- **Ignoring Bankruptcy Value Loss**: Removing delisted stocks from historical datasets without realizing 100% loss on open long positions.
- **Symbol Re-Use Collisions**: Failing to handle ticker recycling (where ticker symbol `XYZ` is reassigned to a different company after delisting).

## Verification

- Query point-in-time universe for historical date (e.g. 2008-09-15) and verify delisted companies (e.g., Lehman Brothers `LEH`) are included.
- Simulate bankruptcy delisting event and confirm long position is marked to $0.0$ and closed.
- Run unit test suite `python scripts/test_universe_builder.py` and confirm 100% pass rate.

## Related Skills

- `walk-forward-optimization-window-management`
- `corporate-action-adjusted-backtesting`
- `purge-and-embargo-cross-validation`
---
