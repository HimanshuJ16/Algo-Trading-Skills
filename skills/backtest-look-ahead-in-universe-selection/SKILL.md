---
name: backtest-look-ahead-in-universe-selection
description: Use when auditing strategy universe selection logic to detect and prevent
  lookahead bias (e.g. selecting top 50 S&P 500 stocks by today's market cap retroactively
  for 2015 backtests).
domain: algorithmic-trading
subdomain: backtesting-methodology
tags:
- backtesting-methodology
- universe-selection
- lookahead-bias
- point-in-time
- survivorship-bias
brokers_frameworks:
- Universe Lookahead Auditor
- Python
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when building or validating strategy universe selection rules. Selecting assets for a historical backtest using present-day attributes (e.g., "top 50 stocks by market cap today" or "current S&P 500 constituents" applied to 2015) introduces severe lookahead and survivorship bias. This skill audits universe membership point-in-time timestamps to guarantee zero future data leakage.

## Prerequisites

- Point-in-time historical universe constituent records with publication dates.
- Universe selection filter logic.

## Workflow

1. **Extract Constituent Timestamps**: Record the exact timestamp when symbol membership became known.
2. **Audit Filter Execution**: Assert that selection criteria (market cap, volume) use data strictly preceding backtest date $T$.
3. **Flag Lookahead Violations**: Alert if any constituent added to historical universe relied on post-date data.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Retroactive Index Composition**: Using 2024 S&P 500 constituents for a 2015 backtest, excluding companies that went bankrupt.
- **Using Un-adjusted Historical Caps**: Ranking universe by unadjusted market caps without point-in-time share counts.

## Verification

- Submit universe selection with future-dated market caps, verify audit violation flag.
- Run `python scripts/test_universe_lookahead_auditor.py` and confirm 100% pass rate.

## Related Skills

- `backtest-database-schema-for-point-in-time-queries`
- `survivorship-bias-free-universe-construction`
---
