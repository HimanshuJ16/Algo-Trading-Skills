# Workflows for Instrument Identity Encoding

## 1. Define the target and its observability

Write both down before anything else:

- **Target value**: e.g. `close(t + 5d) / close(t) - 1`.
- **Observability time**: the earliest moment that value is knowable — here `t + 5d`.

`label_horizon` is the difference between the two. It is a property of the *label*, not
of the bar frequency, and the two are routinely different: minute bars with a daily
label need `pd.Timedelta(days=1)`, daily bars with a 20-day label need
`pd.Timedelta(days=20)`.

If the time column is an integer bar index rather than a timestamp, express the horizon
as an integer count of bars. The encoder subtracts the horizon from the time column
directly and raises `TypeError` if the two are not compatible, rather than silently
doing something else.

Leave `label_horizon=None` only for a genuinely one-step-ahead label. `None` means "the
label attached to a row is realized by the next timestamp in the panel".

## 2. Shape the panel

- One row per (instrument, timestamp). Multiple rows sharing a (symbol, timestamp) are
  handled — they all count toward that symbol's statistics — but they usually indicate a
  join that fanned out.
- Sorting is **not** required. The encoder sorts internally and returns rows in the
  caller's original order with the caller's index intact, so the encoded column can be
  paired with labels held in a parallel structure without re-aligning.
- Rows with a NaN target stay in the panel and still receive an encoding; they are
  excluded from the statistics, on the grounds that a missing label says nothing about
  the symbol. Infinite targets are rejected outright — they would poison every
  subsequent encoding through the running sum.
- Missing timestamps and missing symbols are rejected: neither row can be placed on the
  time axis or attributed to an instrument.

## 3. How the point-in-time cutoff is computed

For each row at time `T`, the encoder locates the newest history entry satisfying both
conditions from `references/standards.md` §2, using two binary searches over the sorted
unique timestamps and taking the stricter result:

```
realized            = last index with t <= T - label_horizon   (searchsorted, side="right")
not_contemporaneous = last index with t <  T                   (searchsorted, side="left")
position            = min(realized, not_contemporaneous)        # -1 when nothing qualifies
```

Cumulative sums and counts are precomputed once per unique timestamp — globally and per
symbol — so the lookup is a single array read per row rather than a re-aggregation. Cost
is `O(N log N)` overall, dominated by the group-by; there is no per-timestamp Python
loop over the panel, only a loop over distinct symbols.

`position == -1` means no realized history exists at all, which is the cold-start case:
`global_mean` becomes `cold_start_prior` and the symbol contributes nothing.

## 4. Apply the smoothing formula

```
global_mean = global_sum / global_count          (or cold_start_prior when count == 0)
encoded     = (local_sum + m * global_mean) / (local_count + m)
```

`m` is `smoothing_weight`. The equivalent shrinkage form and its provenance are in
`references/standards.md` §1. Three cases worth checking by hand on your own data:

| Situation | `local_count` | Result |
|---|---|---|
| First timestamp of the panel | 0 | `cold_start_prior` |
| Newly listed symbol, panel has history | 0 | the global mean |
| Symbol with exactly `m` observations | `m` | midpoint of its own mean and the global mean |

## 5. Backtest and live inference

```python
encoder = PointInTimeTargetEncoder(
    smoothing_weight=20.0,
    label_horizon=pd.Timedelta(days=5),
    cold_start_prior=0.0,
)

train_encoded = encoder.fit_transform(train_panel, "timestamp", "symbol", "fwd_ret_5d")
live_encoded  = encoder.transform(live_rows, "timestamp", "symbol")
```

`transform` reads no target column, so it works on live rows that have no label yet, and
it applies the same per-row cutoff — a live row at `T` sees exactly the history that was
realized at `T`. That is what makes the live feature identical to the one the model was
trained on; assert it explicitly (`SKILL.md` → Verification) rather than assuming it.

Do **not** re-run `fit_transform` on `pd.concat([train_panel, live_rows])` to encode live
rows. It happens to be safe with this implementation, but it re-fits on every call, gets
slower as the panel grows, and the habit is unsafe with any encoder that lacks a
per-row cutoff.

Refitting cadence is a separate decision: the fitted state is a running sum, so a
periodic refit on the extended panel is the normal pattern. See
`model-training-data-freshness-sla`.

## 6. Integrate with the estimator

- Exclude the raw symbol column from the feature matrix. Keeping it for debugging is
  fine; feeding an object-dtype column to XGBoost/LightGBM is not.
- Keep the encoded column's name traceable to its source column (`symbol_encoded` by
  default, overridable via `encoded_col`). The encoder refuses to overwrite an existing
  column of that name rather than silently replacing a caller's data.
- Record the encoder's configuration — `smoothing_weight`, `label_horizon`,
  `cold_start_prior` — alongside the model artefact. The encoding is part of the model;
  reproducing a prediction without it is not possible. See `model-versioning-and-rollback`
  and `reproducible-ml-training-pipelines`.
