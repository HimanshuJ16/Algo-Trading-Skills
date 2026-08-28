# Pre-Flight Checklist — Synthetic Labels from the Triple Barrier Method

Sign off before a label set is used to train a model whose output will move capital.

## Input data

- [ ] Closes are strictly positive and finite; the engine raises on NaN, `inf`, zero
      and negative prices — confirm the pipeline surfaces that error rather than
      swallowing it.
- [ ] The index is **unique and monotonically increasing**, at one bar frequency
      throughout. A shuffled or duplicated index is rejected, but a series that was
      re-indexed after shuffling is indistinguishable from a sorted one.
- [ ] The series is not stale: no forward-filled halt, no repeated close from an
      illiquid name. A flat stretch gives $\sigma_t = 0$ and its events are dropped;
      a nearly flat one gives barriers narrower than the tick size.
- [ ] At least `volatility_span` bars of history sit ahead of the first event you
      need labelled.
- [ ] If `highs`/`lows` are supplied, they bracket every close and are aligned
      bar-for-bar with it.

## Barrier configuration

- [ ] `vertical_bars` equals the holding period the strategy will actually run — not
      a round number, and not a different clock from the bars.
- [ ] `pt_mult` and `sl_mult` are **equal** unless a `side` is supplied. Asymmetric
      barriers with no direction manufacture a class skew (AFML Snippet 3.3).
- [ ] `volatility_span` recorded as a deliberate choice; note that AFML's own
      `getDailyVol` default is 100, this engine's is 20.
- [ ] `min_target_return` set with intent, and never below 0 to rescue dropped
      events — a zero-width barrier is not a barrier.
- [ ] Every one of `pt_mult`, `sl_mult`, `vertical_bars`, `volatility_span`,
      `min_target_return` and `scale_target_by_horizon` is stored with the dataset.
      The class balance is a function of all six.

## Label quality

- [ ] Class balance inspected. A large $+1/-1$ gap on a liquid instrument is barrier
      geometry until proven otherwise; compare against the driftless-random-walk
      table in `references/standards.md`.
- [ ] The vertical barrier fires often enough to be a real class. If $0$ is under a
      few percent, the barriers are too narrow for the horizon.
- [ ] `len(labels)` reconciled against the events requested: warm-up drops,
      `min_target_return` drops and end-of-series drops all accounted for, not
      assumed to be zero.
- [ ] `intrabar_ambiguous` rows counted. A material share means the bars are too
      coarse for the barrier widths — go finer rather than trusting the tie-break.
- [ ] If labelling closes only, it is a recorded decision that an intrabar stop-out
      followed by a recovery will be labelled as if the position survived.

## Meta-labelling (when `side` is supplied)

- [ ] The side series comes from a primary model and is aligned exactly with the
      closes; no reindexing that would introduce NaNs.
- [ ] `realized_return` read as the return *of the bet*: a profitable short is
      positive.
- [ ] `meta_label` (1 = take, 0 = pass) used as the secondary target; the ternary
      `barrier_touched` kept for diagnostics rather than fed to a binary model.

## Downstream integrity

- [ ] Uniqueness weights computed from `entry_timestamp`/`exit_timestamp` before
      fitting — `sample-weighting-for-overlapping-labels`.
- [ ] Cross-validation folds purged and embargoed over the same spans —
      `hyperparameter-tuning-without-target-leakage`, `walk-forward-validation-setup`.
- [ ] Features confirmed computable strictly before each label's entry bar —
      `feature-engineering-without-leakage`.
- [ ] It is written down somewhere that these labels are a training target and not a
      trading signal: their outcome is read from bars that do not exist at decision
      time.
- [ ] `python -m unittest discover -s skills/synthetic-labels-from-triple-barrier-method/scripts`
      passes 100% on the pinned dependency versions.
