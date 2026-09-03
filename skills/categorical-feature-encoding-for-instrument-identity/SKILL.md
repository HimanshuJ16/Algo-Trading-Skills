---
name: categorical-feature-encoding-for-instrument-identity
description: Use when instrument identity (ticker, symbol, contract) must become a
  numeric feature for a cross-sectional trading model — replacing a high-cardinality
  symbol column with a smoothed target encoding computed only from labels already
  realized at each row's timestamp, so newly listed symbols cold-start onto a prior
  instead of a spurious extreme and no row is encoded using its own or a later label.
domain: algorithmic-trading
subdomain: financial-ml
tags:
- financial-ml
- categorical-encoding
- target-encoding
- instrument-identity
- point-in-time
brokers_frameworks:
- pandas
- NumPy
- scikit-learn
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when a cross-sectional model — one that ranks or scores many
instruments at the same timestamp — needs to know *which* instrument a row belongs to,
and the symbol column has hundreds or thousands of distinct values. One-hot encoding a
3,000-name universe adds 3,000 near-empty columns; label encoding (AAPL=0, ABBV=1, …)
invents an ordering that a tree model will happily split on. Target encoding replaces
the symbol with one number: a shrinkage blend of that symbol's own historical target
mean and the global historical target mean.

The reason this needs a skill rather than a one-liner is that target encoding is
constructed *from the labels*, so a careless implementation is a leakage machine. The
`PointInTimeTargetEncoder` in `scripts/` restricts every row's encoding to observations
that were already realized at that row's timestamp, and preserves the caller's row order
and index so the resulting feature cannot silently misalign against those labels.

## When NOT to Use

- **Low-cardinality identity features.** Sector, exchange, or currency with a handful of
  levels are better one-hot encoded: the sparsity argument does not apply, and target
  encoding buys leakage risk for nothing.
- **Short panels or short per-symbol history.** With a few hundred rows per symbol the
  encoding is mostly the prior; you have added a leakage surface to reproduce a constant.
- **When identity itself should not carry signal.** If the intent is a model that
  generalises to instruments it has never traded, encoding identity teaches it
  per-symbol effects that will not transfer. Prefer symbol-level *characteristics*
  (liquidity tier, ADV bucket, sector) which a new listing also has.
- **Random-split cross-validation.** Point-in-time encoding only protects a
  chronologically split evaluation. Combined with random k-fold it produces an optimistic
  number regardless of how careful the encoder is.
- **Non-panel data with no time axis.** Use `sklearn.preprocessing.TargetEncoder`, whose
  k-fold cross-fitting is the right defence when there is no chronology to order by.

## Prerequisites

- A panel with one row per (instrument, timestamp): a symbol column, a timestamp column
  (timezone-aware, or a monotone integer bar index), a numeric target, and the features.
- A written definition of the target that states **when its value becomes observable** —
  a 5-day forward return computed at time `t` is not knowable until `t + 5 days`. That
  holding period is the `label_horizon` argument, and getting it wrong is the main way
  this skill still leaks.
- A chronological (walk-forward) evaluation split. See `walk-forward-validation-setup`.
- pandas and NumPy. No broker or venue connectivity is involved.

## Workflow

1. **State the label horizon before writing any code.** Convert the target's holding
   period into a `pd.Timedelta` (or an integer count of bars, if the time column is a bar
   index). Leave `label_horizon=None` *only* when the label is realized by the very next
   timestamp in the panel — the one-step-ahead case.
2. **Choose the cold-start prior deliberately.** `cold_start_prior` is what a row gets
   when no history exists at all. The 0.0 default is right for a centred target such as
   an excess return, and wrong for a 0/1 classification label, where it asserts an event
   that never happens — set the expected base rate, or `float("nan")` to make the absence
   of history explicit to whatever handles missing values downstream.
3. **Pick a smoothing weight in units of observations.** `smoothing_weight=m` means the
   symbol's own mean carries 50% of the weight once it has `m` realized observations.
   Choose it from how many observations you would want before trusting a symbol-specific
   estimate, not by grid search on the validation fold you are about to report.
