---
name: corporate-action-event-calendar-integration
description: >-
  Quantitative corporate action data integration module for tracking declaration, ex-date, record date, and payment date lifecycle events, calculating dividend entitlements, and auditing vendor feed parity.
domain: Data Management & Global
subdomain: Corporate Action Calendars
tags: ["corporate-actions", "event-calendar", "ex-date", "record-date", "dividends", "splits", "entitlement"]
brokers_frameworks: ["Generic Market Data", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when integrating corporate action event calendars (Bloomberg Data License, Refinitiv DataScope, Exchange Direct Feeds) into quantitative trading and portfolio accounting pipelines. Tracking the 4 key lifecycle dates (**Declaration Date**, **Ex-Date**, **Record Date**, **Payment Date**) is critical for position risk management (e.g. reducing position sizing ahead of volatility-inducing events), calculating dividend entitlement receivables, and avoiding trade execution errors on ex-dates.

## Prerequisites

- Security master mapping (Ticker, ISIN, CUSIP).
- Corporate action feed with date attributes: `declaration_date`, `ex_date`, `record_date`, `payment_date`.

## Workflow

1. **Event Ingestion & Validation**: Ingest event payload (`symbol`, `event_type`, `ex_date`, `record_date`, `payment_date`, `value`).
2. **Upcoming Risk Query**: Query active events for a target date range $[T_{current}, T_{current} + \Delta T]$ to alert trading algorithms of upcoming ex-dates or splits.
3. **Dividend Entitlement Calculation**:
   - On `record_date` close, evaluate portfolio position $N_{shares}$.
   - Entitlement Receivable $= N_{shares} \times \text{DividendPerShare}$.
   - Track receivable status from `EX_DATE` $\to$ `RECORD_DATE` $\to$ `PAYMENT_DATE`.
4. **Multi-Vendor Feed Reconciliation**: Compare vendor event feeds against Golden Source references to detect mismatched ex-dates or values.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Confusing Ex-Date with Record Date**: Entitlement is determined by holding shares at the close of business prior to the Ex-Date, but record-date settlement (T+1 / T+2) must align with the shareholder ledger.
- **Ignoring Currency Conversion on Foreign Dividends**: Failing to convert foreign cash dividends to portfolio base currency using the Payment Date exchange rate.
- **Missing Special Dividends**: Treating regular quarterly dividends and one-off special dividends identically, causing unexpected price drops on ex-date.

## Verification

- Instantiate `CorporateActionEventCalendarEngine`. Register a cash dividend event ($1.50/share, Ex-Date: 2025-05-10, Record Date: 2025-05-11, Payment Date: 2025-05-25). Query upcoming events for 2025-05-08 with a 5-day window; verify event is returned. Calculate entitlement for 10,000 shares held on Record Date; verify $15,000 receivable logged.
- Run `python scripts/test_corporate_action_event_calendar_integration.py`.

## Related Skills

- `corporate-action-adjusted-backtesting`
- `isin-cusip-sedol-cross-reference-service`
---
