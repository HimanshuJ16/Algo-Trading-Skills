---
name: options-chain-expiry-cycle-conventions-by-exchange
description: >-
  Options chain expiry cycle conventions engine resolving 3rd Friday monthly dates, AM vs PM settlement rules (SOQ vs closing price), European vs American exercise styles, and Days to Expiration (DTE).
domain: Derivatives Market Structure
subdomain: Global Options Expiry Cycles & Settlement Rules
tags: ["options-conventions", "expiry-cycles", "cboe-spx", "am-settlement", "pm-settlement", "3rd-friday", "dte", "derivatives"]
brokers_frameworks: ["Cboe / CME / Eurex Options Spec", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when building options chain data models, backtesters, or live trading systems across global exchanges (Cboe, CME, Eurex, Deribit). Options contract specifications vary dramatically: standard monthly index options (e.g. Cboe SPX) expire on the 3rd Friday and settle AM using Special Opening Quotes (`SOQ`), whereas Weeklys (SPXW) and equity options settle PM at market close. This engine resolves exact 3rd Friday expiration dates, settlement conventions (`AM_SETTLED` vs `PM_SETTLED`), exercise styles (`EUROPEAN` vs `AMERICAN`), delivery types (`CASH` vs `PHYSICAL`), and Days to Expiration ($DTE$).

## Prerequisites

- Reference date (`ref_date_iso`: 'YYYY-MM-DD').
- Underlying asset symbol (e.g. 'SPX', 'SPXW', 'AAPL') and exchange name ('CBOE', 'CME', 'EUREX', 'DERIBIT').

## Workflow

1. **3rd Friday Monthly Expiry Calculation**:
   - Compute the exact calendar date of the 3rd Friday for target year and month.
2. **Settlement & Exercise Convention Resolution**:
   - Assign settlement type:
     - Standard Index Monthly (e.g. `SPX`) $\implies$ `AM_SETTLED` (SOQ).
     - Weeklys (`SPXW`) & Equity/ETF options $\implies$ `PM_SETTLED` (Closing price).
   - Assign exercise style: Index options $\implies$ `EUROPEAN`; Equity/ETF options $\implies$ `AMERICAN`.
   - Assign delivery type: Index options $\implies$ `CASH`; Equity/ETF options $\implies$ `PHYSICAL`.
3. **DTE Calculation**:
   - Calculate Days to Expiration $DTE = \text{ExpiryDate} - \text{RefDate}$.
4. **Audit Report Generation**: Output structured `OptionsChainConventionReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Confusing AM and PM Settlement**: Trading standard monthly SPX options expecting 4:00 PM ET settlement instead of Friday morning SOQ settlement.
- **Incorrect 3rd Friday Calculation**: Assuming 3rd Friday is always day 21 of the month.
- **Mishandling American vs European Exercise**: Assuming equity options cannot be assigned early prior to expiration.

## Verification

- Instantiate `OptionsChainExpiryConventionsEngine`. Query standard monthly SPX expiration for January 2024 $\implies$ verify 3rd Friday date `2024-01-19`, `AM_SETTLED`, `EUROPEAN` style, `CASH` delivery. Query SPXW weekly option $\implies$ verify `PM_SETTLED`.
- Run `python scripts/test_options_chain_conventions.py`.

## Related Skills

- `options-chain-data-normalization-across-vendors`
- `options-backtesting-with-realistic-iv-surface`
---
