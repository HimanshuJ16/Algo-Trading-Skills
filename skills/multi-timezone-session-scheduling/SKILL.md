---
name: multi-timezone-session-scheduling
description: >-
  Use when scheduling logic references market open or close for an exchange in another
  time zone, resolving local trading hours to UTC across daylight saving instead of a
  hardcoded offset. Pair it with a holiday calendar.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: data-management-global
  tags: data-management-global, iana-tz-database, pytz-zoneinfo
  brokers_frameworks: "IANA tz database; pytz/zoneinfo"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this whenever a bot's scheduling logic references "market open" or "market close" for any exchange, especially if the bot runs on infrastructure in a different time zone than the exchange it trades on, or trades on more than one exchange. A schedule hardcoded as a fixed UTC offset (e.g. "NYSE opens at UTC-5") is correct for roughly half the year and silently wrong for the other half, and breaks permanently if the bot's own host time zone changes — common when migrating cloud regions — without a corresponding schedule update.

## When NOT to Use

- **As a trading gate on its own.** The reference implementation is weekday-based and has no holiday or half-day calendar, so a public holiday and an early close are both reported as normal sessions. Compose it with `global-exchange-holiday-calendar-handling` before letting it authorise an order.
- **For weekend-opening venues.** Sessions are anchored to weekdays, so a Sunday-evening futures open (CME Globex, 17:00 CT Sunday) is reported closed. Those need a weekly session model.
- **As a substitute for a live exchange status feed.** A schedule says what was *planned*; it does not know about an unscheduled halt, a LULD pause, or an ad hoc closure.
- **For DST forensics.** If the task is enumerating US/EU desynchronisation windows or auditing transition-day timestamps at nanosecond resolution, use `daylight-saving-time-transition-handling`; this skill only *flags* a session boundary landing on a transition.
- **For per-instrument hours.** Exchange-level schedules do not model instrument-level variation (different closes per product, expiry-day sessions, auction-only names).

## Prerequisites

- IANA time zone database access via `zoneinfo` (Python 3.9+) or `pytz`, for all exchange-local time representation. On hosts with no system tz database — notably Windows — the `tzdata` package must be installed, or every `ZoneInfo` lookup fails at runtime.
- The trading host's own clock and time zone understood and controlled. Run the host in UTC and convert explicitly rather than relying on host-local time happening to match an exchange.
- A source of truth for each exchange's published local session times, including intraday breaks. Exchange hours change: the Tokyo Stock Exchange moved its close from 15:00 to 15:30 JST on 2024-11-05.

## Workflow

