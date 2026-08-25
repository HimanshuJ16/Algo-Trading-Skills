# Broker & Framework Coverage — global-exchange-holiday-calendar-handling

| Exchange / Library | Relevance to this skill |
|---|---|
| `exchange_calendars` (Python) | MIC-keyed exchange trading calendars covering holiday dates, early closes, per-exchange weekmasks, and historical trading hours. Session times are tz-aware UTC. Early closes are exposed as the `early_closes` `DatetimeIndex`; there is **no** `is_half_day()` method on `ExchangeCalendar`. |
| `pandas_market_calendars` | Market calendar library supporting pandas integration; an alternative primary source with its own, different coverage and API. |
| `XNYS` (NYSE) | Published holiday and early-close calendar. 2026 early closes: Friday 27 November and Thursday 24 December, both 13:00 ET. `XNAS`, `ARCX`, `XASE`, `BATS` are aliased to `XNYS` in `exchange_calendars`. |
| `XNSE` (NSE) | **Not present in `exchange_calendars`** — India is covered by `XBOM` (BSE) only, with `"BSE"` aliased to `XBOM`. A `get_calendar("XNSE")` call raises `InvalidCalendarName`. NSE publishes its trading-holiday list annually by circular; Diwali Muhurat trading is a live session held on Sunday 8 November 2026. |
| `XBOM` (BSE) | Session 09:15–15:30 `Asia/Kolkata`. Carries special weekmasks for genuine Saturday sessions (20 January 2024; Budget Day, 1 February 2025). |
| `XSAU` (Saudi Exchange) | Weekmask `"1111001"` — trades **Sunday–Thursday**, 10:00–15:00 `Asia/Riyadh`. Saudi Arabia observes no DST. |
| `XTAE` (Tel Aviv) | Traded **Sunday–Thursday** (weekmask `"1111001"`) until 2026-01-04 and moved to a Monday–Friday week effective 2026-01-05, with an early Friday close ahead of Shabbat. Any backtest spanning the boundary sees the trading week itself change. |
| Other global exchanges (`XLON`, `XTKS`, `XHKG`) | Referenced for listing-venue suffix resolution; session data must come from the calendar library, not this skill's static tables. |

## Timezone Notes

Session times must be stored as exchange-local wall clock plus an IANA zone and
converted to UTC per date. `America/New_York` observes DST (NYSE 09:30 = 14:30 UTC
under EST, 13:30 UTC under EDT); `Asia/Kolkata` (UTC+05:30) and `Asia/Riyadh`
(UTC+03:00) do not. Cross-exchange offsets therefore change twice a year, and the
change dates differ by region.

## Category

`data-management-global` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with international settlement cycle conventions (T+1 equity settlement rules, exchange holiday operational risk compliance).

A published calendar states what was *scheduled*. It is not a substitute for a
real-time exchange status feed: unscheduled halts, mid-session technical outages,
and ad hoc closures announced after publication do not appear in it. Where a
session-state decision gates live order flow, both sources are required.
