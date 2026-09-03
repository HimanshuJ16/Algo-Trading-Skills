---
name: global-macro-economic-calendar-integration
description: >-
  Use when a live system must stop quoting around scheduled macro releases such as FOMC,
  CPI and payrolls, resolving release timestamps across daylight saving and resuming
  only when the window has genuinely closed.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: data-management-global
  tags: macro-calendar, economic-events, fomc, cpi, nfp, trading-blackout, surprise-index, news-filter
  brokers_frameworks: "Trading Economics API; FRED API; Bloomberg Data; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a live trading system must stop quoting or sending new orders around scheduled macroeconomic releases — FOMC rate decisions, US CPI, Non-Farm Payrolls, ECB decisions — and must resume only when the release window has genuinely closed. The engine holds a calendar of `MacroEconomicEvent` records, answers a single question per tick (`audit_macro_trading_status`) with a structured `MacroCalendarAuditReport`, and computes the standardised surprise $S = (\text{Actual} - \text{Consensus}) / \sigma$ **only after** the release timestamp has passed.

Two properties define it: it **fails closed** (an empty, stale, or unparseable calendar blocks trading rather than permitting it) and it **does not look ahead** (`actual_release` is unreadable before `release_timestamp_utc`, even if the field is populated in the calendar row).

## When NOT to Use

- **As a news or headline filter.** This engine handles *scheduled* releases only. Unscheduled events — an intermeeting cut, a geopolitical headline, an exchange halt — have no calendar entry and will not raise a blackout. Pair it with a separate kill switch; see `kill-switch-and-drawdown-circuit-breakers`.
- **With a feed that gives only a release *date*.** FRED's release calendar publishes dates, not the 8:30 a.m. ET instant. A date-only row placed at midnight produces a blackout window at the wrong time of day. Use a feed that carries a time, or resolve the wall-clock time yourself via `release_timestamp_from_local`.
- **As the cancel mechanism.** `should_cancel_open_limit_orders` is a *level-triggered* flag: it is `True` on every tick for the duration of the blackout, not once at the edge. Wiring it directly to a cancel call issues a cancel per tick. Debounce it, and make the cancel path idempotent — see `broker-api-idempotent-cancel-requests`.
- **As a backtest event filter without re-checking the calendar's vintage.** Consensus forecasts and even release schedules are revised. The engine enforces no look-ahead *within* a run, but it cannot tell whether the calendar you loaded is the one that existed at the historical decision time.
- **To decide whether an exchange is open.** Blackouts and session/holiday calendars are separate concerns; see `global-exchange-holiday-calendar-handling` and `multi-timezone-session-scheduling`.

## Prerequisites

- A macro calendar feed supplying, per event: `event_id` (unique), `event_name`, `currency`, `release_timestamp_utc` (**epoch seconds, UTC**), and `impact_severity`. Vendor severity codes (`1`/`2`/`3`, `"low"`/`"medium"`/`"high"`) go through `normalize_impact_severity`, which **raises** on anything it does not recognise rather than defaulting to "not important".
- ISO-8601 strings must be converted with `parse_release_timestamp` (rejects naive strings) or `release_timestamp_from_local(local_iso, iana_timezone)` (rejects offset-bearing strings, unknown zones, and DST-ambiguous or non-existent local times). `zoneinfo` is stdlib; no new dependency.
- For a standardised surprise you additionally need `consensus_forecast`, `actual_release`, and `forecast_std_dev` — the standard deviation of that indicator's *past* surprises. Without `forecast_std_dev` the engine returns `None`, not an unstandardised number.
- Blackout buffers, chosen by you. The 900 s / 900 s defaults are a placeholder, not a calibrated or mandated value; see `references/standards.md`.

## Workflow

