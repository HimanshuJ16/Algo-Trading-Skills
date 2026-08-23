# Workflows for Daylight Saving Time Transition Handling

## 1. Register and validate exchange schedules

- Register each venue as `ExchangeScheduleSpec(exchange_id, exchange_name, iana_timezone, local_open_time, local_close_time)`.
- Registration validates eagerly and raises `DstScheduleError` naming the exchange: unresolvable IANA zone, malformed `HH:MM`, out-of-range hour/minute, or a close that is not after the open.
- For an overnight session (CME Globex 17:00 → 16:00), set `spans_midnight=True`; the close then rolls to the next calendar day and re-resolves its own offset. Without the flag the registration is rejected rather than producing a negative session length.

## 2. Convert local session boundaries to UTC

- `calculate_utc_session(exchange_id, "YYYY-MM-DD")` returns a `UtcSessionWindow` with `utc_open_iso` / `utc_close_iso`, 64-bit `utc_open_ns` / `utc_close_ns` epochs, and `session_duration_hours`.
- The open and close each resolve their own IANA offset (`utc_offset_open_hours`, `utc_offset_close_hours`). Never cache one offset for the day.
- `session_duration_hours` is computed from the UTC epochs, so a session containing a transition reports its true elapsed length rather than the nominal clock span.

## 3. Classify the local wall time against the transition

- `local_open_is_nonexistent` — the configured wall time is skipped by "spring forward". The default resolution applies the pre-transition offset, which is a guess; correct the schedule or route the date through a special-session calendar.
- `local_open_is_ambiguous` — the wall time is repeated by "fall back". The default resolves to the **first** (`fold=0`, pre-transition) occurrence per PEP 495. Confirm this matches the venue's intent.
- `dst_shift_inside_session` — the transition falls between open and close; elapsed length differs from the nominal span by an hour. Do not bucket bars on local wall time across such a session.
- Construct `DstTransitionHandlerEngine(strict=True)` to raise `DstScheduleError` on the first two cases instead of flagging them. Prefer strict mode for unattended schedulers.

## 4. Audit cross-border desynchronization

- `audit_global_dst_transitions("YYYY-MM-DD")` evaluates every registered exchange and compares the US and EU legs.
- Legs resolve by IANA time zone (`US_DST_TIMEZONES` / `EU_DST_TIMEZONES`), with a fallback to the legacy `NYSE` / `LSE` ids, so MIC-coded registrations (`XNYS`, `XLON`) work. Pin them with `us_exchange_id` / `eu_exchange_id`.
- Always read `us_exchange_id` and `eu_exchange_id` off the report. If either is `None` the audit did not run and the report carries a `SKIPPED` warning — `is_us_eu_desync_window=False` on its own does not mean "no desync".
- Multiple candidate exchanges on one side produce a warning and use the first registered; disambiguate explicitly.

## 5. Recalibrate schedules

- Drive cron triggers and strategy timers from `utc_open_ns` / `utc_close_ns` recomputed per session date — not from a UTC time fixed at deployment.
- Use `us_eu_offset_delta_hours` (−5h aligned, −4h in either desync window) and `us_eu_overlap_hours` to resize cross-border execution windows. Do not schedule the shift against hard-coded calendar dates: the spring window is 14 or 21 days depending on the year, and the autumn window is 7 days.
- Re-run the audit daily during March and late October / early November, and alert on any change in `us_eu_offset_delta_hours`.

## 6. Keep the tz database current

- Pin the `tzdata` version alongside the backtest for reproducibility; refresh it on a defined cadence for live trading.
- Windows hosts and slim containers have no system tz database — install the `tzdata` package or every zone lookup fails.
- Re-verify the statutory status in `references/standards.md` before relying on it; both the US and EU have live proposals to abolish seasonal clock changes.
