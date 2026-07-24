# Deep Workflow Reference — global-exchange-holiday-calendar-handling

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Exchange Calendar ISO Code Resolution:**
   - Map instrument symbols to primary listing exchange ISO codes using `map_instrument_to_exchange()` (e.g. `INFY` ADR $\rightarrow$ `XNYS`, `INFY.NS` $\rightarrow$ `XNSE`).

2. **Session Status & Half-Day Detection:**
   - Query `GlobalExchangeCalendarManager.get_session_info()` to obtain session status: `REGULAR_SESSION`, `HALF_DAY_EARLY_CLOSE`, `FULL_DAY_HOLIDAY`, or `WEEKEND_CLOSED`.

3. **Half-Day & Early Close Timing:**
   - On early close days (e.g. US Thanksgiving Friday 13:00 EST close), retrieve exact UTC open and close timestamps via `open_utc` and `close_utc`.
   - Prevent submitting orders or evaluating signals after early close boundaries.

4. **DST-Aware UTC Session Calculation:**
   - Evaluate session open/close in UTC rather than fixed local hour offsets to prevent 1-hour schedule errors during US/EU Daylight Saving Time transition misalignment weeks.

5. **Fallback Calendar Resilience:**
   - Use fallback static holiday/half-day tables (`FALLBACK_HOLIDAYS`, `FALLBACK_HALF_DAYS`) to guarantee system operational stability even if third-party `exchange_calendars` packages are unavailable.

## Failure Modes Observed in Production

- **Hardcoded Holiday Lists:** Hand-maintaining static lists that become stale when exchange authorities modify holiday schedules or announce ad-hoc closures.
- **Half-Day Overrun:** Treating early close days as regular full-day sessions, generating after-hours order rejections.
- **Cross-Listed ADR Calendar Misassignment:** Checking the home country calendar (e.g. Indian NSE) for a US-traded ADR (e.g. Infosys on NYSE).
- **DST Shift Disconnection:** Using fixed UTC hour offsets year-round, causing 1-hour execution misalignments during spring/fall DST transitions.

## Production Implementation Reference

- Reference code: `scripts/calendar_check.py` (`GlobalExchangeCalendarManager`, `SessionInfo`, `SessionStatus`, `map_instrument_to_exchange`).
- Automated unit tests: `scripts/test_calendar_check.py`.
