---
name: daylight-saving-time-transition-handling
description: IANA zone-aware market session engine for Daylight Saving Time transitions
  across US, EU, and Asian exchanges — computes UTC session opens and nanosecond epochs,
  flags skipped/repeated local wall times, and detects the US-EU desynchronization windows.
domain: Data Management Global
subdomain: Timezone & Session Scheduling
tags:
- dst-transition
- timezone-handling
- utc-normalization
- iana-timezones
- session-scheduling
- cross-border-desync
- market-hours
brokers_frameworks:
- Python zoneinfo
- IANA Time Zone Database
- Python Dataclasses
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in global market data pipelines, cross-border relative-value algorithms, and session schedulers spanning US (NYSE/Nasdaq), European (LSE/XETRA), and Asian (TSE/HKEX) exchanges — anywhere a session boundary, a cron trigger, or a bar-aggregation boundary is expressed in exchange-local time and consumed in UTC.

Two statutory rules drive the behaviour, and they do not line up:

- **US** — DST runs from 02:00 **local** time on the second Sunday of March to 02:00 local on the first Sunday of November (15 U.S.C. § 260a, as amended by the Energy Policy Act of 2005). Because the rule is stated in local time, US zones transition an hour apart from each other (Eastern before Central, and so on).
- **EU** — summer time runs from 01:00 **GMT** on the last Sunday of March to 01:00 GMT on the last Sunday of October (Directive 2000/84/EC, Arts. 2–3). Because the rule is stated in GMT, every Member State transitions at the same instant.

The mismatch produces two **desynchronization windows** each year, during which the transatlantic overlap shifts by one hour. Their lengths are **not** fixed:

| Window | Boundaries | Length |
|---|---|---|
| Spring | US start (2nd Sun Mar) → EU start (last Sun Mar) | **14 days normally, 21 days whenever 1 March is a Sunday** (2020, 2024, 2025, 2026, 2030, 2031 …) |
| Autumn | EU end (last Sun Oct) → US end (1st Sun Nov) | **always exactly 7 days** |

Treat any "the desync window is two weeks" assumption as a bug: it is wrong for every autumn window and wrong for roughly a third of spring windows.

## When NOT to Use

- **Holiday and half-day calendars** — this engine answers "what UTC instant is this local session boundary", not "is the exchange open today". Pair it with `global-exchange-holiday-calendar-handling`.
- **Intraday session-state queries** (pre-market / regular / post / closed) — use `multi-timezone-session-scheduling`.
- **Zones with no DST** (`Asia/Tokyo`, `Asia/Kolkata`, `Europe/Moscow`, `Europe/Istanbul`) — the engine handles them correctly, but a plain fixed-offset conversion would too; the desync audit has nothing to report.

## Prerequisites

- Python 3.9+ for `zoneinfo` (this module uses the standard library only).
- **A resolvable IANA tz database.** `zoneinfo` reads the *system* tz database; Windows has none (`zoneinfo.TZPATH` is empty there), so the `tzdata` PyPI package must be installed or every `ZoneInfo(...)` lookup raises `ZoneInfoNotFoundError`. This is not listed in the repository's `requirements.txt` — install it explicitly on Windows hosts and in slim Linux containers.
- IANA zone names (`America/New_York`, `Europe/London`, `Asia/Tokyo`) — never fixed UTC offsets.
- Exchange session times in local time as `HH:MM` (`local_open_time` e.g. `"09:30"`, `local_close_time` e.g. `"16:00"`).

## Workflow

1. **Register exchange schedules** — `register_exchange(ExchangeScheduleSpec(...))` validates the IANA zone and the `HH:MM` strings up front and raises `DstScheduleError` naming the offending exchange, rather than failing deep inside a later conversion. For an overnight session whose close is earlier on the clock than its open (CME Globex 17:00 → 16:00), set `spans_midnight=True`; otherwise a close that is not after the open is rejected, because silently computing a negative session length is worse than refusing the schedule.
2. **Compute the UTC session window** — `calculate_utc_session(exchange_id, "YYYY-MM-DD")` resolves the IANA offset **independently at the open and at the close**, rather than caching one offset for the day. That is what makes a session containing the transition come back with its true elapsed length (7h or 9h for a nominal 8h span), exposed as `session_duration_hours` alongside `utc_offset_open_hours` / `utc_offset_close_hours` and the `dst_shift_inside_session` flag.
3. **Decide what to do about a flagged wall time** — do not treat `local_open_is_nonexistent` or `local_open_is_ambiguous` as advisory noise:
   - *Non-existent* (spring forward skipped it): the configured session time never occurs. The default resolution applies the pre-transition offset, which is a guess. Fix the schedule or route the day through your holiday/special-session calendar.
   - *Ambiguous* (fall back repeated it): the default resolves to the **first** (pre-transition, `fold=0`) occurrence per PEP 495. If your venue means the second, adjust explicitly — do not assume the default matches the exchange's intent.
   - Construct the engine with `strict=True` to convert both cases into a `DstScheduleError` instead of a flag, which is the right default for an unattended scheduler that must not act on a guessed timestamp.
