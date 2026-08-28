# Workflows for Synthetic Labels from the Triple Barrier Method

Reference procedure for `TripleBarrierLabelerEngine`. Formulas, snippet numbers and
the measured class-balance table are in `references/standards.md`.

## 1. Prepare the price series

```python
import pandas as pd
from triple_barrier_labeler import TripleBarrierLabelerEngine, TripleBarrierError

closes = bars["close"]                      # pandas Series, one bar frequency
closes = closes.sort_index()                # index must be monotonic and unique
```

- Strictly positive, finite closes. The engine raises on NaN, `inf`, zero and
  negative prices rather than labelling around them: a NaN close fails both barrier
  comparisons and would otherwise be recorded as an untouched bar.
- A **unique, monotonically increasing** index. Barrier scanning is positional; an
  out-of-order series labels each event against the wrong future and still returns a
  plausible frame.
- One frequency throughout. `vertical_bars` counts bars, and $\sigma_t$ is per bar;
  mixing daily and intraday rows makes both meaningless.
- Leave at least `volatility_span` bars of history ahead of the first event you care
  about. Warm-up events are dropped, not defaulted.

## 2. Choose the barrier geometry

```python
engine = TripleBarrierLabelerEngine(
    pt_mult=2.0,            # profit-taking width, in units of sigma_t
    sl_mult=2.0,            # stop-loss width; keep equal to pt_mult when side is None
    vertical_bars=10,       # holding horizon, in bars
    volatility_span=20,     # EWM span for sigma_t
    min_target_return=0.0,  # AFML minRet: sigma_t must strictly exceed this
)
```

- Set `vertical_bars` to the holding period the strategy will actually run, not to a
  round number. The label is a claim about that horizon and nothing else.
- Keep the multipliers equal unless you are passing a `side`. Unequal barriers with
  no direction manufacture a class skew — the table in `references/standards.md`
  shows 58.9% / 34.3% on a series with no drift at all.
- If the vertical barrier almost never fires, the barriers are too narrow for the
  horizon. Widen the multipliers, shorten the horizon, or set
  `scale_target_by_horizon=True` and re-tune the multipliers downward (near 0.75
  rather than 2.0).

## 3. Generate directional labels

```python
labels = engine.generate_labels(closes)
labels["barrier_touched"].value_counts(normalize=True)
```

Every row carries `entry_timestamp`, `entry_price`, `exit_timestamp`, `exit_price`,
`barrier_touched` (+1/0/−1), `realized_return`, `target_volatility`, `side`,
`holding_bars`, `meta_label` and `intrabar_ambiguous`.

Read the class balance before anything else. A distribution that looks like a strong
directional view on a liquid, broad instrument usually means the barrier geometry is
lopsided, not that the market is.

## 4. Seed on filtered events instead of every bar

```python
events = cusum_filter(closes, threshold)          # index labels, not positions
labels = engine.generate_labels(closes, events=events)
```

- `events` are **index labels** from `closes.index`. An unknown label raises: v1.0.0
  dropped them, so a filter producing 400 events could yield 260 rows in silence.
- Events within `vertical_bars` of the end of the series are dropped, with the count
  logged at WARNING. Compare `len(labels)` against `len(events)` and account for the
  difference before training.
- Filtering also thins the overlap between labels, which is the other reason to do
  it — but it does not remove the overlap. Step 7 still applies.

## 5. Scan intrabar when the strategy has a real stop

```python
labels = engine.generate_labels(
    closes, highs=bars["high"], lows=bars["low"],
)
ambiguous = labels["intrabar_ambiguous"].sum()
```

- `highs` and `lows` must be supplied together, aligned bar-for-bar with `closes`,
  and must bracket each close. Swapped arguments and out-of-range closes raise.
- A bar whose low breaches the stop and whose close recovers labels `0` close-only
  and `−1` here. That difference is the point: the close-only label teaches the model
  that a position it would have been stopped out of was fine.
- A bar spanning both barriers is resolved to the **stop-loss** and flagged. Count
  those rows. If they are more than a small fraction of the set, the bars are too
  coarse for the barrier widths — go to finer bars rather than trusting the tie-break.
- The exit price in this mode is the barrier level, not the bar extreme.

## 6. Meta-label a primary model's signal

```python
side = primary_model_signal.reindex(closes.index)   # +1 / -1, aligned to closes
labels = engine.generate_labels(closes, side=side)
y_meta = labels["meta_label"]                       # 1 = take the bet, 0 = pass
```

- With a side, path returns are multiplied by it: the profit barrier for a short sits
  *below* the entry, and `realized_return` is the return of the bet, so a profitable
  short is positive.
- Asymmetric `pt_mult`/`sl_mult` become meaningful here — they now describe a target
  and a stop on a directional position.
- `meta_label` is the AFML `getBins` binary target: fit the secondary model on it to
  decide *whether* to take each bet, and size with the resulting probability. The
  ternary `barrier_touched` remains available as the barrier identity.
- The side series must be aligned with `closes` exactly; a misaligned series raises
  rather than being reindexed with NaNs.

## 7. Hand the labels to the training pipeline

```python
spans = labels[["entry_timestamp", "exit_timestamp", "realized_return"]]
```

- Convert those timestamps to bar ordinals and compute uniqueness weights with
  `sample-weighting-for-overlapping-labels` before fitting. With `vertical_bars=10`
  and an event per bar, each label shares up to nine bars of path with its neighbour;
  training on them as IID reports memorisation as accuracy.
- Purge and embargo the cross-validation folds using the same spans
  (`hyperparameter-tuning-without-target-leakage`, `walk-forward-validation-setup`).
  Weighting and purging fix different leaks; a pipeline with only one still leaks.
- Record `pt_mult`, `sl_mult`, `vertical_bars`, `volatility_span`,
  `min_target_return` and `scale_target_by_horizon` with the dataset. The class
  balance is a function of all six, so a label set without them is not reproducible.
- Screen the labels for noise (`label-noise-estimation-in-financial-targets`) and
  check the class ratio (`class-imbalance-handling-for-rare-signal-events`) before
  attributing a model's performance to signal.

## 8. Handle the failure modes deliberately

| Symptom | Cause | Action |
|---|---|---|
| `TripleBarrierError: no event survived target filtering` | Series is shorter than the volatility warm-up, or is flat (halted/forward-filled instrument, $\sigma_t = 0$). | Extend the history, or fix the stale data. Do not lower `min_target_return` below 0. |
| WARNING naming dropped events | Warm-up bars, or events inside `vertical_bars` of the end. | Expected. Reconcile the count against the events you requested. |
| WARNING about symmetric barriers | Asymmetric multipliers with no `side`. | Equalise the multipliers, or pass the side that justifies the asymmetry. |
| Almost no `0` labels | Barriers narrow relative to the horizon. | Widen the multipliers or enable `scale_target_by_horizon`. |
| Many `intrabar_ambiguous` rows | Bar range is wide relative to the barrier width. | Use finer bars; do not rely on the stop-loss tie-break at volume. |
