# Deep Workflow Reference — multi-timezone-session-scheduling

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **IANA Zone-Aware Timezone Storage:**
   - Store and reason about exchange trading hours using explicit IANA timezone identifiers (e.g. `America/New_York`, `Europe/London`, `Asia/Kolkata`, `Australia/Sydney`).

2. **Dynamic UTC Session Conversion:**
   - Compute session open and close times in UTC dynamically per date via `MultiTimezoneSessionScheduler.get_session_utc(exchange_code, date)`.
   - Never precompute or cache static UTC offsets across dates.

3. **Asynchronous DST Transition Alignment:**
   - Account for 2-3 week DST transition misalignment windows between North America and Europe in March and October.
   - Account for Southern Hemisphere inverse DST transitions (e.g., Sydney, Australia shifting DST in opposite directions).

4. **Market Session State Evaluation:**
   - Query `get_market_status()` to evaluate session state (`PRE_MARKET`, `REGULAR_TRADING`, `POST_MARKET`, `MARKET_CLOSED`).

5. **Cross-Exchange Sequential Handoff:**
   - Recompute inter-market session gaps via `calculate_exchange_gap_minutes()` on each query date rather than assuming fixed gaps year-round.

## Failure Modes Observed in Production

- **Hardcoded UTC Offsets:** Hardcoding "NYSE opens at UTC-5", causing 1-hour schedule errors during US Daylight Saving Time (UTC-4).
- **Static Cron Scheduling:** Scheduling EOD tasks at a fixed UTC time without adjusting for DST shifts.
- **Asynchronous DST Misalignment:** Assuming constant 5-hour time differences between London and New York year-round.
- **Southern Hemisphere Inversion:** Applying Northern Hemisphere DST rules to Southern Hemisphere exchanges.

## Production Implementation Reference

- Reference code: `scripts/session_scheduler.py` (`MultiTimezoneSessionScheduler`, `ExchangeSchedule`, `MarketSessionState`).
- Automated unit tests: `scripts/test_session_scheduler.py`.
