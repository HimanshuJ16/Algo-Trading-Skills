---
name: position-limit-reporting-cftc-large-trader
description: >-
  CFTC Form 102 Large Trader Reporting (LTR) and speculative position limit compliance engine aggregating account holdings per entity across futures and options contracts.
domain: Regulatory Compliance & Risk Controls
subdomain: CFTC Regulatory Reporting & Speculative Position Limits
tags: ["cftc", "form-102", "large-trader-reporting", "position-limits", "futures", "speculative-limits", "regulatory-reporting"]
brokers_frameworks: ["CFTC Part 17 / Part 150 / Part 20 OCR Rules", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when managing futures, options, and swaps portfolios subject to Commodity Futures Trading Commission (CFTC) regulatory oversight. CFTC Part 17/20 (Ownership and Control Reporting - OCR) mandates daily filing of Form 102A when an entity's aggregated position meets or exceeds contract-specific Large Trader Reporting (LTR) thresholds (e.g., 350 contracts in Crude Oil CL, 1,000 in E-mini S&P ES). Furthermore, CFTC Part 150 enforces strict Federal Speculative Position Limits. This engine aggregates multi-account positions per entity and generates CFTC LTR compliance reports.

## Prerequisites

- Account position records (`account_id`, `entity_name`, `commodity_code`, `net_position`, `long_position`, `short_position`).
- CFTC limit specs (`commodity_code`, `reporting_threshold_contracts`, `federal_speculative_limit`).

## Workflow

1. **Entity-Level Position Aggregation**:
   - Group and sum positions across all sub-accounts owned or controlled by the same legal entity.
2. **Form 102A LTR Threshold Audit**:
   - Compare aggregated position against `reporting_threshold_contracts`.
   - If $|\text{NetPos}| \ge \text{Threshold}$ OR $\max(\text{Long}, \text{Short}) \ge \text{Threshold} \implies$ flag `is_reportable = True` (triggers Form 102A filing).
3. **Federal Speculative Limit Breach Audit**:
   - Compare aggregated position against `federal_speculative_limit`.
   - If $|\text{NetPos}| > \text{Limit} \implies$ flag `is_limit_breached = True` (triggers immediate regulatory violation alert).
4. **Audit Report Generation**: Output structured `CFTCLargeTraderReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Failing to Aggregate Multi-Account Holdings**: Evaluating accounts separately, missing entity-level reportable positions or limit breaches across sister funds.
- **Confusing Reporting Thresholds with Speculative Limits**: Treating the Form 102 reporting trigger (e.g., 350 contracts) as a hard position ceiling.
- **Ignoring Intraday Spikes**: Checking position limits only at session close, ignoring intraday limit breaches.

## Verification

- Instantiate `PositionLimitReportingCFTCLargeTraderEngine`. Aggregate 2 accounts for `ACME_FUND` in Crude Oil `CL` ($200$ contracts + $200$ contracts $= 400$ contracts). Compare against $350$ contract LTR threshold and $10,000$ speculative limit $\implies$ verify `is_reportable = True` (Form 102A required) and `is_limit_breached = False`.
- Run `python scripts/test_position_limit_reporting_cftc_large_trader.py`.

## Related Skills

- `position-limit-breach-simulation-fire-drills`
- `leverage-limit-enforcement-across-instruments`
---
