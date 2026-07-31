---
name: instrument-universe-change-detection-and-alerting
description: >-
  Reference data management engine for detecting tradable universe changes (additions, de-listings, ticker renames, halts) across daily snapshots using permanent identifiers (FIGI / ISIN).
domain: Data Management Global
subdomain: Reference Data Universe Tracking & Alerting
tags: ["universe-detection", "security-master", "openfigi", "isin", "ticker-renames", "delistings", "reference-data"]
brokers_frameworks: ["OpenFIGI API", "Security Master Database", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in reference data management, index constituent tracking, and trading bot universe maintenance. Trading algorithms relying solely on ephemeral ticker symbols experience severe failures during ticker renames (e.g. `FB` $\to$ `META`), corporate delistings, or index additions. By indexing instruments by immutable permanent identifiers (**FIGI** / **ISIN**), this engine cross-matches daily universe snapshots ($U_{t-1}$ vs $U_t$) to detect Additions, Deletions, Ticker Renames, and Trading Status Halts, emitting actionable real-time alerts (`LIQUIDATE_POSITION_AND_UNSUBSCRIBE`, `UPDATE_SYMBOL_MAPPER`).

## Prerequisites

- Previous universe snapshot ($U_{t-1}$) and Current universe snapshot ($U_t$).
- Immutable permanent identifier mapping (`figi` or `isin`).

## Workflow

1. **Snapshot Ingestion & Permanent ID Keying**:
   - Key instrument records by immutable `permanent_id` (FIGI / ISIN).
2. **Set Difference & Cross-Matching Delta Analysis**:
   - **Additions**: Permanent ID in $U_t$ but not $U_{t-1} \implies$ Action `INITIATE_COVERAGE`.
   - **Deletions / De-listings**: Permanent ID in $U_{t-1}$ but not $U_t \implies$ Action `LIQUIDATE_POSITION_AND_UNSUBSCRIBE`.
   - **Ticker Renames**: Permanent ID in both, but ticker changed (e.g. `FB` $\to$ `META`) $\implies$ Action `UPDATE_SYMBOL_MAPPER`.
   - **Status Halts**: Permanent ID in both, but status changed (`ACTIVE` $\to$ `HALTED`) $\implies$ Action `FREEZE_TRADING_ALERTS`.
3. **Alert Notification Generation**: Output structured `UniverseChangeReport` and alerts.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Keying Universe Diffs on Ticker Symbols**: Using ticker symbols instead of immutable FIGI/ISIN keys, treating a ticker rename (`FB` $\to$ `META`) as a deletion of `FB` and addition of `META`, losing historical position joins.
- **Failing to Alert on De-listings**: Missing corporate de-listings and leaving orphan open limit orders on halted or de-listed instruments.
- **Unsynchronized Database Joins**: Updating ticker mappers without notifying live execution daemons, causing trade routing failures.

## Verification

- Instantiate `UniverseChangeDetectionEngine`. Compare $U_{t-1}$ (`BBG000MM82B1`: `FB`, `BBG000B9XRY4`: `AAPL`) with $U_t$ (`BBG000MM82B1`: `META`, `BBG000B9XRY4`: `AAPL`, `BBG001S5N8V8`: `PLTR`) $\implies$ verify engine detects 1 Addition (`PLTR`), 0 Deletions, and 1 Ticker Rename (`FB` $\to$ `META` with action `UPDATE_SYMBOL_MAPPER`).
- Run `python scripts/test_universe_change_detection.py`.

## Related Skills

- `reference-data-symbol-mapping-across-vendors`
- `isin-cusip-sedol-cross-reference-service`
---
