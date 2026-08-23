# Workflows for Deployment Freeze Windows Around Market Events

## 1. Calendar ingestion and freshness

1. Pull the macro schedule from its source (Fed FOMC calendar, BLS release schedules, exchange expiry calendar) on a defined cadence, and call `set_calendar_as_of` with the pull time on every refresh.
2. Anchor each event to the **release instant in its own timezone**, then convert to epoch. FOMC statements are released at 2:00 p.m. ET — EDT in summer, EST in winter — so an event stored as a fixed UTC time is an hour wrong for part of the year.
3. Register multi-part events as multiple windows. The FOMC press conference starts 30 minutes after the statement; a 30-minute post-buffer on the statement expires exactly as the press conference begins.
4. Re-registering a moved release under its existing `event_id` is rejected. Rebuild the calendar rather than layering a correction on top of a stale window.
5. Set `max_calendar_staleness_sec` to your refresh SLA. A calendar that has not been refreshed cannot certify "no freeze active" — releases are rescheduled and cancelled (2025 lapse in appropriations).

## 2. Session window definition

1. Give each window an IANA timezone and a local `HH:MM` anchor. Buffers are minutes either side.
2. Set `weekdays` for the venue's trading days (Monday–Friday by default).
3. Feed `session_overrides` from the exchange calendar:
   - `"2026-12-24": "13:00"` — early close, window moves.
   - `"2026-07-03": None` — no session, window suppressed.
4. Windows near midnight are evaluated against the local day before, of, and after the request, so a window that straddles midnight still matches.
5. A local anchor inside a spring-forward gap does not exist; that occurrence is skipped with a warning rather than silently shifted. Anchors in the fall-back repeated hour resolve to the first occurrence.

## 3. Request evaluation order

The order matters — each step is a distinct fail-closed decision:

1. **Environment classification.** Exempt → approve. Production → continue. Unknown → deny. Never treat an unrecognised name as non-production.
2. **Calendar freshness.** Stale or never refreshed → block. This is a *blocking condition*, not a hard error, so the break-glass path can still lift it during an incident.
3. **Freeze windows.** Collect every covering window; bounds are inclusive. Governing = latest-ending, ties by label. Report all labels plus the final lift time.
4. **Break-glass** only if a blocking condition exists. Outside a freeze, an emergency request is just an ordinary approval — do not consume the override.

## 4. Break-glass validation

1. Require `is_emergency_hotfix`, both approval booleans, both approver ids, and a justification (change ticket or incident id).
2. Compare the two ids case-insensitively and reject a match: one person holding both roles is not dual authorisation.
3. Resolve identities from authenticated IAM claims **before** building the request. This module cannot tell a real identity from a string.
4. Log the override at WARNING with both identities, and persist the report — for EU/EEA firms that record is the RTS 6 Art. 11 change record (when, who, who approved, nature).
5. Follow up out of band: bound the override to the single change, review it retrospectively, and confirm the freeze policy was not simply routed around.

## 5. Pipeline integration

1. Call the guard in a required CI/CD job and fail the job on `is_approved == False`. A report nobody blocks on is documentation, not a control.
2. Surface `active_freeze_labels` and `freeze_ends_epoch_sec` in the job output — "blocked until 19:00 UTC by FOMC Press Conference" is actionable; "blocked" is not.
3. Match on the status constants (`DEPLOYMENT_BLOCKED_FREEZE_ACTIVE`, `UNKNOWN_ENVIRONMENT_DENIED`, ...), not on the prose in `applied_policy`.
4. Archive every report, approvals and denials alike, to the same audit store used for change records.

## 6. Calibration and review

1. Review blocked and overridden deployments periodically: frequent break-glass use means the freeze windows or the release process need changing, not that the control works.
2. Size buffers against your own rollback time — a freeze shorter than the time to detect and revert a bad release is theatre.
3. Re-check session windows whenever a venue changes hours, and re-check event anchors whenever a statistical agency changes its publication schedule.
