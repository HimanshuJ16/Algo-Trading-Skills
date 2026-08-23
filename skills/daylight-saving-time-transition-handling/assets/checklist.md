# Pre-Flight Checklist

## Storage and conversion

- [ ] Are all system timestamps, database columns, and log records in UTC (nanosecond epochs), with local time used only for display?
- [ ] Are IANA zone strings (`America/New_York`, `Europe/London`) used everywhere, with no fixed UTC offset constants anywhere in the schedule path?
- [ ] Is the offset resolved fresh at each session boundary, rather than one offset cached per day?
- [ ] Is session length derived from the UTC epochs, not from the nominal local-clock span?

## Transition edge cases

- [ ] Is a session boundary that falls in the "spring forward" gap detected (`local_open_is_nonexistent`) rather than silently resolved?
- [ ] Is a repeated "fall back" boundary detected (`local_open_is_ambiguous`), and does the chosen occurrence (`fold=0` by default) match the venue's intent?
- [ ] Is `strict=True` enabled for unattended schedulers, so a guessed timestamp raises instead of propagating?
- [ ] Is `dst_shift_inside_session` checked before aggregating bars or computing VWAP denominators for that date?
- [ ] Are overnight sessions registered with `spans_midnight=True` rather than producing a negative duration?

## Cross-border desynchronization

- [ ] Are the March and October/November US-EU desync windows audited daily while they are open?
- [ ] Are `us_exchange_id` and `eu_exchange_id` asserted non-`None` on the report, so a `False` result cannot mean "the audit never ran"?
- [ ] Are cron triggers and execution timers recalibrated from `us_eu_offset_delta_hours` and the recomputed UTC epochs, rather than from hard-coded dates?
- [ ] Is the code free of any fixed "two-week window" assumption? (Spring is 14 **or 21** days; autumn is always 7.)

## Time zone data

- [ ] Is the `tzdata` package installed on Windows hosts and slim containers (where `zoneinfo` has no system database)?
- [ ] Is the `tzdata` version pinned for backtest reproducibility and refreshed on a schedule for live trading?
- [ ] Has the current statutory status been re-verified (US Sunshine Protection Act; EU Directive 2000/84/EC) before relying on the documented rules?
