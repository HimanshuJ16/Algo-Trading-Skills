---
name: transfer-learning-across-correlated-instruments
description: >-
  Use when a new or thinly traded instrument has too little history to fit a model on
  its own and a liquid co-moving instrument exists; fits on the source and adapts,
  rather than fitting noise on the target.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: financial-ml
  tags: financial-ml, transfer-learning, cold-start, l2-sp-fine-tuning, negative-transfer, covariate-shift, out-of-sample-validation
  brokers_frameworks: Python standard library (math, logging, dataclasses)
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill when a model is needed for an instrument that does not yet have
enough history to fit one — a recent IPO or direct listing, a newly launched token, a
thinly traded corporate bond, a small sector ETF — and a liquid instrument exists whose
returns co-move with it. Rather than fitting on 40 noisy bars and calling the result a
signal, the liquid instrument's model is fitted first and then adapted onto the sparse
target under a penalty that keeps it near the pre-trained weights.

`FinancialTransferLearningEngine` does four separable things:

1. **Pre-trains** an ordinary least-squares forecaster on the source instrument, in
   closed form, and keeps its feature scaler.
2. **Fine-tunes** onto the target by minimising
   `(1/N) * ||y - Zw - b||^2 + lambda * ||w - w_src||^2` — the L2-SP objective of Li,
   Grandvalet & Davoine (ICML 2018) — again in closed form. For `lambda > 0` this is
   identified even when the target has fewer rows than features, which is the whole
   reason the method is worth using on a cold start.
3. **Screens for negative transfer** on two axes: the timestamp-aligned correlation of
   the two instruments' targets, and the per-feature standardized mean difference
   between their feature distributions.
4. **Decides deployment** on a chronological out-of-sample comparison against a
   target-only baseline, scored with the Campbell–Thompson (2008) out-of-sample
   R-squared whose benchmark is the fit window's historical mean.

## When NOT to Use

- **The target already has enough history.** If a target-only fit is well identified
  and stable, transfer buys nothing and only imports the source's biases. Run the
  comparison; if the gain is not positive out of sample, ship the target-only model.
- **The relationship you want is non-linear.** This engine fits a linear model. The
  gating logic (alignment, shift screening, chronological OOS comparison) generalises;
  the estimator does not.
- **Source and target do not share a feature definition.** Transfer requires the same
  features, computed the same way, on the same sampling frequency. Two different
  feature spaces are not a transfer problem.
- **You need a distributional distance, not a mean shift.** The shift metric here is a
  standardized mean difference and is blind to changes in dispersion or shape. If two
  regimes differ in volatility at equal means, use a Wasserstein or KS distance
  instead — this engine will report zero shift.
- **As evidence that `P(Y|X)` is stable.** Nothing here tests that. See *Common
  Pitfalls*.

## Prerequisites

- Python 3.9+. No third-party packages: `scripts/transfer_learning_bootstrap.py` uses
  only `math`, `logging`, `dataclasses` and `typing`.
- Matched feature matrices for both instruments — identical feature definitions, lag
  structures and sampling frequency — with all values finite.
- **Strictly increasing integer timestamps on both datasets**, in the same unit.
  `evaluate_transfer_performance` refuses to run without them: it cannot align two
  instruments or order a chronological split otherwise.
- A target history long enough that a held-out tail of `test_fraction` still contains
  `min_test_samples` bars.

## Workflow

1. **Assemble both datasets with timestamps.** Build `Dataset(symbol, features,
   targets, feature_names, timestamps)` for source and target. Timestamps must be
   strictly increasing and drawn from the same clock and trading calendar — the engine
   joins on exact equality, so a source on session closes and a target on a different
   session boundary will report an empty overlap and raise rather than silently
   correlate misaligned bars.
2. **Configure the gate.** `TransferConfig(source_symbol, target_symbol,
   min_correlation, min_correlation_overlap, l2_penalty, max_domain_shift,
   max_feature_domain_shift, test_fraction)`. **None of the defaults are standards** —
   see `references/standards.md`. Calibrate `l2_penalty` on the target's own held-out
   window; set `max_feature_domain_shift` unless you have a reason not to.
3. **Run `evaluate_transfer_performance(source, target, config)`.** It splits the
   target chronologically, truncates the source to bars strictly before the held-out
   window opens, computes the aligned correlation and the per-feature shifts, fits the
   source model, fine-tunes it, fits the target-only baseline, and scores both on the
   held-out window.
4. **Read `rejection_reasons`, not just the boolean.** Every failed condition is listed
   with the measured value and the threshold it breached, so a rejection tells you
   *which* gate to act on. An approval means all of: sufficient aligned overlap,
   correlation at or above the floor, shift within the ceilings, `transfer_model_r2 >
   0`, and a positive gain over the baseline where that baseline is identified.