4. **Audit cross-border desynchronization** — `audit_global_dst_transitions("YYYY-MM-DD")` resolves the US and EU legs **by IANA time zone**, so it works with MIC-coded ids (`XNYS`/`XLON`) as well as the legacy `NYSE`/`LSE` ids; pass `us_exchange_id` / `eu_exchange_id` to pin them. Read `us_exchange_id` / `eu_exchange_id` on the returned report to confirm which legs were actually compared — when neither resolves, the report carries a `SKIPPED` warning rather than presenting `is_us_eu_desync_window=False` as a clean result.
5. **Recalibrate against the reported offset, not a calendar guess** — `us_eu_offset_delta_hours` gives the live US-minus-EU UTC offset (−5h aligned, −4h in either desync window) and `us_eu_overlap_hours` the resulting overlap. Re-derive cron triggers and strategy timers from these each session; do not schedule the shift by hard-coded date.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Hard-coding UTC offsets** — pinning US Eastern at −5 or the UK at +0 breaks for the ~8 months those zones sit at −4 (EDT) and +1 (BST). Resolve every offset from the IANA database at the instant it is needed.
- **Assuming a fixed two-week desync window** — the spring gap is 14 *or* 21 days depending on the year, and the autumn gap is always 7 days. Detect the window from the two zones' live DST states; never encode its length.
- **Assuming the US and EU transition at the same instant on their own transition day** — the EU switches simultaneously at 01:00 GMT everywhere, while each US zone switches at 02:00 of its own local time. A "both regions transition at 07:00 UTC" assumption is wrong on both sides.
- **Silently accepting a skipped or repeated local time** — constructing `datetime(..., tzinfo=ZoneInfo(...))` for 02:30 on a spring-forward date returns a timestamp for a wall time that never existed, and for 01:30 on a fall-back date returns the first of two valid answers, in both cases with no error. Check the flags or run `strict=True`.
- **Aggregating bars on local wall time across a transition** — the fall-back hour repeats, so local-time bucketing double-counts it, and the spring-forward hour is missing, so a gap looks like a data outage. Bucket on `utc_open_ns`.
- **Treating a DST-transition session's nominal clock span as its elapsed length** — a 01:00–09:00 local session is 7 hours on the spring-forward date and 9 hours on the fall-back date. Volume/VWAP denominators computed from the nominal span are wrong by ±12.5% on those days.
- **Assuming a desync audit that returns `False` actually ran** — before the leg-resolution fix, registering exchanges under MIC codes made the audit match nothing and report "no desync" on genuinely desynchronized dates. Always assert `us_exchange_id` and `eu_exchange_id` are populated.
- **Letting the tz database go stale** — DST rules are political and actively in flux. The EU's proposal to abolish seasonal changes has been stalled since 2018 and Directive 2000/84/EC remains in force; in the US, the Sunshine Protection Act passed the House on 14 July 2026 but has **not** been enacted, so 15 U.S.C. § 260a still governs. Pin the `tzdata` version for reproducible backtests, refresh it on a schedule for live trading, and re-verify both statuses before relying on them.

## Verification

- Register NYSE (`America/New_York`, 09:30–16:00) and LSE (`Europe/London`, 08:00–16:30). On **2026-03-15** the US is on EDT (−4) and the EU still on GMT (0): expect NYSE open `13:30:00Z`, LSE open `08:00:00Z`, `is_us_eu_desync_window=True`, and `us_eu_offset_delta_hours=-4.0`. On **2026-04-15** both are on summer time: expect `is_us_eu_desync_window=False` and `us_eu_offset_delta_hours=-5.0`. The NYSE/LSE UTC overlap moves from 2.0h aligned to 3.0h in the window — the one-hour shift this skill exists to catch.
- Confirm window lengths empirically rather than by assumption: count desynchronized days across March 2026 (expect **21**) and March 2027 (expect **14**), and confirm the autumn window from 2026-10-25 to 2026-11-01 is **7** days.
- Confirm the edge-case flags: a 02:30 open on 2026-03-08 sets `local_open_is_nonexistent`; a 01:30 open on 2026-11-01 sets `local_open_is_ambiguous` and resolves to `05:30:00Z`; a 01:00–09:00 session sets `dst_shift_inside_session` with `session_duration_hours` of 7.0 (2026-03-08) and 9.0 (2026-11-01).
- Run `python -m unittest discover -s skills/daylight-saving-time-transition-handling/scripts`.

## Related Skills

- `multi-timezone-session-scheduling`
- `global-exchange-holiday-calendar-handling`
- `cross-vendor-timestamp-precision-reconciliation`
