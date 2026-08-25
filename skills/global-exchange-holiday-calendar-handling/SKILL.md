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
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a system needs to know "is the market open today" for any exchange outside the one it was originally built against, or whenever a backtest spans historical data from more than one exchange. A bot built and tested only against one country's calendar (e.g. NSE/BSE) will silently misbehave the first time it's pointed at a different exchange (NYSE, LSE, HKEX) if holiday/half-day logic was hardcoded rather than sourced from an actual per-exchange calendar — treating a foreign holiday as a trading day causes stale-data processing or unnecessary alerting; treating a trading day as a holiday causes missed signals.

Do NOT use this as a substitute for a real-time exchange status feed. A calendar tells you what was *scheduled*; it does not know about an unscheduled halt, a mid-session technical outage, or an ad hoc closure announced after the calendar was published. Session-state decisions during live trading need both.

## Prerequisites

- A maintained per-exchange trading calendar source (e.g. the `exchange_calendars` or `pandas_market_calendars` Python libraries, which encode holiday schedules, half-days, and even historically-changed trading hours for dozens of exchanges) rather than a hand-maintained holiday list
- Confirmation that the calendar source actually covers every exchange in the instrument universe. Coverage is not uniform: `exchange_calendars` ships no NSE (`XNSE`) calendar at all — India is covered by `XBOM` (BSE) only — so a lookup by NSE's MIC raises rather than returning a calendar.
- Clarity on which exchange's calendar governs which instrument — for cross-listed or ADR instruments this is not always obvious and must be decided explicitly

## Workflow

