---
name: global-macro-economic-calendar-integration
description: >-
  Quantitative macro engine for integrating global economic calendars (FOMC, CPI, NFP), calculating surprise metrics, and enforcing automated pre/post-event trading blackout windows.
domain: Macro Research & Risk Management
subdomain: Macro Economic Events & News Risk Safeguards
tags: ["macro-calendar", "economic-events", "fomc", "cpi", "nfp", "trading-blackout", "surprise-index", "news-filter"]
brokers_frameworks: ["Trading Economics API", "FRED API", "Bloomberg Data", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in quantitative macro strategies, systematic risk management platforms, and algorithmic execution engines. High-impact macroeconomic releases (FOMC Rate Decisions, Non-Farm Payrolls NFP, US CPI inflation data) trigger extreme price volatility, order book depth collapse, and severe execution slippage. This module ingests global economic calendar feeds, computes macro surprise metrics, and enforces automated pre-event and post-event trading blackout windows (e.g. 15 mins before to 15 mins after FOMC release).

## Prerequisites

- Macro economic calendar event schedule (`event_id`, `name`, `release_time_iso`, `currency`, `impact_severity`, `consensus_forecast`, `actual_release`).
- Configured blackout buffer windows ($\Delta t_{\text{pre}} = 15\text{ mins}$, $\Delta t_{\text{post}} = 15\text{ mins}$).

## Workflow

1. **Macro Calendar Event Ingestion**:
   - Ingest scheduled releases (e.g. `FOMC_RATE_DECISION`, `US_CPI_YOY`, `NFP_JOBS`).
2. **Surprise Deviation Index Calculation**:
   - Compute macro surprise metric when actual data is released:
     $$S = \frac{\text{Actual Value} - \text{Consensus Forecast}}{\text{Forecast StdDev}}$$
3. **Blackout Window & Safeguard Enforcement**:
   - Audit current time $T_{\text{current}}$ relative to scheduled event release $T_{\text{release}}$.
   - If $T_{\text{release}} - \Delta t_{\text{pre}} \le T_{\text{current}} \le T_{\text{release}} + \Delta t_{\text{post}}$:
     - Flag `MACRO_BLACKOUT_ACTIVE`.
     - Block new order signals (`is_trading_permitted = False`).
     - Issue `MASS_CANCEL_LIMIT_ORDERS` signal.
4. **Audit Report Generation**: Output structured `MacroCalendarAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Trading Through High-Impact Releases Without Blackouts**: Leaving market or limit orders open during FOMC releases, getting filled at extreme slippage prices.
- **Neglecting Timezone Conversions**: Failing to convert UTC calendar timestamps to local exchange execution time, triggering blackouts at wrong hours.
- **Ignoring Low-Visibility Macro Releases**: Monitoring only FOMC while ignoring surprise CPI or labor data prints.

## Verification

- Instantiate `GlobalMacroCalendarEngine`. Register FOMC release at 14:00 UTC. Test current time 13:50 UTC (10 mins prior) $\implies$ verify engine flags `MACRO_BLACKOUT_ACTIVE`, blocks new trades, and triggers limit order mass-cancel. Test current time 15:00 UTC (60 mins after) $\implies$ verify engine flags `MACRO_TRADING_PERMITTED` and calculates macro surprise score ($S = +2.50$).
- Run `python scripts/test_macro_calendar_integration.py`.

## Related Skills

- `central-bank-communication-nlp-analysis`
- `global-exchange-holiday-calendar-handling`
---
