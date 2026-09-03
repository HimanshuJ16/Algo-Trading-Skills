# Deep Workflow Reference — global-exchange-holiday-calendar-handling

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Listing-Venue Resolution (explicit, never defaulted):**
   - Map instrument symbols to their primary *listing* exchange MIC using
     `map_instrument_to_exchange()`: an explicit ADR/cross-listing table first
     (`INFY` ADR → `XNYS`), then a venue-suffix table (`INFY.NS` → `XNSE`,
     `VOD.L` → `XLON`, `7203.T` → `XTKS`).
   - A symbol matching neither raises `UnresolvedInstrumentExchange`. Defaulting an
     unrecognised ticker to a house venue is how an ADR gets checked against its
     issuer's home calendar instead of the calendar it settles on.

2. **Calendar-Source Coverage Check (do this before trusting any lookup):**
   - Confirm the calendar library actually has a calendar for every MIC in the
     universe. `exchange_calendars` ships **no `XNSE` calendar** — India is covered
     by `XBOM` (BSE) only, with `"BSE"` aliased to `XBOM` — so `get_calendar("XNSE")`
     raises `InvalidCalendarName`.
   - `XNAS`, `ARCX`, `XASE` and `BATS` are aliases of `XNYS` in that library: US
     venues share one calendar there, which is fine for holidays but is not a
     statement that their session times are identical for every product.

3. **Session Status & Half-Day Detection:**
   - Query `GlobalExchangeCalendarManager.get_session_info()` for one of
     `REGULAR_SESSION`, `HALF_DAY_EARLY_CLOSE`, `FULL_DAY_HOLIDAY`, `WEEKEND_CLOSED`,
     or `UNKNOWN_NO_CALENDAR_DATA`.
   - Detect early closes via the library's `early_closes` `DatetimeIndex`.
     `ExchangeCalendar` has **no `is_half_day()` method**; calling one raises
     `AttributeError`, and inside a broad `except Exception` that failure is
     invisible — every lookup silently degrades to fallback data.

4. **Trading-Week Determination (weekmask, not weekday):**
   - Take the trading week from the calendar's `weekmask` (7-character Monday-first
     string, `numpy.busdaycalendar` convention), never from `weekday() >= 5`.
   - `XSAU` (Saudi Exchange) has weekmask `"1111001"` — Sunday–Thursday. `XTAE`
     (Tel Aviv) used `"1111001"` until 2026-01-04 and moved to Monday–Friday
     effective 2026-01-05. `XBOM` carries special weekmasks for genuine Saturday
     sessions (20 January 2024; Budget Day, 1 February 2025).
   - NSE runs its Diwali Muhurat session on **Sunday 8 November 2026**. A weekday
     short-circuit reports every one of these as closed.

5. **Early Close & Session-Boundary Timing:**
   - On early close days (NYSE 2026: Friday 27 November and Thursday 24 December,
     both 13:00 ET), read the exact UTC bounds from `open_utc` / `close_utc`.
   - Gate order submission and signal evaluation on those boundaries, not on a
     nominal full-day close.

6. **DST-Correct UTC Conversion:**
   - Store session times as exchange-local wall clock plus an IANA zone and convert
     per date. NYSE's 09:30 open is 14:30 UTC under EST and 13:30 UTC under EDT;
     a frozen UTC offset is wrong for roughly eight months of the year.
   - IST (`Asia/Kolkata`, UTC+05:30) and AST (`Asia/Riyadh`, UTC+03:00) observe no
     DST, so cross-exchange offsets against US/EU venues move twice a year.
   - Require a `datetime.date`, not a `datetime`, at the API boundary: one instant
     falls on different calendar dates in different exchange timezones.

7. **Degraded Mode That Fails Loudly:**
   - The static tables (`EXCHANGE_PROFILES`, `FALLBACK_HOLIDAYS`,
     `FALLBACK_HALF_DAYS`) are a degraded mode, **not** a calendar. They are pinned
     to `FALLBACK_COVERAGE_YEARS` and refuse to answer outside it, so a stale
     checkout errors instead of quietly serving last year's holidays.
   - An exchange is answerable only if it appears in **both** `EXCHANGE_PROFILES`
     and `FALLBACK_HOLIDAYS` (`FALLBACK_COVERED_EXCHANGES`). A profile without a
     holiday table would report `REGULAR_SESSION` on that exchange's holidays.
   - Anything uncovered returns `UNKNOWN_NO_CALENDAR_DATA` with `open_utc`/
     `close_utc` of `None`. `is_trading_day()`, `session_open_close()` and
     `SessionInfo.is_tradeable` all raise `CalendarDataUnavailable` rather than
     collapsing unknown into "closed" — including for a caller reading the
     attribute directly instead of going through the manager.
   - `FALLBACK_SPECIAL_SESSIONS` records sessions held *outside* the normal
     trading week (NSE Diwali Muhurat, Sunday 2026-11-08) and is checked ahead of
     the weekmask. Their timings are announced by separate circular and are not in
     the annual holiday calendar, so the resolver reports them as unknown rather
     than either inventing hours or reporting a live market closed.
   - Every `SessionInfo` carries `source` (`EXCHANGE_CALENDARS` / `STATIC_FALLBACK`
     / `NONE`) so a wrong decision is attributable to the calendar that produced it.

## Known Failure Modes

- **Silent Fallback on a Dead Primary Path:** calling a non-existent calendar method
  inside `except Exception` — the library path raises on every request, the warning
  scrolls past, and the system runs indefinitely on static data while reporting
  healthy.
- **Default-Venue Session Fabrication:** a fallback that returns one exchange's
  hours for any unrecognised MIC, so an LSE or NSE lookup silently receives NYSE's
  open/close and no error is ever raised.
- **Frozen UTC Offsets:** storing session bounds directly in UTC, which is correct
  only outside DST and an hour wrong inside it.
- **Weekend Short-Circuit:** deciding market state from `weekday()` before the
  calendar is consulted, wrongly closing Sunday-trading venues and special Saturday
  or Muhurat sessions.
- **Hardcoded Holiday Lists:** hand-maintained tables that go stale when holiday
  dates move year to year (Holi fell on 2026-03-03, Diwali-Balipratipada on
  2026-11-10) or when an exchange announces an ad hoc closure mid-year.
- **Cross-Listed ADR Calendar Misassignment:** checking the home-country calendar
  (NSE) for a US-traded ADR (Infosys on NYSE).
- **Uncovered Venue Assumed Covered:** assuming `exchange_calendars` has an `XNSE`
  calendar because NSE is a major exchange.

## Production Implementation Reference

- Reference code: `scripts/calendar_check.py` (`GlobalExchangeCalendarManager`,
  `SessionInfo`, `SessionStatus`, `CalendarSource`, `ExchangeProfile`,
  `map_instrument_to_exchange`, `CalendarDataUnavailable`,
  `UnresolvedInstrumentExchange`).
- Automated unit tests: `scripts/test_calendar_check.py`.
