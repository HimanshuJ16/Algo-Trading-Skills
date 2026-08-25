# Pre-Flight Checklist

## Calendar feed
- [ ] Does the feed supply a release **time**, not just a date? (FRED gives `YYYY-MM-DD` only; a date placed at midnight blackouts the wrong part of the day.)
- [ ] Are vendor severity codes normalised through `normalize_impact_severity` at the feed boundary, with the exception left to propagate rather than defaulted to "not important"?
- [ ] Are all release timestamps epoch seconds UTC, produced by `parse_release_timestamp` or `release_timestamp_from_local` rather than by `datetime.fromisoformat(...).timestamp()` on a naive string?
- [ ] For fixed wall-clock releases (8:30 a.m. ET, 2:00 p.m. ET), is the local time resolved through an IANA zone so the UTC instant moves correctly across US and EU DST transitions — which fall on different dates?
- [ ] Does the calendar carry the events for the currencies you actually trade, and is `relevant_currencies` set deliberately rather than left at `None`?

## Fail-closed configuration
- [ ] Is `require_non_empty_calendar` left `True`, so a feed that never loaded blocks instead of permitting?
- [ ] Is `max_calendar_age_sec` set, and is `calendar_as_of_utc` actually refreshed on every successful fetch (a staleness tolerance with no as-of stamp blocks permanently)?
- [ ] Are `MACRO_CALENDAR_UNAVAILABLE` and `MACRO_CALENDAR_STALE` alerted on, not just logged? A gate stuck closed on a dead feed is an outage.
- [ ] Does the calendar loader use `replace_events`, so a malformed batch leaves the previous calendar intact?

## Blackout windows
- [ ] Are the 900 s / 900 s defaults calibrated for your instrument and venue, or knowingly accepted? No regulator or exchange sets these.
- [ ] Do events with a market-moving follow-on carry a `post_event_buffer_override_sec` that covers it — the FOMC press conference starts 30 minutes after the statement?
- [ ] Are `MEDIUM_IMPACT` buffers set deliberately, given they inherit the high-impact values unless configured?

## Caller integration
- [ ] Does the gate read `is_trading_permitted`, and nowhere read `is_blackout_active` as though it were its complement?
- [ ] Does the resume schedule come from `blackout_ends_at_utc` rather than from the reported event's release plus a locally computed buffer?
- [ ] Is `should_cancel_open_limit_orders` debounced, and is the cancel path idempotent, given the flag is `True` on every tick of the blackout?
- [ ] Is the full `MacroCalendarAuditReport` persisted for post-trade reconstruction, including `calendar_as_of_utc`?

## Surprise index
- [ ] Is `forecast_std_dev` the standard deviation of that indicator's **past surprises**, and is a `None` result handled as "no standardised surprise available" rather than coerced to a number?
- [ ] Is `higher_actual_is_positive_surprise=False` set for inverse indicators (unemployment rate, initial claims)?
- [ ] Is any `abs(S) > k` threshold applied only to `macro_surprise_index`, never to `macro_surprise_raw` (which is in the release's own units and not comparable across indicators)?
- [ ] In backtests, is the calendar's vintage appropriate for the decision time — the engine blocks look-ahead within a run but cannot detect a revised consensus or a rewritten schedule?