1. **Load and stamp the calendar.** `replace_events(events, as_of_utc=...)` validates every event before swapping, so a bad row leaves the previous calendar intact. Set `max_calendar_age_sec` if the feed can go silent: past that age the status becomes `MACRO_CALENDAR_STALE` and trading is blocked.
2. **Audit each tick.** `audit_macro_trading_status(current_time_utc, relevant_currencies=None)` runs the availability check first (empty or stale calendar → blocked), then collects **every** window covering the current instant.
3. **Gate on `is_trading_permitted`, never on `is_blackout_active`.** They are not complements: an unavailable calendar sets `is_trading_permitted=False` while `is_blackout_active` stays `False`. Code written as `if not report.is_blackout_active:` trades through exactly the failure it was meant to catch.
4. **Resume from `blackout_ends_at_utc`, not from the reported event.** When windows overlap, the latest-closing window governs; `active_blackout_event` names it, `active_blackout_events` lists all of them, and `blackout_ends_at_utc` is the instant trading can resume.
5. **Read the surprise after the fact.** In the permitted branch the report carries `macro_surprise_index` (standardised) and `macro_surprise_raw` (`Actual − Forecast`) for the most recent event released within `surprise_lookback_sec` (default 86 400 s), plus `surprise_source_event`. For inverse indicators — unemployment rate, jobless claims — set `higher_actual_is_positive_surprise=False` so the sign reflects economic direction.
6. **Widen the window where the release is not the whole event.** Per-event `pre_event_buffer_override_sec` / `post_event_buffer_override_sec` override the severity defaults. The FOMC statement lands at 2:00 p.m. ET but the press conference begins at 2:30 p.m. ET; a 15-minute post buffer reopens trading 15 minutes before the Chair starts speaking.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Fail-open on an unrecognised severity code.** A feed that emits `3` or `"HIGH"` where the engine expects `HIGH_IMPACT` silently means "no blackout" if the comparison is a plain string equality. Normalise at ingestion and raise on unknown codes — a severity you cannot classify is not a severity you can ignore.
- **Fail-open on an empty calendar.** Zero events is indistinguishable from a feed that never loaded. It is also a real state: in the autumn 2025 US federal shutdown, scheduled BLS releases were cancelled outright. `require_non_empty_calendar=True` (the default) blocks instead of permitting.
- **Substituting 1.0 for a missing standard deviation.** That returns `Actual − Forecast` in the indicator's own units while labelling it a z-score. A 70 000-job NFP miss becomes "70 sigma"; any `abs(S) > 2` threshold downstream is then meaningless. The engine returns `None`.
- **Reading `actual_release` before the release.** Calendar rows often carry the actual value once backfilled. Reading it at any time before `release_timestamp_utc` is textbook look-ahead in a backtest and nonsense live.
- **Naive ISO timestamps.** `datetime.fromisoformat("2026-01-28T14:00:00").timestamp()` resolves in the *host's* local zone — the same code produces different blackout windows on a laptop and a UTC server. Trading Economics serialises UTC without a designator, so this is a live hazard, not a hypothetical.
- **Fixed wall-clock releases across DST.** 8:30 a.m. ET is a moving UTC instant. US and EU transition on different dates (US: second Sunday of March to first Sunday of November; EU: last Sunday of March to last Sunday of October), so the US–EU offset changes twice a year for a few weeks. Resolve local wall-clock times through `release_timestamp_from_local`, which also rejects the ambiguous and non-existent local times DST creates.
- **Treating the statement as the whole event.** FOMC press conferences, ECB press conferences, and revisions in the following release all move markets after the headline print.
- **Ignoring currency scope.** `relevant_currencies` filters which events can block. Omit it and every currency's calendar gates every book; pass a bare string instead of a sequence and the engine rejects it rather than iterating characters.

## Verification

- Register an FOMC event at 14:00 UTC with `HIGH_IMPACT` and the default 900 s buffers. At 13:50 UTC: `status == 'MACRO_BLACKOUT_ACTIVE'`, `is_trading_permitted is False`, `should_cancel_open_limit_orders is True`, `blackout_ends_at_utc` equals release + 900. At 15:00 UTC: `status == 'MACRO_TRADING_PERMITTED'`; with `consensus_forecast=5.25`, `actual_release=5.50`, `forecast_std_dev=0.10`, `macro_surprise_index == 2.5` and `macro_surprise_raw == 0.25`. With `forecast_std_dev=None`, `macro_surprise_index is None` while `macro_surprise_raw` is still `0.25`.
- Fail-closed regression: an empty calendar, a calendar older than `max_calendar_age_sec`, and an event whose severity code is unrecognised must each leave `is_trading_permitted is False` (the first two via `MACRO_CALENDAR_UNAVAILABLE` / `MACRO_CALENDAR_STALE`, the third by raising at construction).
- Look-ahead regression: an event with `actual_release` populated but `release_timestamp_utc` in the future must return `None` from both `calculate_surprise_index` and `raw_surprise`.
- Negative checks: naive ISO strings, offset-bearing strings passed to `release_timestamp_from_local`, DST-ambiguous and non-existent local times, NaN/Inf timestamps, non-positive `forecast_std_dev`, negative buffers, and duplicate `event_id` must each raise `ValueError`.
- Run `python -m unittest discover -s skills/global-macro-economic-calendar-integration/scripts` and confirm a 100% pass rate.

## Related Skills

- `central-bank-communication-nlp-analysis`
- `global-exchange-holiday-calendar-handling`
- `deployment-freeze-windows-around-market-events`
- `daylight-saving-time-transition-handling`
- `multi-timezone-session-scheduling`
- `kill-switch-and-drawdown-circuit-breakers`
- `broker-api-idempotent-cancel-requests`
