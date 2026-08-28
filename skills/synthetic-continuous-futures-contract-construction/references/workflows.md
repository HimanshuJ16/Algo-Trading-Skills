# Workflows — synthetic-continuous-futures-contract-construction

## 1. Assemble the per-contract histories

Ingest one OHLCV frame per expiration, keyed by contract symbol. Before splicing:

- Normalise every index to **one label type** (all `str`, or all `Timestamp`). A mixed
  set cannot be sorted into a single session timeline and is rejected.
- Enforce **one bar per contract per session**. Duplicate labels make the close for a
  session ambiguous.
- Confirm the frames carry `close`, plus `volume` for `VOLUME_CROSSOVER` or
  `open_interest` for `OPEN_INTEREST_CROSSOVER`. Include `open`, `high`, `low` if the
  backtest touches intrabar levels — stop and target testing on a close-only series
  silently assumes the bar had no range.

## 2. Establish the contract order

Two mutually exclusive paths:

- **Symbol decoding** (default): `<root><month code><two-digit year>`, e.g. `ESZ24`.
  Month codes are the CME set (F…Z = January…December). A single-digit year code is
  rejected — `ESZ4` is 2014 or 2024, and guessing wrong reorders a decade of data.
- **Explicit `contract_expiries`**: `{'ESH24': '2024-03-15', ...}`. Authoritative
  whenever supplied, and required for non-standard symbols, pre-2000 history, and
  `DAYS_BEFORE_EXPIRY`.

Never order by `sorted()` on the raw symbols. Alphabetical ordering places `ESH25`
before `ESZ24`, making the deferred contract the front month for the whole run.

## 3. Evaluate the roll trigger on completed sessions only

For each session *t* in the merged timeline, the trigger is evaluated against session
*t−1*'s data and, if it fires, the switch takes effect at *t*:

| Method | Condition on the reference session | Requires |
|---|---|---|
| `VOLUME_CROSSOVER` | $V_{\text{next}} > V_{\text{front}}$ | `volume` on both contracts |
| `OPEN_INTEREST_CROSSOVER` | $OI_{\text{next}} > OI_{\text{front}}$ | `open_interest` on both contracts |
| `DAYS_BEFORE_EXPIRY` | front expiry − session ≤ `days_before_expiry` (calendar days) | `contract_expiries` |

Both contracts must have a bar on the reference session; otherwise the trigger is
recorded as unevaluable rather than as a negative, and the confirmation streak resets.
A NaN or infinite trigger value is treated the same way — a missing number must never
read as "no crossover".

With `min_confirmation_sessions > 1`, the crossover must hold on that many consecutive
reference sessions. Any non-firing or unevaluable session resets the streak.

## 4. Measure the gap on the roll-from session

On the reference session *t*, with both closes observed simultaneously:

```
gap   = close(back, t) - close(front, t)
ratio = close(back, t) / close(front, t)
```

Positive gap in contango, negative in backwardation. A non-finite close on either
side aborts with an error: an unmeasurable gap would otherwise silently become zero
and leave the discontinuity in the series.

## 5. Apply the adjustment backwards

With rolls indexed oldest-first and segment *j* being the bars priced off contract *j*:

```
offset[N] = 0.0                       factor[N] = 1.0        # newest segment
offset[j] = offset[j+1] + gap[j]      factor[j] = factor[j+1] * ratio[j]
```

Then, per bar, `adjusted = raw + offset[segment]` (additive) or
`adjusted = raw * factor[segment]` (proportional). `open`, `high` and `low` take the
same treatment as `close`; volume and open interest are never adjusted.

The newest segment keeps real market prices, which is what makes the final bar
comparable to a live quote. Proportional adjustment is refused when any roll involves
a close at or below zero.

## 6. Serialise and audit

The output frame carries `active_contract`, `segment_id`, `is_roll_session`, the
`raw_*` and `adjusted_*` price columns, `adjustment_offset` / `adjustment_factor`, and
the passthrough `volume` / `open_interest`.

Check before use:

- `total_roll_events` against the number of expirations the period should have crossed.
  Far fewer means the trigger is not firing; far more means an ordering problem.
- `sessions_without_active_bar` — sessions dropped because the active contract had no
  bar. Non-zero on a liquid product is a data hole.
- `unevaluable_trigger_sessions` — sessions where the trigger could not be assessed.
- The adjusted series against the raw series **at the end**: the final bar must match.

Store `roll_events` alongside the series. Without the roll schedule, the history
cannot be reproduced after the next roll restates it.
