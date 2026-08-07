---
name: global-exchange-holiday-calendar-handling
description: Use when a bot or backtest operates across more than one country's exchange,
  to avoid treating a foreign market holiday as a normal trading day (or vice versa)
domain: algorithmic-trading
subdomain: data-management-global
tags:
- data-management-global
- exchange_calendars-(python)
- pandas_market_calendars
brokers_frameworks:
- exchange_calendars (Python)
- pandas_market_calendars
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a system needs to know "is the market open today" for any exchange outside the one it was originally built against, or whenever a backtest spans historical data from more than one exchange. A bot built and tested only against one country's calendar (e.g. NSE/BSE) will silently misbehave the first time it's pointed at a different exchange (NYSE, LSE, HKEX) if holiday/half-day logic was hardcoded rather than sourced from an actual per-exchange calendar — treating a foreign holiday as a trading day causes stale-data processing or unnecessary alerting; treating a trading day as a holiday causes missed signals.

## Prerequisites

- A maintained per-exchange trading calendar source (e.g. the `exchange_calendars` or `pandas_market_calendars` Python libraries, which encode holiday schedules, half-days, and even historically-changed trading hours for dozens of exchanges) rather than a hand-maintained holiday list
- Clarity on which exchange's calendar governs which instrument — for cross-listed or ADR instruments this is not always obvious and must be decided explicitly

## Workflow

1. Never hand-maintain a holiday list for any exchange beyond the one the system was originally built for — holiday calendars change yearly (governments add/move holidays, exchanges announce ad hoc closures), and a hardcoded list silently goes stale; use a maintained calendar library and treat updating it as a routine dependency-update task, not a one-time setup step.
2. For any multi-exchange system, explicitly resolve which calendar applies per instrument at the point of use (e.g., a US-listed ADR of a UK company trades on NYSE's calendar for order-placement purposes, not LSE's) — don't assume the underlying company's home-country calendar governs.
3. Distinguish full-day closures from half-days/early closes (many exchanges have early closes around certain holidays, e.g. the day after Thanksgiving on US markets) — a system that only checks "is today a holiday" (boolean) rather than "what are today's actual open/close times" will misbehave specifically on early-close days, generating signals or placing orders after the effective close.
4. For backtests spanning historical data, verify the calendar library's historical accuracy for the specific date range in use — trading hours and holiday schedules have changed over time (extended hours changes, new holidays added), and a calendar library that's only accurate for "current" rules will misdate historical bars near a rule-change boundary.
5. Handle the specific case of a bot that runs across multiple exchanges simultaneously (e.g., a global multi-asset strategy) by computing each exchange's open/close independently rather than deriving one from another via a fixed offset — time-zone offsets between exchanges are not constant year-round because not all countries observe daylight saving time on the same schedule (or at all), so a fixed-offset assumption breaks specifically during the weeks each side's DST transition happens on a different date (see `multi-timezone-session-scheduling` for the DST-specific handling).
6. Log which calendar/holiday determination was used for any skipped or unexpected trading-day decision, so a misconfigured or stale calendar is diagnosable rather than presenting as an unexplained missed session.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Hardcoding a holiday list that was accurate at build time and silently goes stale as holiday schedules change year to year.
- Checking only "is today a holiday" as a boolean without accounting for half-days/early closes, causing after-close order attempts or signal generation on early-close days.
- Assuming a fixed timezone offset between two exchanges' local times holds year-round, breaking during the 1-3 week window each year when the two countries' DST transitions don't align.
- Using a company's home-country calendar for an instrument that's actually listed (and must be traded according to) a different exchange's calendar, as is common for ADRs and cross-listings.
- Using a calendar library's current-rules view for historical backtest dates without confirming it correctly reflects the rules that were actually in effect at that historical date.

## Verification

- For each exchange the system trades on, compare the library-derived holiday/half-day schedule for the current year against that exchange's officially published calendar and confirm they match.
- Test a known historical early-close date (e.g., day after US Thanksgiving in a recent year) and confirm the system's open/close time computation reflects the shortened session, not a full day.
- Test a known DST-misalignment week (a week where, e.g., US and EU DST transitions don't coincide) and confirm cross-exchange timing calculations remain correct rather than silently off by an hour.

## Related Skills

- `multi-timezone-session-scheduling`
- `multi-currency-pnl-and-fx-conversion`
- `lookahead-bias-elimination`
