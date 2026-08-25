# Workflows for Global Macro Calendar Integration

## 1. Calendar ingestion and timestamp resolution

- Build one `MacroEconomicEvent` per scheduled release. `release_timestamp_utc` is
  **epoch seconds, UTC** — the engine performs no unit inference and rejects
  non-finite values.
- Normalise the vendor's severity through `normalize_impact_severity` at the feed
  boundary. It accepts the Trading Economics `1`/`2`/`3` encoding, the
  `"low"`/`"medium"`/`"high"` strings, and the `*_IMPACT` constants, and **raises**
  on anything else. Catching that exception and defaulting to `LOW_IMPACT` reopens
  the fail-open hole the normalisation exists to close.
- Resolve timestamps according to what the feed actually gives you:

  | Feed shape | Helper | Notes |
  |---|---|---|
  | ISO-8601 with `Z` or an explicit offset | `parse_release_timestamp` | The only accepted string form. |
  | ISO-8601 with no designator | — | **Rejected.** `datetime.fromisoformat(...).timestamp()` would resolve it in the host's local zone, so the same code yields different windows on a laptop and a UTC server. Decide what zone the vendor means, then use the appropriate helper. |
  | Local wall-clock time plus an IANA zone (`"2026-01-28T14:00:00"`, `"America/New_York"`) | `release_timestamp_from_local` | The correct path for "8:30 a.m. ET" and "2:00 p.m. ET" releases. Rejects offset-bearing strings, unknown zones, DST-ambiguous times (the repeated autumn hour) and non-existent times (the skipped spring hour). |
  | Date only (FRED) | — | Not usable as a blackout timestamp. Obtain the time of day from the issuing agency's schedule and resolve it with `release_timestamp_from_local`. |

- Load with `replace_events(events, as_of_utc=...)`. It validates the whole batch
  before swapping, so a single malformed row leaves the previous calendar intact
  rather than half-replacing it. `add_event` rejects a duplicate `event_id`;
  `remove_event` returns whether anything was removed.

## 2. Blackout window computation

Per event, the window is `[release − pre, release + post]`, inclusive at both ends,
where the buffers are resolved in this order:

1. `pre_event_buffer_override_sec` / `post_event_buffer_override_sec` on the event.
2. `medium_pre_event_buffer_sec` / `medium_post_event_buffer_sec` for
   `MEDIUM_IMPACT`, if configured.
3. The engine-level `pre_event_buffer_sec` / `post_event_buffer_sec`.

Only severities in `BLOCKING_SEVERITIES` (`HIGH_IMPACT`, `MEDIUM_IMPACT`) raise a
blackout; `LOW_IMPACT` events remain in the calendar and can still be reported as
`next_event`, but never close the gate.

**Widen the window where the print is not the whole event.** The FOMC statement is
released at 2:00 p.m. ET and the Chair's news conference starts at 2:30 p.m. ET, so
a 900 s post buffer reopens trading 15 minutes before the Chair begins speaking.
Set `post_event_buffer_override_sec` to cover the conference.

## 3. Per-tick audit

`audit_macro_trading_status(current_time_utc, relevant_currencies=None)` evaluates
in a fixed order, and the availability checks come **first**:

| Condition | `status` | `is_trading_permitted` | `is_blackout_active` |
|---|---|---|---|
| Calendar empty and `require_non_empty_calendar` | `MACRO_CALENDAR_UNAVAILABLE` | `False` | `False` |
| `calendar_as_of_utc` older than `max_calendar_age_sec` | `MACRO_CALENDAR_STALE` | `False` | `False` |
| One or more blocking windows cover the instant | `MACRO_BLACKOUT_ACTIVE` | `False` | `True` |
| Otherwise | `MACRO_TRADING_PERMITTED` | `True` | `False` |

The first two rows are why callers must gate on `is_trading_permitted`. It is the
single authoritative field; `is_blackout_active` answers a narrower question and is
`False` in exactly the failure states that matter most.

`relevant_currencies` restricts which events can block. It must be a sequence of
currency codes — a bare string is rejected rather than iterated character by
character, and an empty sequence is rejected rather than silently disabling the
gate.

## 4. Overlapping windows

The engine collects **every** window covering the current instant rather than
stopping at the first match:

- `active_blackout_events` — all of them, ordered by release timestamp.
- `active_blackout_event` — the **latest-closing** window: the one that actually
  governs when trading can resume.
- `blackout_started_at_utc` — the earliest start among the active windows.
- `blackout_ends_at_utc` — the governing end.

Resume from `blackout_ends_at_utc`. Deriving a resume time from the first matching
event's release plus your own buffer restarts inside a second window that is still
open.

## 5. Surprise computation (post-release only)

- `calculate_surprise_index(event)` returns
  $S = (\text{Actual} - \text{Consensus}) / \sigma$ rounded to 4 dp, where $\sigma$
  is `forecast_std_dev` — the standard deviation of that indicator's *past*
  surprises, not the dispersion of analyst estimates for this print unless that is
  what you calibrated.
- It returns `None` when the release has not happened yet, when `actual_release` or
  `consensus_forecast` is missing, and **when `forecast_std_dev` is missing**. It
  never substitutes `1.0`: an unstandardised difference labelled as a z-score makes
  every downstream `abs(S) > k` threshold meaningless.
- `raw_surprise(event)` gives $\text{Actual} - \text{Consensus}$ in the release's
  own units, under the same look-ahead guard.
- For inverse indicators — unemployment rate, initial jobless claims — set
  `higher_actual_is_positive_surprise=False`. A 3.4% print against a 3.2% consensus
  is a *negative* surprise for the economy even though the number is higher.
- Both functions refuse to read `actual_release` before `release_timestamp_utc`,
  regardless of whether the calendar row already carries a backfilled value.

In the permitted branch the report carries the most recent released event within
`surprise_lookback_sec` as `surprise_source_event`, with `macro_surprise_index` and
`macro_surprise_raw` alongside it, plus `next_event` and `seconds_to_next_event`
for the upcoming blocking release.

## 6. Acting on the report

- **Gate on `is_trading_permitted`.** Nothing else.
- **`should_cancel_open_limit_orders` is level-triggered**: it is `True` on every
  tick of the blackout, not once at the transition. Debounce it against your own
  last-cancel state and route it through an idempotent cancel path.
- **Persist the whole `MacroCalendarAuditReport`**, not just `audit_notes`. It
  carries the events the decision was made from, the window boundaries, and
  `calendar_as_of_utc` — which is what a later reconstruction of "why were we flat
  at 13:52?" actually needs.
- **Alert on `MACRO_CALENDAR_UNAVAILABLE` and `MACRO_CALENDAR_STALE`.** Blocking is
  the safe behaviour, but a gate stuck closed on a dead feed is an outage that will
  not announce itself any other way. The engine logs both at ERROR, an active
  blackout at WARNING, and the permitted branch at DEBUG so a per-tick risk loop is
  not flooded. Both unavailable states also set
  `should_cancel_open_limit_orders=True`: if the gate cannot be evaluated, resting
  orders should not be left exposed either.
