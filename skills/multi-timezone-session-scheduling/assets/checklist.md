# Pre-Flight / Sign-off Checklist — multi-timezone-session-scheduling

Use this before considering the skill's implementation complete.

- [ ] **IANA Timezone Configuration:** Confirm all exchange schedules utilize valid IANA timezone strings via `ExchangeSchedule`.
- [ ] **Dynamic UTC Conversion:** Confirm `get_session_utc()` recomputes UTC open/close times per date rather than using cached static offsets.
- [ ] **DST Shift Verification:** Confirm test calendar validates Northern and Southern Hemisphere DST transition dates.
- [ ] **Session State Evaluation:** Confirm `get_market_status()` accurately classifies `REGULAR_TRADING`, `PRE_MARKET`, and `MARKET_CLOSED`.
- [ ] **Automated Testing:** Run `python scripts/test_session_scheduler.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
