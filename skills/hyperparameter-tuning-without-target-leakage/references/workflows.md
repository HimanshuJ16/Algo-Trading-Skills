# Workflows for Leakage-Free Hyperparameter Tuning

## 1. Establish the label horizon

Read $h$ off the target definition, in bars, before choosing any fold count.
`purge_window_samples` must equal $h$. Setting it lower removes most of the
overlap and none of the risk — the residual leak appears in no diagnostic.
For a target with no forward horizon, set it to `0` explicitly rather than
leaving a default that quietly discards observations.

## 2. Size the geometry

```
n_samples >= outer_folds_count * inner_folds_count          # hard minimum
```

The real requirement is larger: each outer fold surrenders $h$ purged bars and
$\lceil n \cdot E \rceil$ embargoed bars, and each inner fold surrenders the
same again from a pool that is already smaller. The engine raises `TuningError`
rather than proceeding with an empty or single-observation fold.

## 3. Build the outer split — purged

```python
outer = engine.generate_purged_embargoed_split(
    candidate_indices=range(n_samples),
    val_start=test_start, val_end=test_end, n_samples=n_samples,
)
pool = outer.train_indices        # already purged and embargoed
```

The outer training pool is **not** "everything except the test block." It
excludes the $h$ observations whose labels reach into the block and the
$\lceil n \cdot E \rceil$ that follow it. Skipping this leaves the headline
out-of-sample number contaminated even when the tuning loop is spotless.

## 4. Tune inside the pool — never inside the raw sample

```python
for p0, p1 in inner_blocks(len(pool), inner_folds_count):
    block = pool[p0:p1]
    inner = engine.generate_purged_embargoed_split(
        candidate_indices=pool,               # <-- the pool, not range(n_samples)
        val_start=block[0], val_end=block[-1] + 1, n_samples=n_samples,
    )
```

Passing `range(n_samples)` here is the defect that makes nesting nominal: the
outer test block re-enters through the training set and the tuning loop
optimises against the data it is about to be scored on.

An inner block may straddle the outer test block and so be non-contiguous in
global time. This is correct — both segments are outer-training data — and the
purge/embargo zones, computed from the block's global span, still buffer its
outer boundaries. The interior boundary needs no buffer because the outer test
block already sits there and is excluded from the pool.

## 5. Select, then score once

Highest mean inner score wins, ties to the lowest grid index. Fit that
configuration on `outer.train_indices`, score it on `outer.val_indices`, once.
Looking at that score and re-tuning spends the fold; there is no way to
un-spend it.

## 6. Read the report

| Field | Meaning | Trap |
|---|---|---|
| `out_of_sample_outer_sharpe` | Mean nested score across outer folds. | The only figure fit to quote as an expectation. |
| `best_inner_cv_sharpe` | Mean score of the winning configuration in the inner loop. | The maximum of $N$ noisy estimates. Never an expectation. |
| `selection_bias_haircut` | `best_inner − out_of_sample`. | The Varma & Simon quantity. |
| `leaky_cv_overestimated_sharpe` | Best score from non-nested, unpurged K-Fold. | A floor on the naive overstatement; shuffled K-Fold is worse. |
| `leakage_overestimation_haircut` | `leaky − out_of_sample`. | Zero usually means the callback ignores its training indices. |
| `expected_max_sharpe_under_null` | Best-of-$N$ score from luck alone. | Assumes independent trials; correlated grids make it conservative. |
| `structural_isolation_verified` | Run-time check on the authorised index sets. | Attests to indices only, never to what the callback did with them. |

## 7. Audit the callback separately

The engine cannot see inside the evaluation callback. Confirm by inspection
that every stateful step is fitted on `train_indices` alone:

```python
def evaluate(params, train_idx, val_idx):
    scaler = StandardScaler().fit(X[train_idx])        # fit on train only
    model = Model(**params).fit(scaler.transform(X[train_idx]), y[train_idx])
    return sharpe(model.predict(scaler.transform(X[val_idx])), y[val_idx])
```

Feature-level contamination is out of scope here entirely — see
`feature-engineering-without-leakage`.
