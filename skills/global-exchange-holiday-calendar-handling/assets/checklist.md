# Pre-Flight / Sign-off Checklist — global-exchange-holiday-calendar-handling

Use this before considering the skill's implementation complete.

- [ ] **ISO Calendar Resolution:** Confirm instrument symbol mapping resolves to correct listing exchange code via `map_instrument_to_exchange()`.
- [ ] **Full & Half-Day Detection:** Confirm `get_session_info()` accurately distinguishes full holidays from half-day early closes.
- [ ] **DST Misalignment Verification:** Confirm session open/close timestamps are evaluated in UTC to account for regional DST transition weeks.
- [ ] **Fallback Calendar Availability:** Confirm fallback static holiday tables operate cleanly if third-party calendar packages are unavailable.
- [ ] **Automated Testing:** Run `python scripts/test_calendar_check.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
