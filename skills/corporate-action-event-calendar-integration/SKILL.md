---
name: corporate-action-event-calendar-integration
description: Quantitative corporate action data integration module for tracking declaration,
  ex-date, record date, and payment date lifecycle events, calculating dividend entitlements,
  and auditing vendor feed parity.
domain: Data Management & Global
subdomain: Corporate Action Calendars
tags:
- corporate-actions
- event-calendar
- ex-date
- record-date
- dividends
- splits
- entitlement
brokers_frameworks:
- Generic Market Data
- Python Dataclasses
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when integrating corporate action event calendars (Bloomberg Data License, Refinitiv DataScope, Exchange Direct Feeds) into quantitative trading and portfolio accounting pipelines. Tracking the 4 key lifecycle dates (**Declaration Date**, **Ex-Date**, **Record Date**, **Payment Date**) is critical for position risk management (e.g. reducing position sizing ahead of volatility-inducing events), calculating dividend entitlement receivables, and avoiding trade execution errors on ex-dates.

## When NOT to Use

- **You need adjusted price series, not the event calendar.** Building backadjusted OHLCV across splits/dividends belongs to `corporate-action-adjusted-backtesting`, which consumes the event log this skill ingests.
- **You are reconciling two vendors' already-adjusted price series** (factor methodology differences, rounding conventions). See `vendor-specific-adjustment-methodology-reconciliation`.
- **You owe dividends on a short position.** That is a dividend *payable*; this engine models receivables only and rejects negative share counts.
- **You need exchange holiday-calendar arithmetic.** The forward risk window is measured in calendar days; the module carries no business-day calendar.

## Prerequisites

- Security master mapping (Ticker, ISIN, CUSIP).
- Corporate action feed with date attributes: `declaration_date`, `ex_date`, `record_date`, `payment_date`.

## Workflow

1. **Event Ingestion & Validation**: Ingest event payload (`symbol`, `event_type`, `ex_date`, `record_date`, `payment_date`, `value`). Events are validated on construction: event type must be one of `CASH_DIVIDEND`, `STOCK_SPLIT`, `RIGHTS_OFFERING`, `SPIN_OFF`; dates must satisfy `declaration_date <= ex_date <= record_date <= payment_date` (ex == record is valid under T+1); `value` must be positive and finite. Registration is idempotent on `event_id` — vendor re-broadcasts are dropped, not double-counted.
2. **Upcoming Risk Query**: Query active events for a target date range $[T_{current}, T_{current} + \Delta T]$ (calendar days) to alert trading algorithms of upcoming ex-dates or splits.
3. **Dividend Entitlement Calculation**:
   - On `record_date` close, evaluate portfolio position $N_{shares}$ — i.e. the position held at the close *preceding* the ex-date; buying on or after the ex-date creates no entitlement.
   - Entitlement Receivable $= N_{shares} \times \text{DividendPerShare}$, recognized for the **latest** dividend whose record date has passed.
   - Track receivable status from `EX_DATE` $\to$ `RECORD_DATE` $\to$ `PAYMENT_DATE`.
4. **Multi-Vendor Feed Reconciliation**: Compare vendor event feeds against Golden Source references in **both directions** — a whole event missing from one feed is as serious as a mismatched date. Ex-date, record date, payment date and value mismatches all raise alerts; declaration-date differences are not flagged (dissemination lag, not entitlement risk). If any discrepancy is found **before the ex-date**, escalate to the golden source and hold downstream processing for the affected symbol — do not silently prefer either vendor's dates.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Assuming the Ex-Date strictly precedes the Record Date**: since US standard settlement moved to T+1 (SEC Rule 15c6-1(a), compliance date 2024-05-28), exchanges generally set the ex-date **on** the record date; under the prior T+2 convention it was one business day earlier. Entitlement itself is fixed earlier still: you must hold shares at the close *prior to* the ex-date — buying on the ex-date yields no dividend, and shares sold on the ex-date still entitle the seller.
- **One-Directional Reconciliation**: comparing only Golden-Source-vs-Vendor misses events the golden source lacks entirely. An event present in only one feed is the most common parity failure and must block automated processing until resolved.
- **Double-Counting Re-Broadcast Events**: corporate action feeds re-deliver amended or re-published events. Deduplicate on `event_id` (idempotent registration), not on symbol+date, or one announcement cycle will inflate entitlements and risk queries.
- **Ignoring Currency Conversion on Foreign Dividends**: Failing to convert foreign cash dividends to portfolio base currency using the Payment Date exchange rate.
- **Missing Special Dividends**: Treating regular quarterly dividends and one-off special dividends identically, causing unexpected price drops on ex-date.

## Verification

- Instantiate `CorporateActionEventCalendarEngine`. Register a cash dividend event ($1.50/share, Ex-Date: 2025-05-10, Record Date: 2025-05-11, Payment Date: 2025-05-25). Query upcoming events for 2025-05-08 with a 5-day window; verify event is returned. Calculate entitlement for 10,000 shares held on Record Date; verify $15,000 receivable logged.
- Register the same `event_id` twice; verify it is stored once (registration returns `False` on the duplicate).
- Register a second, later dividend ($1.00/share, Record Date: 2025-08-11) for the same symbol; verify the entitlement computed after 2025-08-11 reflects $1.00/share, not the first event.
- Verify reconciliation flags an event present in only one vendor feed, and rejects events with out-of-order dates or non-positive values at construction.
- Run `python scripts/test_corporate_action_event_calendar_integration.py`.

## Related Skills

- `corporate-action-adjusted-backtesting`
- `isin-cusip-sedol-cross-reference-service`
---