1. Store exchange session times as **local wall times plus an IANA zone key** (`America/New_York`, `Asia/Tokyo`), never as a UTC offset. The tz database encodes each region's actual transition rules and their historical amendments; a fixed-offset constant encodes one arbitrary moment's answer.
2. Convert to UTC **at the moment of comparison, for the specific date being scheduled**. Pre-computing an offset once at startup and reusing it is exactly the pattern that breaks across a DST boundary — and it breaks silently, because the wrong answer is still a plausible timestamp.
3. Before trusting a boundary, ask whether the local wall time **exists exactly once on that date**. On a "spring forward" day a local range (typically 02:00–03:00) does not occur, and on a "fall back" day a range occurs twice. Python's `fold=0` default will return *an* answer for both — the pre-transition offset for a skipped time, the first occurrence for a repeated one — so the failure is a silently wrong instant, not an exception. Detect the case (round-trip the local time through UTC to catch a skipped time; compare `fold=0` against `fold=1` offsets to catch a repeated one), then either fail loudly or record that the instant was resolved by convention.
4. Model **intraday breaks explicitly**. An exchange with a lunch break — TSE halts 11:30–12:30 JST, HKEX 12:00–13:00 HKT — is not matching orders during it. A scheduler that only stores open and close reports REGULAR_TRADING for an hour a day when nothing can fill, which turns into unexplained rejected or resting orders rather than an obvious error.
5. Decide session-boundary semantics and document them. Treat windows as **half-open `[open, close)`**: at exactly the closing instant the exchange is no longer in continuous trading, and reporting REGULAR_TRADING there invites an order sent into the closing auction or rejected outright.
6. For multi-exchange handoffs, compute each exchange's open/close **independently in UTC on the query date** rather than assuming a fixed sequential gap. The US and EU do not shift DST on the same weekend — the US moves on the 2nd Sunday of March and 1st Sunday of November (local time), the EU on the last Sundays of March and October (01:00 GMT) — so the transatlantic gap changes by an hour for two multi-week windows a year.
7. Schedule "run daily at local time X" tasks (pre-market health checks, EOD reconciliation) against the **zone-aware local definition, recomputed each run**. A cron entry fixed at a UTC time will run an hour early or late for the part of the year on the other side of a DST transition from when it was configured.
8. Fail loudly on a **misconfigured exchange code or a naive timestamp**. Returning MARKET_CLOSED for an unrecognised code turns a typo into a bot that never trades and never explains why; accepting a naive datetime as "probably UTC" is the same guess this skill exists to eliminate.
9. Test across a synthetic calendar covering **both hemispheres**. Southern Hemisphere exchanges transition on roughly opposite calendar dates in the opposite direction (Sydney is UTC+11 in January and UTC+10 in July), and are the most commonly missed case in otherwise DST-aware code.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Hardcoding a fixed UTC offset for an exchange's session, which is correct for roughly half the year and silently wrong for the other half.
- Configuring a cron job at a fixed UTC time intended to correspond to an exchange's local open, without recomputing it as the exchange's DST status changes.
- Assuming the gap between two exchanges' sessions ("London opens 4.5 hours after Tokyo closes") is constant year-round, when it moves by an hour during the multi-week windows where the two regions' DST transitions do not align.
- Applying Northern Hemisphere DST assumptions to a Southern Hemisphere exchange, e.g. treating Sydney as a constant UTC+10.
- Constructing a local datetime on a "spring forward" or "fall back" day without checking existence or ambiguity — Python returns a plausible-looking timestamp under `fold=0` rather than raising, so the bug ships.
- Storing only open and close for an exchange that halts intraday, so the lunch break is reported as regular trading.
- Treating the closing instant as still-open by using an inclusive `close` comparison.
- Returning "closed" instead of raising when an exchange code is unknown, converting a configuration typo into a silently dormant bot.
- Passing `datetime.utcnow()` (naive) into a scheduler that assumes naive means UTC — correct until someone passes a naive *local* time, at which point the error is a whole UTC offset.
- Mutating a shared module-level default schedule registry from one component, changing session times for every other component in the process.
- Deriving the weekday from the UTC timestamp rather than the exchange-local date, which misclassifies the weekend edges for exchanges far from UTC (Sydney's Monday open is Sunday in UTC).

## Verification

- Run the scheduling logic against a calendar covering at least one Northern and one Southern Hemisphere DST transition, and confirm the computed local open/close matches the exchange's published local times on each side of the transition.
- Confirm a "spring forward" and a "fall back" boundary each produce either an explicit failure or a flagged, documented resolution — never an unannounced hour-off result.
- Confirm the transatlantic gap between a US and an EU exchange is *different* inside and outside the March and October desynchronisation windows. If it is constant, an offset is being cached somewhere.
- Confirm an exchange with an intraday break reports a non-trading state during it, and that the instant of close is not reported as regular trading.
- Confirm an unknown exchange code and a naive timestamp both raise rather than returning a session state.
- Confirm the host clock is running in UTC (or that all logic converts from host-local explicitly) by checking system configuration, not by assuming the cloud image defaults to UTC.
- Run `python -m unittest discover -s skills/multi-timezone-session-scheduling/scripts`.

## Related Skills

- `daylight-saving-time-transition-handling`
- `global-exchange-holiday-calendar-handling`
- `forex-broker-integration-oanda-mt5`
- `systemd-supervision-for-trading-bots`