5. **Check the correlation's confidence interval before trusting a marginal pass.**
   `correlation_ci95_low` is the Fisher-z lower bound. A correlation of 0.62 over 31
   bars clears a 0.60 floor and has a lower bound near 0.35; that is a coin-flip
   dressed as a gate. Raise `min_correlation_overlap` rather than lowering the floor.
6. **Handle `direct_target_r2 is None` explicitly.** On a genuinely cold-start
   instrument the target-only baseline is often unidentified — the transferred model is
   then the only candidate, and condition `transfer_model_r2 > 0` is the only
   performance evidence you have. Do not read `None` as "the baseline scored zero".
7. **Archive the `audit_trail`** with the evaluation. It records the split point, the
   number of source rows dropped, the overlap size, and every measured statistic.

## Common Pitfalls

- **Scoring the fine-tuned model on the rows it was fitted on and calling it OOS.** An
  unregularized fit on `n = D + 1` rows of pure noise interpolates them exactly; an
  in-sample R-squared on a cold-start instrument is close to guaranteed positive, so a
  gate driven by it approves everything. Split chronologically and score only on the
  held-out tail — which is what `evaluate_transfer_performance` does, and why it
  requires timestamps.
- **Benchmarking out-of-sample R-squared against the test window's own mean.** That
  hands the benchmark a statistic of the period being scored. Campbell & Thompson
  (2008) benchmark against the *fit* window's historical mean; `calculate_oos_r2` takes
  that benchmark as an argument for exactly this reason, and `calculate_r2` is kept
  separate for genuine in-sample use.
- **Leaving the source's history overlapping the target's evaluation window.** The two
  instruments co-move by premise — that is why you chose this source — so source bars
  drawn from the evaluation period leak that period into the pre-trained weights. The
  engine truncates the source and records how many rows it dropped.
- **Correlating a prefix of the source against the target.** Slicing the source's first
  `len(target)` rows compares the source's oldest bars with the target's, which is a
  correlation of nothing. Align on timestamps.
- **Treating a correlation of returns as evidence that the transfer is valid.**
  Covariate shift in Shimodaira's (2000) sense assumes `P(X)` moves while `P(Y|X)`
  stays fixed. Co-movement of two instruments' *outcomes* is a different claim, and
  neither the correlation gate nor the shift metric tests the `P(Y|X)` half. It is an
  assumption; the out-of-sample comparison is the only thing that challenges it.
- **Reading the mean standardized mean difference as the whole story.** Averaged across
  `D` features, one catastrophically shifted feature disappears. Set
  `max_feature_domain_shift`, and read `worst_shift_feature`.
- **Calling the shift metric a Wasserstein distance.** It is a standardized mean
  difference: two distributions with equal means and wildly different variances score
  0.0. The first Wasserstein distance integrates `|F_src - F_tgt|` over the CDFs and
  would not.
- **Re-standardizing the target with the target's own statistics.** That destroys the
  alignment with the pre-trained weights. `ModelParameters` carries the source scaler
  so the fine-tuned model inherits it; do not substitute a target-fitted scaler.
- **Approving on a positive gain alone.** Out of sample both R-squared values can be
  negative, and "beat the baseline" is then satisfied by a model that is itself worse
  than predicting the fit-window mean. The engine additionally requires
  `transfer_model_r2 > 0`.
- **Assuming `lambda`'s effect decays as the target's history grows.** It does not —
  L2-SP defines the penalty against the *mean* squared error, so shrinkage toward the
  source is governed by `lambda` alone and is invariant to `N`. To let the prior wash
  out asymptotically, scale `l2_penalty` with `1 / n_target` yourself.

## Verification

Run the unit tests, which check the closed-form solutions against hand-derived values,
the L2-SP limits, timestamp alignment and split behaviour, and the rejection conditions:

```bash
python -m unittest discover -s skills/transfer-learning-across-correlated-instruments/scripts
```

Then confirm on your own data that: the audit trail's split timestamp precedes every
source bar used; `correlation_overlap` matches the true number of shared bars;
`transfer_model_r2` is positive and exceeds `direct_target_r2`; and re-running the
evaluation reproduces every figure exactly. Work through
`assets/checklist.md` before the model takes capital.

## Related Skills

- `cold-start-handling-for-newly-listed-instruments`
- `feature-engineering-without-leakage`
- `hyperparameter-tuning-without-target-leakage`
- `concept-drift-vs-staleness-differentiation`
- `cross-sectional-vs-time-series-model-design`
- `walk-forward-validation-setup`
