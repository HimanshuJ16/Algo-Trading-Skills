# Instrument Identity Encoding Pre-Flight Checklist

Model / dataset: ____________  Target column: ____________
Encoder config — `smoothing_weight`: ______  `label_horizon`: ______
`cold_start_prior`: ______  Reviewed by: ____________  Date (UTC): ____________

## Before encoding

- [ ] Cardinality justifies target encoding at all (hundreds+ of symbols). Below that,
      one-hot encoding is simpler and carries no leakage surface.
- [ ] Encoding identity is intended: the model is allowed to learn per-symbol effects and
      is not required to generalise to instruments it has never seen.
- [ ] Target definition written down **with its observability time**, not just its value.
- [ ] `label_horizon` set from that observability time and cross-checked against the bar
      frequency. (Minute bars with a daily label still need a 1-day horizon. `None` is
      correct only for a one-step-ahead label.)
- [ ] `cold_start_prior` matched to the target's units — `0.0` for centred returns, the
      base rate for a 0/1 label, `nan` if the estimator handles missing values and you
      would rather it saw the gap.
- [ ] `smoothing_weight` chosen from "how many observations before I trust this symbol's
      own mean", stated before looking at any results.
- [ ] Symbol column is a stable instrument identifier, or ticker reuse and ticker changes
      in the sample period have been checked and are known to be absent.
- [ ] Universe is survivorship-bias-free; delisted names are present for the periods they
      were live.

## Verifying the encoded column

- [ ] First timestamp of the panel encodes to exactly `cold_start_prior`.
- [ ] A symbol first appearing mid-panel encodes to exactly the global mean on its first
      row, and converges toward its own mean as its count passes `smoothing_weight`.
- [ ] Appending a row with an extreme target at the end of the panel moves **no** earlier
      row's encoding.
- [ ] Shuffling the input rows changes no value, and the returned index equals the input
      index. (Check the index, not only the column — misalignment is silent.)
- [ ] Encoded column contains no unexpected NaN or inf. NaN is expected only where
      `cold_start_prior` is NaN, and only at the first timestamp.
- [ ] Cross-sectional dispersion of the encoded column is non-trivial. A near-constant
      column means the smoothing weight is swamping every symbol's own history.

## Train / live parity

- [ ] `fit()` on history + `transform()` on a later row reproduces the `fit_transform()`
      value for that row to floating-point precision.
- [ ] Live path calls `transform()`, never a re-fit over concatenated train + live rows.
- [ ] Encoder configuration is persisted with the model artefact — the encoding is part
      of the model, and a prediction cannot be reproduced without it.
- [ ] Refit cadence decided and scheduled, not left to whenever someone reruns training.

## Before reporting any performance number

- [ ] Evaluation split is chronological (walk-forward), not a random k-fold.
- [ ] `smoothing_weight` was not tuned on the period being reported.
- [ ] Encoded-feature importance reviewed: if identity dominates every other feature, the
      model is likely memorising symbols rather than learning a transferable effect.
- [ ] Backtest-vs-live divergence monitoring covers this feature specifically — its value
      drifts as history accumulates even when nothing else changes.
