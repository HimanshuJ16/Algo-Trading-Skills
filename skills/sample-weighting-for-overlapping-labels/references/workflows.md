# Workflows for Sample Weighting for Overlapping Labels

Reference procedure for `SampleWeightingForOverlappingLabelsEngine`. Formulas and
snippet numbers are documented in `references/standards.md`.

## 1. Assemble label spans

Build one `LabelSpan` per training row, straight from the labeller's output:

```python
from overlapping_sample_weighter import (
    LabelSpan, SampleWeightingForOverlappingLabelsEngine, WeightingMethod,
)

spans = [
    LabelSpan(sample_id=row.event_id,
              start_time_idx=bar_index[row.t0],      # inclusive
              end_time_idx=bar_index[row.t1],        # inclusive
              realized_return=row.ret)
    for row in triple_barrier_events.itertuples()
]
```

- Indices are **bar ordinals**, both endpoints inclusive. Convert timestamps with
  `bar_index = {ts: i for i, ts in enumerate(price_index)}` — do not pass epoch
  seconds, which would allocate one concurrency entry per second covered.
- A label that resolves on the bar that opened it is `[t, t]`.
- Rows whose vertical barrier ran past the end of the sample must be truncated to
  the last available bar by the labeller, not left open.
- `sample_id` must be unique; it is the join key for the resulting weights.

## 2. Concurrency and uniqueness

```python
engine = SampleWeightingForOverlappingLabelsEngine()
concurrency = engine.compute_concurrency(spans)
uniqueness = engine.compute_sample_uniqueness(spans, concurrency)
```

Inspect before weighting:

- `max(concurrency.values())` — the peak number of labels sharing one bar. A peak
  equal to the holding period is normal for labels generated on every bar; a peak
  far above it usually means duplicated events.
- `sum(uniqueness) / len(uniqueness)` — the dataset average $\bar{u}$. Treat
  $\bar{u}N$ as the effective independent sample size when judging whether any
  downstream statistic is meaningful.
- Bars absent from `concurrency` are bars no label covers. A large gap usually
  means the labeller dropped events (halts, missing data), which is worth
  resolving before training rather than weighting around.

## 3. Weight assignment

```python
report = engine.compute_sample_weights(spans, WeightingMethod.UNIQUENESS_ONLY)
```

**Return attribution — exact form.** Supply per-bar log returns covering every bar
of every span:

```python
import math
log_ret = {i: math.log(close[i] / close[i - 1]) for i in range(1, len(close))}
report = engine.compute_sample_weights(
    spans, WeightingMethod.RETURN_ATTRIBUTED, bar_log_returns=log_ret
)
assert report.return_attribution_is_exact
```

Snippet 4.10 sums over the closed interval `[t0, t1]`, which includes the return
realised *into* the opening bar; this engine reproduces that convention. Note that
bar 0 has no log return: either start spans at bar 1 or extend the mapping.
Omitting `bar_log_returns` is allowed and falls back to $u_i |r_i|$, with
`return_attribution_is_exact = False` and an `APPROXIMATION` note in the audit
string — check the flag rather than assuming.

**Time decay.**

```python
engine = SampleWeightingForOverlappingLabelsEngine(time_decay_last_weight=0.0)
report = engine.compute_sample_weights(spans, WeightingMethod.TIME_DECAY)
```

`time_decay_last_weight` is the book's `clfLastW`: `1.0` no decay, `0.5` the oldest
label retains half the newest label's factor, `0.0` decays linearly to zero,
`-0.5` zeroes the oldest half of *cumulative uniqueness*. Values outside
$(-1, 1]$ raise. Spans are sorted internally, so the caller's list order is
irrelevant.

To decay return-attribution weights — the book's full composition — multiply the
factors in yourself and re-normalise:

```python
factors = engine.compute_time_decay_factors(spans, uniqueness)
ra = engine.compute_sample_weights(spans, WeightingMethod.RETURN_ATTRIBUTED, log_ret)
raw = [r.raw_weight * f for r, f in zip(ra.sample_results, factors)]
total = sum(raw)
weights = [w * len(raw) / total for w in raw]   # restore sum(w) == N
```

## 4. Hand off to the estimator

```python
weight_by_id = {r.sample_id: r.normalized_weight for r in report.sample_results}
w = X.index.map(weight_by_id).to_numpy()        # join by sample_id, never by position
assert not any(v is None for v in w)
model.fit(X, y, sample_weight=w)
score = metric(y_test, model.predict(X_test), sample_weight=w_test)
```

- Join by `sample_id`. Joining by position silently mis-assigns every weight if
  any row was filtered between labelling and training.
- Weight the **scoring** call too. A weighted fit evaluated with an unweighted
  metric puts the redundancy straight back into the number you act on.
- Set a bagged learner's `max_samples` to `report.average_dataset_uniqueness`
  (AFML §4.4); weights alone do not de-duplicate bootstrap draws.
- If `report.degenerate_uniform_fallback` is `True`, stop: every raw weight was
  zero and the uniform vector is a placeholder, not a result.

## 5. Combine with fold-level leakage control

Sample weights do not purge anything. Run the weighted training set through
purged, embargoed cross-validation
(`hyperparameter-tuning-without-target-leakage`,
`walk-forward-validation-setup`).

On *where* to compute the weights: the book computes them once over the full
sample. Recomputing them **inside each training fold** is the stricter option,
because a globally computed $c_t$ reflects which labels exist after the fold's
last bar — the event schedule, not the outcomes, but still information the fold
has not reached. Per-fold recomputation is cheap here (the engine is O(sum of
span lengths)) and is the conservative default; if you compute once globally
instead, say so in the model card.
