---
name: multi-timezone-session-scheduling
description: >-
  Use when a bot's scheduling logic (session start/end, pre-market checks, EOD tasks) must operate correctly across time zones and daylight saving transitions, not just a single fixed local time
domain: algorithmic-trading
subdomain: data-management-global
tags: ["data-management-global", "iana-tz-database", "pytz-zoneinfo"]
brokers_frameworks: ["IANA tz database", "pytz/zoneinfo"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a bot's scheduling logic references "market open" or "market close" for any exchange, especially if the bot itself runs on infrastructure in a different time zone than the exchange it trades on, or trades on more than one exchange. A schedule hardcoded as a fixed UTC offset (e.g. "market opens at UTC+5:30") silently breaks twice a year for any exchange whose local time observes daylight saving time, and breaks permanently if the bot's own host time zone changes (common when migrating cloud regions) without a corresponding schedule update.

## Prerequisites

- Use of the IANA time zone database (via `zoneinfo` in modern Python, or `pytz`) for all exchange-local time representations, not fixed UTC offsets
- The trading host's own system clock and time zone configuration understood and controlled (ideally run the host in UTC and convert explicitly, rather than relying on host-local time matching any particular exchange)

## Workflow

1. Store and reason about exchange session times as IANA zone-aware times (e.g. `America/New_York`, `Europe/London`, `Asia/Kolkata`) rather than fixed UTC offsets — the IANA database encodes each region's actual DST transition rules and historical rule changes, which a fixed-offset constant cannot.
2. Run the bot's own host clock in UTC and perform all scheduling comparisons by converting the exchange's zone-aware session times to UTC at the moment of comparison, not by pre-computing a UTC offset once and reusing it — pre-computing the offset is exactly the pattern that breaks across a DST boundary.
3. Explicitly handle the two DST transition edge cases: the "spring forward" transition where a local time range (e.g. 2:00-3:00 AM) doesn't exist at all, and the "fall back" transition where a local time range occurs twice — scheduling logic that naively constructs a local datetime without DST-awareness can throw an error, silently pick the wrong occurrence, or schedule a task an hour off during these transition days specifically.
4. For a bot trading multiple exchanges whose local sessions overlap or are sequential (e.g., Asian session handoff to European handoff to US session), compute each exchange's open/close independently in UTC rather than assuming a fixed sequential gap between them, since the gap changes across DST boundaries when the two exchanges' regions don't transition on the same date (the US and EU, for example, do not shift DST on the same weekend).
5. Any "run daily at local time X" scheduled task (pre-market health checks, EOD reconciliation) should be scheduled against the exchange's zone-aware local time definition and recomputed each run, not fixed at deployment time — a cron-style scheduler configured with a fixed UTC time will systematically run an hour early or late relative to true exchange-local time for the portion of the year on the other side of a DST transition from when it was configured.
6. Test explicitly across a synthetic calendar that includes both hemispheres' DST transition dates if trading exchanges in both the Northern and Southern Hemisphere (which transition DST on different calendar dates, in opposite directions) — Southern Hemisphere DST rules are a commonly-missed edge case even in otherwise DST-aware scheduling code.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Hardcoding a fixed UTC offset for an exchange's local session time, which is correct for roughly half the year and silently wrong for the other half.
- Configuring a cron job or scheduler with a fixed UTC time intended to correspond to an exchange's local open, without recomputing that UTC time as the exchange's local DST status changes.
- Assuming the gap between two exchanges' sessions (e.g., "London opens 4.5 hours after Tokyo closes") is constant year-round, when the true gap fluctuates by an hour during the multi-week windows where the two regions' DST transitions don't align.
- Not testing Southern Hemisphere exchange scheduling (which transitions DST on essentially opposite dates to Northern Hemisphere exchanges) if the bot ever expands to trade e.g. ASX or JSE.
- Constructing a local datetime for a "spring forward" nonexistent hour or a "fall back" ambiguous hour without explicit handling, causing an exception or silently wrong scheduling on those specific days.

## Verification

- Run the scheduling logic against a test calendar covering at least one Northern Hemisphere and one Southern Hemisphere DST transition date and confirm computed session open/close times match the exchange's actual published local times for each.
- Confirm a "spring forward" and "fall back" transition day each produce a correctly-scheduled task time rather than an exception or an hour-off result.
- Confirm the bot's own host clock is running in UTC (or that all scheduling logic explicitly converts from host-local time) by checking system configuration, not just by assuming a cloud deployment defaults to UTC.

## Related Skills

- `global-exchange-holiday-calendar-handling`
- `forex-broker-integration-oanda-mt5`
- `systemd-supervision-for-trading-bots`