1. Never hand-maintain a holiday list for any exchange beyond the one the system was originally built for — holiday calendars change yearly (governments add/move holidays, exchanges announce ad hoc closures), and a hardcoded list silently goes stale; use a maintained calendar library and treat updating it as a routine dependency-update task, not a one-time setup step.
2. If a static table is kept as a degraded-mode fallback, pin it to explicit coverage years and make it refuse to answer outside them. A fallback that extrapolates is worse than no fallback, because it returns a confident wrong answer instead of an error.
3. For any multi-exchange system, explicitly resolve which calendar applies per instrument at the point of use (e.g., a US-listed ADR of a UK company trades on NYSE's calendar for order-placement purposes, not LSE's) — don't assume the underlying company's home-country calendar governs, and don't let an unresolved symbol fall through to a default venue: raise instead, because a silent default is how an ADR ends up checked against its issuer's home calendar.
4. Never decide "is this a trading day" from the weekday alone. The Saturday/Sunday weekend is a regional convention, not a market universal: the Saudi Exchange trades Sunday–Thursday, the Tel Aviv Stock Exchange traded Sunday–Thursday until it moved to Monday–Friday on 2026-01-05, NSE holds its Diwali Muhurat session on a Sunday (8 November 2026), and BSE/NSE have held full special sessions on Saturdays (e.g. Budget Day, 1 February 2025). Take the trading week from the calendar's own weekmask; a `weekday() >= 5` short-circuit placed ahead of the calendar lookup will override correct data with a wrong answer.
5. Distinguish full-day closures from half-days/early closes (many exchanges have early closes around certain holidays, e.g. the day after Thanksgiving on US markets) — a system that only checks "is today a holiday" (boolean) rather than "what are today's actual open/close times" will misbehave specifically on early-close days, generating signals or placing orders after the effective close. Check the API the library actually exposes: `exchange_calendars` provides an `early_closes` `DatetimeIndex`, and has no `is_half_day()` method, so a call to one raises `AttributeError`.
6. Store session times as exchange-local wall clock plus an IANA timezone, and convert to UTC per date. Freezing a UTC offset into the data reintroduces the DST bug at the point where it is hardest to see: NYSE's 09:30 open is 14:30 UTC under EST but 13:30 UTC under EDT.
7. For backtests spanning historical data, verify the calendar library's historical accuracy for the specific date range in use — trading hours, holiday schedules, and even the trading week itself have changed over time, and a calendar library that's only accurate for "current" rules will misdate historical bars near a rule-change boundary.
8. Handle the specific case of a bot that runs across multiple exchanges simultaneously (e.g., a global multi-asset strategy) by computing each exchange's open/close independently rather than deriving one from another via a fixed offset — time-zone offsets between exchanges are not constant year-round because not all countries observe daylight saving time on the same schedule (or at all), so a fixed-offset assumption breaks specifically during the weeks each side's DST transition happens on a different date (see `multi-timezone-session-scheduling` for the DST-specific handling).
9. Return an explicit "unknown" state when no calendar covers an (exchange, date) pair, and never collapse it into "closed" or into a guessed session. Propagate which source answered (library vs fallback) on the result so a wrong decision is traceable to its origin rather than presenting as an unexplained missed session.
10. Log which calendar/holiday determination was used for any skipped or unexpected trading-day decision, so a misconfigured or stale calendar is diagnosable.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Hardcoding a holiday list that was accurate at build time and silently goes stale as holiday schedules change year to year.
- Letting a degraded-mode fallback answer for an exchange or year it has no data for. Borrowing one exchange's session hours as a default for every other exchange turns a missing-calendar bug into a confidently wrong open/close time, and no error is ever raised.
- Short-circuiting on `weekday() >= 5` before consulting the calendar, which reports Sunday-trading exchanges (Saudi Exchange; TASE before 2026-01-05) and special weekend sessions (NSE Muhurat, BSE Budget-Day Saturdays) as closed regardless of what the calendar says.
- Checking only "is today a holiday" as a boolean without accounting for half-days/early closes, causing after-close order attempts or signal generation on early-close days.
- Calling a calendar method that does not exist (`is_half_day()` on `exchange_calendars`) inside a broad `except Exception`, so the primary path raises on every call, the exception is swallowed, and the system runs permanently on fallback data while appearing healthy.
- Assuming a fixed timezone offset between two exchanges' local times holds year-round, breaking during the 1-3 week window each year when the two countries' DST transitions don't align.
- Assuming a calendar library covers every venue you trade. `exchange_calendars` has no `XNSE` calendar; a lookup raises, and if that exception is swallowed the system silently substitutes whatever the fallback returns.
- Using a company's home-country calendar for an instrument that's actually listed (and must be traded according to) a different exchange's calendar, as is common for ADRs and cross-listings.
- Defaulting an unrecognised symbol to a house exchange instead of raising, so a mis-typed or newly-added ticker is silently traded against the wrong calendar.
- Passing a `datetime` where a calendar date is wanted: one instant falls on different calendar dates in different exchange timezones, so an implicit `.date()` quietly assumes the caller's zone is the exchange's.
- Using a calendar library's current-rules view for historical backtest dates without confirming it correctly reflects the rules that were actually in effect at that historical date.

## Verification

- For each exchange the system trades on, compare the library-derived holiday/half-day schedule for the current year against that exchange's officially published calendar and confirm they match.
- Confirm the calendar source actually resolves for every MIC in the instrument universe, and that a lookup for an uncovered venue produces an error or an explicit "unknown", not a session.
- Test a known historical early-close date (e.g., day after US Thanksgiving in a recent year) and confirm the system's open/close time computation reflects the shortened session, not a full day.
- Test the same exchange on one date inside DST and one outside, and confirm the UTC open/close differ by exactly one hour. Identical UTC times across the two dates indicate a frozen offset.
- Test a known weekend-session date (NSE Muhurat trading, Sunday 8 November 2026) and a Sunday-trading exchange, and confirm the system reports them open.
- Test a known DST-misalignment week (a week where, e.g., US and EU DST transitions don't coincide) and confirm cross-exchange timing calculations remain correct rather than silently off by an hour.

## Related Skills

- `multi-timezone-session-scheduling`
- `multi-currency-pnl-and-fx-conversion`
- `lookahead-bias-elimination`