4. **Fit on history, transform forward.** `fit_transform(df, time_col, symbol_col,
   target_col)` encodes a backtest panel in one pass. For live inference, `fit()` on the
   history and `transform()` on the live rows — `transform` never reads a target column,
   so a live row with no label yet is encoded exactly as the backtest encoded it. Do not
   re-fit on a frame that concatenates live rows with training rows.
5. **Verify the cold-start rows rather than assuming them.** Confirm the first timestamp
   in the panel encodes to the prior, and that a symbol appearing mid-panel (an IPO, an
   index addition) starts at the global mean and converges toward its own mean as its
   count grows past `smoothing_weight`.
6. **Drop the raw symbol string before fitting the estimator.** Keep it in the frame for
   debugging, exclude it from the feature matrix; leaving an object-dtype column in the
   design matrix is either an error or a silently different encoding.

> Full procedure, including how the point-in-time cutoff is computed: see
> `references/workflows.md`.
> Formula provenance and engineering standards: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Encoding on the whole dataset first, splitting second.** A global `groupby(symbol)
  .target.mean()` applied to every row puts day 250's outcomes into day 1's feature. It
  is the single most common way a target-encoded backtest produces an unreproducible
  Sharpe.
- **Treating "strictly earlier timestamp" as sufficient.** It is not, whenever the label
  looks forward more than one step. With minute bars and a 1-day forward return, the
  label attached to the previous minute is not observable for another day — using it is
  leakage even though its timestamp is in the past. This is the purge that
  `label_horizon` implements; see also `sample-weighting-for-overlapping-labels`.
- **Assuming a zero prior is neutral.** For a 0/1 label, a 0.0 cold-start prior is not
  "no information" — it is a confident prediction of the negative class for every newly
  listed instrument.
- **Letting the encoder reorder or reindex the frame.** If the returned feature column
  comes back in a different row order than the labels it will be paired with, the model
  trains on scrambled data and the failure is silent. Check the index, not just the
  column.
- **Recycled tickers and corporate actions.** Symbols are reused: today's `XYZ` may be an
  unrelated company from a delisted one, and a ticker change splits one instrument's
  history in two. Encode a stable internal instrument ID where you have one — see
  `isin-cusip-sedol-cross-reference-service` and
  `reference-data-symbol-mapping-across-vendors`.
- **Tuning the smoothing weight against the reported evaluation fold.** The weight is a
  prior strength; fitting it to the test period is a second, subtler leak on top of the
  one this skill exists to prevent.
- **Ignoring that identity encoding is a bet on persistence.** The encoding says "this
  symbol's target has been high"; it pays only if that is persistent rather than
  mean-reverting, and it decays as regimes change. Monitor it like any other feature —
  see `feature-importance-drift-monitoring`.

## Verification

- Run `python -m unittest discover -s skills/categorical-feature-encoding-for-instrument-identity/scripts` (40 tests),
  or `python tools/run_all_tests.py` for the whole repo.
- Feed a three-symbol panel where one symbol appears only from the middle of the sample.
  Confirm the first timestamp encodes to exactly the cold-start prior, the newly
  appearing symbol's first row encodes to exactly the global mean, and subsequent rows
  move toward the symbol's own mean.
- Shuffle the input rows and re-encode: every row's value must be unchanged and the
  caller's index must come back intact.
- Leakage sanity check: append a row with an extreme target at the end of the panel and
  confirm no earlier row's encoding moves at all.
- Train/live parity check: `fit()` on history and `transform()` a later row, then compare
  against `fit_transform()` over the combined panel. The two must agree to floating-point
  precision; if they do not, the live model is being fed a different feature than it was
  trained on.

## Related Skills

- `feature-engineering-without-leakage`
- `lookahead-bias-elimination`
- `cross-sectional-vs-time-series-model-design`
- `cold-start-handling-for-newly-listed-instruments`
- `sample-weighting-for-overlapping-labels`
- `feature-store-for-live-and-backtest-parity`
- `walk-forward-validation-setup`
