# Pre-Flight / Sign-off Checklist — multi-timezone-session-scheduling

Use this before considering the skill's implementation complete.

- [ ] **IANA Timezone Configuration:** Every `ExchangeSchedule` uses a tz database key, not a fixed
      offset, and the key resolves on the target host (`tzdata` installed where there is no system
      tz database — notably Windows).
- [ ] **Dynamic UTC Conversion:** `resolve_session()` / `get_session_utc()` recompute UTC bounds per
      date; no UTC offset is cached at startup or across dates.
- [ ] **DST Shift Verification:** Session times verified on both sides of a Northern *and* a
      Southern Hemisphere transition against the exchange's published local hours.
- [ ] **Transition-Day Boundaries:** A skipped ("spring forward") and a repeated ("fall back") local
      wall time each either raise under `strict=True` or appear in
      `ResolvedSession.nonexistent_boundaries` / `.ambiguous_boundaries` — never resolve silently.
- [ ] **Desynchronisation Windows:** `calculate_exchange_gap_minutes()` returns a *different* value
      inside and outside the March/October US–EU windows. A constant means an offset is cached.
- [ ] **Intraday Breaks:** Exchanges with a lunch halt (TSE 11:30–12:30 JST, HKEX 12:00–13:00 HKT)
      report `BREAK`, not `REGULAR_TRADING`, during it.
- [ ] **Published Hours Currency:** Session constants re-checked against the exchange's current
      calendar — e.g. TSE closes at 15:30 JST, not 15:00, since 2024-11-05.
- [ ] **Boundary Semantics:** Windows are half-open `[open, close)`; the closing instant is not
      classified as regular trading.
- [ ] **Fail-Loud Configuration:** An unknown exchange code and a naive `query_time_utc` both raise
      `SessionScheduleError` rather than returning a session state.
- [ ] **Registry Isolation:** `register_schedule()` on one scheduler leaves
      `DEFAULT_EXCHANGE_SCHEDULES` and every other instance unchanged.
- [ ] **Holiday Composition:** A non-CLOSED result is combined with a real holiday/half-day calendar
      before it authorises an order — this module is weekday-based only.
- [ ] **Host Clock:** Trading host verified to run in UTC (checked in system configuration, not
      assumed from the cloud image).
- [ ] **Automated Testing:** Run
      `python -m unittest discover -s skills/multi-timezone-session-scheduling/scripts`
      and confirm a 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Reviewed by: ___________________________
