# Standards for Point-in-Time Instrument Identity Encoding

## 0. How to read this document

This skill has **no regulatory surface**. Nothing here is a legal or exchange
requirement; encoding a ticker as a number is a modelling choice, not a reportable
activity. Section 1 records the provenance of the formula so the implementation can be
checked against its source. Sections 2-5 are **engineering standards** — this
repository's recommended practice.

Where a number appears (a smoothing weight, an observation count), it is an engineering
default to be calibrated on your own data, not a threshold anyone has established.

If the encoded feature ends up inside a model that a regulator will ask you to explain,
the governing obligations live in the model-documentation and validation skills
(`model-card-documentation-for-trading-models`, `explainability-for-live-trading-signals`),
not here.

## 1. Provenance of the smoothing formula

**Primary source.** Daniele Micci-Barreca, "A preprocessing scheme for high-cardinality
categorical attributes in classification and prediction problems", *ACM SIGKDD
Explorations Newsletter* 3(1), pp. 27-32, 2001.
<https://dl.acm.org/doi/10.1145/507533.507538>

The paper introduces replacing a high-cardinality categorical level with a blend of the
level's own target mean and the overall target mean, justified as an empirical-Bayes
shrinkage estimator: the fewer observations a level has, the more its estimate is pulled
toward the prior.

**Implementation cross-check.** `sklearn.preprocessing.TargetEncoder` documents the same
estimator as

```
S_i = lambda_i * (n_iY / n_i) + (1 - lambda_i) * (n_Y / n)
lambda_i = n_i / (m + n_i)
```

where `m` is the smoothing parameter.
<https://scikit-learn.org/stable/modules/preprocessing.html#target-encoder>

This skill computes

```
encoded = (local_sum + m * global_mean) / (local_count + m)
```

which is the same estimator rearranged: substituting `local_sum = n_i * local_mean` and
`lambda_i = n_i / (m + n_i)` recovers the shrinkage form exactly. The rearrangement is
preferred here because it stays defined at `local_count == 0`, where the shrinkage form
needs a separate branch for `local_mean` (`0/0`).

**Verified consequence.** At `local_count == m`, `lambda_i = 0.5` — so a smoothing weight
of 20 means the symbol's own mean carries half the weight once it has 20 realized
observations. This is asserted directly by the unit test
`test_shrinkage_gives_the_symbol_mean_half_the_weight_at_n_equals_weight`.

**Difference from the scikit-learn implementation.** scikit-learn's `fit_transform`
defends against leakage with random k-fold cross-fitting, because a generic tabular
dataset has no time axis. A financial panel does, so this skill orders by time instead:
each row is encoded from realized history only. The two defences are not
interchangeable — k-fold cross-fitting on a time series still lets a fold learn from its
own future.

## 2. Engineering standard — what "available at time T" means

*Recommended practice, not a regulatory requirement.*

An observation at time `t` may contribute to the encoding of a row at time `T` only when
**both** hold:

- `t <= T - label_horizon` — the observation's label has actually been realized. For a
  target defined as a forward return over a holding period `h`, the label attached to `t`
  is unknown until `t + h`.
- `t < T` — the observation is not contemporaneous with the row being encoded.

The second condition is what makes the horizon-free case (`label_horizon=None`) safe for
one-step-ahead labels; the first is the purge that López de Prado prescribes for
overlapping labels (*Advances in Financial Machine Learning*, Wiley, 2018, ch. 7, "Purging
the Training Set"). The book applies purging to train/test folds; the same argument
applies to any statistic built from labels, target encoding included.

Practical consequence worth internalising: **a timestamp in the past is not the same as
information in the past.** Minute bars carrying a 1-day forward return produce 390
past-timestamped labels per session, none of which is observable within that session.

## 3. Engineering standard — choosing the smoothing weight

*Recommended practice, not a regulatory requirement.*

- Read the weight as "how many of this symbol's own observations before I trust its own
  mean as much as the crowd's". That is a judgement about the noisiness of your target,
  and it is stateable before seeing any results.
- Higher-frequency targets are noisier per observation, so they need a *higher* weight,
  not a lower one, for the same effective confidence.
- Tune it, if at all, on an inner walk-forward split — never on the period whose
  performance you intend to report. See `hyperparameter-tuning-without-target-leakage`.
- A weight so high that no symbol's `local_count` ever approaches it produces a column
  that is nearly constant across symbols. Check the cross-sectional dispersion of the
  encoded column before concluding the feature is useless.

## 4. Engineering standard — the cold-start prior

*Recommended practice, not a regulatory requirement.*

- The prior is only ever used where **no** history exists — in a fitted panel, that is the
  first timestamp. Everywhere else, a symbol with no history of its own falls back to the
  global mean, which is the behaviour the cold-start case is usually about.
- Match the prior to the target's units: `0.0` for centred returns, the expected base
  rate for a 0/1 label, `float("nan")` when you would rather the model's missing-value
  handling see the gap than be handed a fabricated zero.
- A NaN prior is the honest choice when the estimator handles missing values natively
  (LightGBM, XGBoost). It is the wrong choice for an estimator that will raise or
  silently drop the row.

## 5. Engineering standard — instrument identity itself

*Recommended practice, not a regulatory requirement.*

- Encode a **stable instrument identifier**, not a display ticker, wherever one is
  available. Tickers are reassigned after delisting, change on rebranding, and differ per
  vendor; each of those splits or merges a symbol's history in ways the encoder cannot
  detect. See `isin-cusip-sedol-cross-reference-service` and
  `reference-data-symbol-mapping-across-vendors`.
- Corporate actions do not invalidate the encoding directly, but they do invalidate the
  *target* it is built from if the price series is not adjusted point-in-time. See
  `corporate-action-adjusted-backtesting`.
- Universe survivorship matters here as much as anywhere: fitting the encoder on today's
  index members rewrites history for every name that was dropped. See
  `survivorship-bias-free-universe-construction`.
- The encoded column is a per-symbol statistic and therefore an obvious carrier of
  overfitting in a small universe. With 20 instruments, per-symbol dummies and a target
  encoding are nearly the same object; the sparsity argument for target encoding only
  starts to pay at high cardinality.
