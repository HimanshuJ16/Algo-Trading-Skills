# Deep Workflow Reference — multi-timezone-session-scheduling

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Store local wall time + IANA zone, never an offset**
   - `ExchangeSchedule(exchange_code, iana_timezone, open_time, close_time, ...)` holds naive
     `datetime.time` values plus a tz key. A tz-aware `time` object is rejected at registration,
     because attaching a zone to a bare time pins one fixed offset — the anti-pattern.
   - Validate the zone key eagerly. `ZoneInfo` resolves lazily, so an unresolvable key otherwise
     surfaces as a `ZoneInfoNotFoundError` from deep inside a scheduling call, at the worst moment.

2. **Resolve per date, never cache the offset**
   - `MultiTimezoneSessionScheduler.resolve_session(code, local_date)` returns a `ResolvedSession`
     with `open_utc`, `close_utc`, and `trading_windows_utc`, recomputed from the tz database for
     that date. `get_session_utc()` is the two-tuple convenience wrapper.
   - Nothing is memoised across dates. Two calls for dates on opposite sides of a transition must
     return instants an hour apart for the same local open time.

3. **Classify transition-day boundaries before trusting them**
   - A **skipped** wall time is detected by round-tripping through UTC: if `local → UTC → local`
     does not return the original wall time, the wall time never occurred.
   - A **repeated** wall time is detected by comparing `fold=0` and `fold=1` UTC offsets. Under
     PEP 495 a skipped time also yields two offsets, so ambiguity is the fold disagreement that
     remains once the skipped case is excluded — the two are mutually exclusive.
   - Default (`strict=False`): resolve with `fold=0`, emit a `logging.WARNING`, and name the
     boundary in `ResolvedSession.nonexistent_boundaries` / `.ambiguous_boundaries`.
   - `strict=True`: raise `SessionScheduleError`. Use this where a wrong-by-an-hour schedule is
     worse than a failed startup, e.g. an EOD reconciliation job.
   - Regular equity opens (09:30, 10:00) are not exposed to this, since transitions occur around
     02:00 local. Overnight sessions, early pre-market windows, and custom "run at 02:30" tasks are.

4. **Model intraday breaks**
   - `breaks=((time(11,30), time(12,30)),)` removes the halt from `trading_windows_utc`, so
     `get_market_status()` returns `BREAK` rather than `REGULAR_TRADING`. Breaks are validated to
     lie inside the session, be internally ordered, and not overlap.

5. **Half-open boundary semantics**
   - Every window is `[start, end)`. At exactly `close_utc` the state is `POST_MARKET` (if a
     post-market window is configured) or `MARKET_CLOSED`, never `REGULAR_TRADING`.
   - `pre_market_time` is the **start** of the pre-market window and must not be after `open_time`;
     `post_market_time` is the **end** of the post-market window and must not be before
     `close_time`. Both are validated at registration.

6. **Weekday evaluation in exchange-local time**
   - `get_market_status()` converts the query instant to exchange-local time and takes the weekday
     from the *local* date. Deriving it from the UTC date misclassifies exchanges far from UTC —
     Sydney's Monday open is Sunday 23:00 UTC.

7. **Overnight sessions**
   - `spans_midnight=True` resolves `close_time` on the following local date, and
     `get_market_status()` additionally evaluates the session anchored on the previous local date,
     so an instant after midnight is attributed to the session that is actually in progress.
   - `spans_midnight` cannot be combined with `breaks`, `pre_market_time`, or `post_market_time`;
     those are anchored to a single local date. Model an overnight session with intraday halts as
     separate `ExchangeSchedule` entries rather than accepting an ambiguous anchor.

8. **Cross-exchange sequential handoff**
   - `calculate_exchange_gap_minutes(a, b, local_date)` returns minutes from A's close to B's open;
     negative means the sessions overlap. `local_date` is each exchange's own local calendar date.
   - Recompute per date. Worked example: LSE close → NYSE open is **−180 minutes** on 2026-03-16
     (New York already on EDT, London still on GMT) but **−120 minutes** on 2026-04-15 (both on
     summer time). Any implementation returning a constant is caching an offset somewhere.

9. **Fail loudly on misconfiguration**
   - An unknown exchange code raises `SessionScheduleError` (a `ValueError` subclass) rather than
     returning `MARKET_CLOSED`, so a typo does not present as a quiet, permanently dormant bot.
   - A naive `query_time_utc` is rejected rather than assumed to be UTC.
   - The schedule registry is copied per scheduler instance, so `register_schedule()` cannot mutate
     `DEFAULT_EXCHANGE_SCHEDULES` or another component's registry.

## Failure Modes Observed in Production

- **Hardcoded UTC offsets** — "NYSE opens at UTC-5", producing a one-hour error for the ~8 months
  of the year the US is on EDT (UTC-4).
- **Static cron scheduling** — an EOD task pinned to a fixed UTC time, drifting an hour off true
  exchange-local time after each transition.
- **Asynchronous DST misalignment** — assuming a constant 5-hour London/New York differential, which
  is wrong for roughly four weeks a year.
- **Southern Hemisphere inversion** — applying Northern Hemisphere DST assumptions to Sydney or
  Johannesburg (the latter observes no DST at all, which fixed-offset code gets right by accident
  and rule-based code gets wrong on purpose).
- **Unmodelled lunch breaks** — orders sent into the TSE 11:30–12:30 JST halt because the scheduler
  stored only open and close and reported regular trading throughout.
- **Stale published hours** — TSE's close moved from 15:00 to 15:30 JST on 2024-11-05; a schedule
  constant written before that date silently truncates the last 30 minutes of every session.
- **Inclusive close comparison** — an order released at exactly the closing instant on the belief
  the session was still open.
- **Shared mutable registry** — one component registering a custom schedule and changing session
  times for every other component in the same process.

## Limitations

- No holiday or half-day calendar. A public holiday and an early close are both reported as normal
  sessions; compose with `global-exchange-holiday-calendar-handling` before gating orders.
- Weekday-only anchoring: a session whose *open* falls on a Saturday or Sunday is reported closed.
  This is wrong for weekend-opening futures venues — CME Globex opens 17:00 CT on Sunday — which
  need a weekly session model rather than a per-weekday one.
- No live halt awareness (LULD pauses, technical outages, ad hoc closures).
- No per-instrument hours; the model is exchange-level.
- Auction windows are not modelled as distinct states.

## Production Implementation Reference

- Reference code: `scripts/session_scheduler.py` — `MultiTimezoneSessionScheduler`,
  `ExchangeSchedule`, `ResolvedSession`, `MarketSessionState`, `SessionScheduleError`.
- Automated unit tests: `scripts/test_session_scheduler.py`.
