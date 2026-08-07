---
name: cross-validation-of-commission-schedules-over-time
description: Use when backtesting multi-year historical strategies to model historical
  changes in broker commission schedules over time rather than applying modern zero-commission
  or current fee structures retroactively.
domain: algorithmic-trading
subdomain: backtesting-methodology
tags:
- backtesting-methodology
- commission-schedule
- historical-fees
- transaction-costs
- broker-rates
brokers_frameworks:
- Historical Commission Modeler
- Python
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when running historical backtests spanning multiple years (e.g. 2010–2024). Applying today's zero-commission retail equity structure or current low HFT maker rebates retroactively to 2012 (when equity trades cost $6.95–$9.95 per trade) creates unrealistically high backtested P&L for high-frequency or multi-trade strategies. This skill models date-effective fee schedules and audits commission impact over time.

## Prerequisites

- Trade execution log with timestamps.
- Historical commission schedule table ($T_{\text{effective}}$, fixed fee, per-share fee, minimum ticket fee, percentage fee).

## Workflow

1. **Construct Time-Varying Fee Schedule**: Define commission schedule rules indexed by effective date ranges.
2. **Lookup Date-Specific Fee Rate**: For each trade, resolve the applicable broker commission rate based on trade timestamp $T_{\text{trade}}$.
3. **Compute Trade Commission**: Calculate exact fee considering per-share rates, ticket minimums, and volume tiering.
4. **Audit Fee Schedule Impact**: Compare backtest return using historical fee schedules vs fixed modern fee schedules.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Retroactive Zero-Commission**: Assuming 0 commission back to 2015 when US retail zero-commission only started around late 2019.
- **Ignoring Minimum Ticket Charges**: Applying a 0.005/share fee without enforcing $1.00 minimum ticket charges on small 10-share trades.

## Verification

- Submit historical trades spanning 2018 ($4.95 ticket + $0.005/sh) and 2020 ($0 commission), verifying date-specific fee calculation.
- Run `python scripts/test_commission_schedule_modeler.py` and confirm 100% pass rate.

## Related Skills

- `transaction-cost-analysis-tca-integration`
- `post-only-and-maker-taker-fee-optimization`
- `multi-year-regime-coverage-requirement`
---
