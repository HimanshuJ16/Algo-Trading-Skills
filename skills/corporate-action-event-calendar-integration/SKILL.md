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
version: "1.2.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when integrating corporate action event calendars (Bloomberg Data License, Refinitiv DataScope, Exchange Direct Feeds) into quantitative trading and portfolio accounting pipelines. Tracking the 4 key lifecycle dates (**Declaration Date**, **Ex-Date**, **Record Date**, **Payment Date**) is critical for position risk management (e.g. reducing position sizing ahead of volatility-inducing events), calculating dividend entitlement receivables, and avoiding trade execution errors on ex-dates.

## When NOT to Use

- **You need adjusted price series, not the event calendar.** Building backadjusted OHLCV across splits/dividends belongs to `corporate-action-adjusted-backtesting`, which consumes the event log this skill ingests.
- **You are reconciling two vendors' already-adjusted price series** (factor methodology differences, rounding conventions). See `vendor-specific-adjustment-methodology-reconciliation`.
- **You owe dividends on a short position.** That is a dividend *payable*; this engine models receivables only and rejects negative share counts.
- **You need exchange holiday-calendar arithmetic.** The forward risk window is measured in calendar days; the module carries no business-day calendar, so it cannot itself derive an ex-date from a record or payable date — it validates the dates the feed supplies.
- **You need a multi-currency receivable ledger.** `calculate_dividend_entitlement` returns a single dividend, rounds to 2 decimal places (assuming a 2-minor-unit currency), and carries no currency tag or FX conversion.

## Prerequisites

- Security master mapping (Ticker, ISIN, CUSIP).
- Corporate action feed with date attributes: `declaration_date`, `ex_date`, `record_date`, `payment_date`.

## Workflow

1. **Event Ingestion & Validation**: Ingest event payload (`symbol`, `event_type`, `ex_date`, `record_date`, `payment_date`, `value`, `ex_date_convention`). Events are validated on construction: event type must be one of `CASH_DIVIDEND`, `STOCK_SPLIT`, `RIGHTS_OFFERING`, `SPIN_OFF`; `value` must be positive and finite; and the date ordering is checked **against the declared ex-date convention** (step 2). Registration is idempotent on `event_id`, but a re-broadcast that differs materially from the stored event is treated as an **amendment**, not a duplicate: it is rejected *and* logged at ERROR with the differing fields, because silently overwriting an ex-date that downstream sizing has already acted on is as dangerous as silently discarding the change. Resolve amendments against the golden source.
2. **Declare the Ex-Date Convention**: Which side of the lifecycle the ex-date falls on depends on the distribution's size relative to the security price — something this module cannot observe, so the feed must declare it:
   - `PRE_RECORD` (default) — distributions worth **less than 25%** of the security. Ordering: `declaration <= ex <= record <= payment`. Under US T+1 the ex-date is the record date itself.
   - `POST_PAYABLE` — distributions of **25% or more** (most forward splits, large special dividends, many spin-offs and rights issues). FINRA Rule 11140(b)(2) puts the ex-date on the *first business day following the payable date*. Ordering: `declaration <= record <= payment <= ex`.
   Never infer the convention from `event_type` or `value`: a 5% stock dividend is `PRE_RECORD` while a 30% special *cash* dividend is `POST_PAYABLE`.
3. **Upcoming Risk Query**: Query active events for a target date range $[T_{current}, T_{current} + \Delta T]$ (calendar days) to alert trading algorithms of upcoming ex-dates or splits.
4. **Dividend Entitlement Calculation**:
   - On `record_date` close, evaluate portfolio position $N_{shares}$ — i.e. the position held at the close *preceding* the ex-date; buying on or after the ex-date creates no entitlement.
   - Entitlement Receivable $= N_{shares} \times \text{DividendPerShare}$, recognized for the **latest** dividend whose record date has passed.
   - Track receivable status from `EX_DATE` $\to$ `RECORD_DATE` $\to$ `PAYMENT_DATE`.
   - Only the single most recent dividend is returned. Where a special dividend shares a record date with the regular one, the engine logs a warning and returns the lower `event_id` deterministically — accrue the other leg yourself.
