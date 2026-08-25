# Pre-Flight / Sign-off Checklist — global-exchange-holiday-calendar-handling

Use this before considering the skill's implementation complete.

- [ ] **Calendar Source Coverage:** Confirm the calendar library resolves a calendar for *every* MIC in the instrument universe. `exchange_calendars` has no `XNSE` calendar — India is `XBOM` only.
- [ ] **Listing-Venue Resolution:** Confirm `map_instrument_to_exchange()` resolves each traded symbol to its listing exchange, and that an unrecognised symbol raises `UnresolvedInstrumentExchange` rather than defaulting to a house venue.
- [ ] **Trading Week From Weekmask:** Confirm no code path decides market state from `weekday()` ahead of the calendar lookup. Spot-check a Sunday-trading venue (`XSAU`) and a weekend session (NSE Muhurat, Sunday 2026-11-08).
- [ ] **Full & Half-Day Detection:** Confirm `get_session_info()` distinguishes full holidays from early closes, using the library's `early_closes` index (not a non-existent `is_half_day()`), and that `close_utc` reflects the shortened session.
- [ ] **DST Correctness:** Confirm the same exchange returns UTC open/close differing by exactly one hour between a date inside DST and one outside. Identical UTC times indicate a frozen offset.
- [ ] **No Fabricated Sessions:** Confirm an uncovered exchange, or a date outside `FALLBACK_COVERAGE_YEARS`, returns `UNKNOWN_NO_CALENDAR_DATA` with `open_utc`/`close_utc` of `None` — never a borrowed default session.
- [ ] **Unknown Is Not Closed:** Confirm `is_trading_day()`, `session_open_close()` and `SessionInfo.is_tradeable` all raise `CalendarDataUnavailable` on an unknown session rather than reporting the market closed.
- [ ] **Out-of-Week Special Sessions:** Confirm sessions held outside the normal trading week (NSE Muhurat, exchange-announced Saturday sessions) are not reported as `WEEKEND_CLOSED`, and that their timings are sourced from the exchange notice.
- [ ] **Source Attribution:** Confirm each `SessionInfo.source` is logged or persisted alongside any skipped/unexpected trading-day decision, so a stale calendar is diagnosable.
- [ ] **Fallback Freshness:** Confirm `FALLBACK_COVERAGE_YEARS` includes the current year and that the static tables were re-checked against each exchange's published calendar this year.
- [ ] **Live Status Feed:** Confirm calendar state is combined with a real-time exchange status source before it gates live order flow — a calendar does not know about unscheduled halts.
- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/global-exchange-holiday-calendar-handling/scripts` and confirm a 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