5. **Multi-Vendor Feed Reconciliation**: Compare vendor event feeds against Golden Source references in **both directions** — a whole event missing from one feed is as serious as a mismatched date. Symbol, event type, ex-date convention, ex-date, record date, payment date and value mismatches all raise alerts; declaration-date differences are not flagged (dissemination lag, not entitlement risk). If any discrepancy is found **before the ex-date**, escalate to the golden source and hold downstream processing for the affected symbol — do not silently prefer either vendor's dates.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Assuming the Ex-Date always precedes the Record Date**: for distributions of **25% or more** — which includes essentially every forward stock split — FINRA Rule 11140(b)(2) sets the ex-date to the first business day *after* the payable date. NVIDIA's 2024 10-for-1 split had record 2024-06-06, payable 2024-06-07 and ex-distribution 2024-06-10. A validator hard-coding `ex <= record` rejects these events outright, and a pipeline that infers the ex-date from the record date mis-dates the price adjustment by days. Declare `ex_date_convention='POST_PAYABLE'` for them.
- **Confusing the ex-date with the entitlement cut-off**: since US settlement moved to T+1 (SEC Rule 15c6-1(a), compliance date 2024-05-28), sub-25% distributions go ex **on** the record date; under the prior T+2 convention it was one business day earlier. Entitlement is fixed at the close *prior to* the ex-date — buying on the ex-date yields no dividend, and shares sold on the ex-date still entitle the seller. For a `POST_PAYABLE` event the ex-date is *after* payment, so an upcoming-ex alert does **not** mean the entitlement cut-off is still ahead.
- **Treating an Amended Event as a Duplicate**: vendors re-publish corrected events under the same identifier (ISO 15022 carries these as an MT 564 with function `REPL`, and cancellations as `CANC`). Deduplicating purely on `event_id` drops a moved ex-date or a revised dividend amount as if it were noise. Compare the payload and escalate a material difference.
- **One-Directional Reconciliation**: comparing only Golden-Source-vs-Vendor misses events the golden source lacks entirely. An event present in only one feed is the most common parity failure and must block automated processing until resolved.
- **Double-Counting Re-Broadcast Events**: corporate action feeds re-deliver amended or re-published events. Deduplicate on `event_id` (idempotent registration), not on symbol+date, or one announcement cycle will inflate entitlements and risk queries.
- **Reconciling Only the Dates**: two feeds can agree on `event_id`, all three lifecycle dates and the value while disagreeing on the *symbol* or *event type*. That is a security-master mapping failure that credits the entitlement to the wrong position, and a date-only comparison passes it as agreement.
- **Ignoring Currency Conversion on Foreign Dividends**: Failing to convert foreign cash dividends to portfolio base currency using the Payment Date exchange rate.
- **Missing Special Dividends**: Treating regular quarterly dividends and one-off special dividends identically, causing unexpected price drops on ex-date. A special dividend declared alongside the regular one shares its record and payment dates, so a "latest dividend" lookup silently returns whichever one it happens to hit and under-accrues the other.

## Verification

- Instantiate `CorporateActionEventCalendarEngine`. Register a cash dividend event ($1.50/share, Ex-Date: 2025-05-10, Record Date: 2025-05-11, Payment Date: 2025-05-25). Query upcoming events for 2025-05-08 with a 5-day window; verify event is returned. Calculate entitlement for 10,000 shares held on Record Date; verify $15,000 receivable logged.
- Register the same `event_id` twice; verify it is stored once (registration returns `False` on the duplicate).
- Register a second, later dividend ($1.00/share, Record Date: 2025-08-11) for the same symbol; verify the entitlement computed after 2025-08-11 reflects $1.00/share, not the first event.
- Register a `POST_PAYABLE` split with NVIDIA's real 2024 dates (declared 2024-05-22, record 2024-06-06, payable 2024-06-07, ex 2024-06-10); verify it is accepted, and that the same dates under the default `PRE_RECORD` convention raise `ValueError`.
- Re-register `EVT_DIV_01` with a changed ex-date and value; verify registration returns `False` **and** an ERROR is logged naming the differing fields, and that the stored event is unchanged.
- Verify reconciliation flags an event present in only one vendor feed, a symbol/event-type mismatch under a shared `event_id`, and rejects events with out-of-order dates or non-positive values at construction.
- Run `python scripts/test_corporate_action_event_calendar_integration.py`.

## Related Skills

- `corporate-action-adjusted-backtesting`
- `isin-cusip-sedol-cross-reference-service`
---
