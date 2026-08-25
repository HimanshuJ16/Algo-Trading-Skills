---
name: hyperparameter-tuning-without-target-leakage
description: Use when selecting hyperparameters for a model trained on serially
  dependent financial time series with overlapping labels, to run purged and embargoed
  nested cross-validation so the folds that choose the hyperparameters are never the
  folds that score them, and to measure — rather than assume — how much a non-nested,
  non-purged grid search would have overstated performance
domain: algorithmic-trading
subdomain: financial-ml
tags:
- financial-ml
- hyperparameter-tuning
- nested-cross-validation
- purged-cv
- embargo
- selection-bias
- backtest-overfitting
brokers_frameworks:
- Leakage-Free Hyperparameter Tuner
- Python standard library (statistics, math)
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this when a grid, random or Bayesian search will choose hyperparameters for a model whose target is a **forward-looking, overlapping label** — an $h$-bar return, a triple-barrier outcome, a meta-label. Two independent biases stack in this setting, and defeating one does not defeat the other:

- **Fold contamination.** With an $h$-bar label, observation $t$'s target is realised at $t+h$. Any fold boundary crossed by that interval puts the validation period's outcome into the training set. López de Prado's remedy is **purging** the overlapping training observations and **embargoing** a buffer after each validation block (*Advances in Financial Machine Learning*, 2018, Ch. 7, Snippets 7.1–7.4, pp. 106–110).

- **Selection bias.** Reporting the cross-validation score of the configuration that *won* that same cross-validation is optimistic, because the score contains the maximum of many noisy estimates. Varma & Simon measured this directly: on datasets constructed with **no** class difference — true error 50% — tuned-CV reported a mean error of 37.8% (shrunken centroids) and 41.7% (SVM), and on 38% of the SVM null datasets it reported under 30%. Nested CV "reduces the bias considerably and gives an estimate of the error that is very close to that obtained on the independent testing set" (*BMC Bioinformatics* 7:91, 2006).

The engine addresses both: an outer loop that scores, an inner loop that selects, and purge/embargo geometry applied at **both** levels.

## When NOT to Use

- **As a leakage detector for features.** This controls *which observations* each fit may see. It is blind to a feature that is itself contaminated — a centred rolling window, an adjusted close, a fundamental joined by effective date. Those pass every fold boundary cleanly and still leak. See `feature-engineering-without-leakage`.

- **When the evaluation callback does not honour the index sets.** The engine hands the callback `train_indices` and `val_indices`; it cannot observe what the callback then does. A callback that calls `scaler.fit(X)` on the full frame, or ignores `train_indices`, defeats the whole apparatus while the report still says isolation is verified. `structural_isolation_verified` attests to the *index sets*, nothing more.

- **On data that is not in time order.** Purge and embargo are computed from index arithmetic. Index $i$ must be the $i$-th observation chronologically. A shuffled frame produces geometrically valid, semantically meaningless splits — and no error.

- **As the only guard against backtest overfitting.** Nesting removes the bias from *reusing* folds; it does not remove the inflation from *how many* configurations were tried. A 500-point grid produces a high best score under the null no matter how clean the folds are. Bound the search itself — see `walk-forward-hyperparameter-search-budget` — and read `expected_max_sharpe_under_null` in the report.

- **When labels do not overlap at all** (a strictly one-bar-ahead target, non-overlapping sampling). Purging then removes nothing and costs data; set `purge_window_samples=0` deliberately rather than leaving a default that quietly discards observations.

## Prerequisites

- A chronologically ordered sample of length `n_samples`, index $i$ = the $i$-th observation in time.
- The **label horizon $h$ in bars**, taken from the target definition, not guessed. `purge_window_samples` must equal it: a smaller value leaves overlapping labels in training, and that is silent.
- An evaluation callback `f(params, train_indices, val_indices) -> float`, higher-is-better, finite, that fits **every** stateful step — scaler, encoder, imputer, feature selector — on `train_indices` alone.
- A parameter grid small enough to be defensible. Record its size; it is the $N$ in the luck floor.
- Enough observations for the geometry: at least `outer_folds_count × inner_folds_count`, and materially more once purge and embargo have taken their share.

## Workflow

1. **Fix the label horizon before the fold geometry.** The purge window is a property of the target, not a tuning knob. With an $h$-bar forward label whose interval is $[i, i+h]$, the observations $[\text{val\_start} - h, \text{val\_start})$ all read a price at or after the first validation bar and must go. That is the inclusive-overlap convention of Snippet 7.1.

2. **Build the outer split first, and purge it too.**
   - **Decision point — the outer training pool needs the same treatment as the inner ones.** It is tempting to define the outer training set as "everything that is not the test block." That set still contains the $h$ observations whose labels reach into the test block, so the final out-of-sample number — the one that gets reported to a capital allocator — is itself contaminated. `generate_purged_embargoed_split` is applied at the outer level for this reason.

3. **Tune inside the purged outer pool, never inside the raw sample.**
   - **Decision point — inner folds must be drawn from the outer training pool, not from `range(n_samples)`.** Slicing inner folds out of the full sample re-admits the outer test block through the back door: the tuning loop then selects the configuration that best fits the data it is about to be scored on, and the nesting is nominal. Pass the pool as `candidate_indices`; the split composes and inherits the outer fold's exclusions.
   - An inner block may straddle the outer test block and be non-contiguous in global time. That is correct — both segments are outer-training data — and the purge/embargo zones are computed from the block's global span, so its outer boundaries are still buffered.

4. **Embargo after every validation block.** $\lceil n \cdot E \rceil$ bars at $E = 1\%$.
   - **Decision point — the published snippet truncates and this module rounds up.** Snippet 7.2 computes $\lfloor T \cdot E \rfloor$, which is **zero** for any sample shorter than $1/E$ — 100 bars at 1%. On a short sample the published code silently applies no embargo at all. `embargo_window()` uses $\lceil \cdot \rceil$ so a positive `embargo_pct` always buffers at least one bar. Know which convention you are quoting.

5. **Select deterministically, then score once.** The winning configuration per outer fold is the highest mean inner score, ties to the lowest grid index. It is then fit on the purged outer pool and scored on the outer test block exactly once. Do not look at that score and re-tune.

6. **Read the three gaps the report gives you, and do not confuse them.**
   - `selection_bias_haircut` = best inner-CV score − nested out-of-sample score. The Varma & Simon quantity.
   - `leakage_overestimation_haircut` = non-nested unpurged K-Fold best − nested out-of-sample score. What the naive pipeline would have overstated.
   - `expected_max_sharpe_under_null` = the best-of-$N$ score expected from luck alone at the observed cross-candidate dispersion (Bailey & López de Prado 2014). If the best inner score does not clear it, the search found the winner of a lottery.
   - **Decision point — a haircut of zero is not a clean bill of health.** It usually means the callback's score does not depend on its training set at all. The engine logs a warning saying so; treat it as a broken measurement, not as absence of leakage.

7. **Check `structural_isolation_verified`, and know its scope.** It is the result of a run-time set-intersection check that no outer-test observation reached any inner index set. It says nothing about what the callback did with the indices.

> Full procedure: see `references/workflows.md`.
> Standards, citations, and stated limitations: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Nesting that is nominal.** Cutting inner folds from `range(n_samples)` instead of from the outer training pool. The code looks nested, the report says nested, and every tuning fold trains on the test block. This is the failure mode that makes a leakage-free tuner *worse* than no tuner, because it certifies the result.
- **Purging the inner folds but not the outer one.** The tuning is then honest and the headline out-of-sample number still overlaps its own training labels.
- **Setting `purge_window_samples` below the label horizon.** Off by a few bars removes most of the overlap and none of the confidence; the residual leak is invisible in every diagnostic the engine reports.
- **Fitting a scaler, ranker or imputer outside the callback.** `StandardScaler().fit(X)` before the split puts the test period's mean and variance into every training row. No fold geometry can detect this.
- **Manufacturing the haircut.** Reporting an assumed or randomly drawn "leakage overestimate" instead of measuring one. A fabricated haircut is worse than no haircut: it launders an unexamined pipeline as an audited one, and it is not reproducible between runs.
- **Treating a 1% embargo as always non-zero.** Below 100 bars, the published truncating formula disables it entirely.
- **Reading the best inner-CV score as an out-of-sample expectation.** It is the maximum of $N$ noisy estimates. Under a pure null with 10 trials of unit dispersion, the expected maximum is already ≈ 1.57.
- **Re-tuning after seeing the outer score.** Each glance at the test block spends it. The outer fold is a single-use instrument.
- **Assuming purge and embargo are interchangeable.** Purging removes labels that reach *forward* into the validation block; the embargo removes training observations *after* it whose features are still serially correlated with it. Neither substitutes for the other.
- **Running the geometry on unsorted data.** Every guard here is index arithmetic and will report success on a shuffled frame.

## Verification

- **Index geometry.** With `purge_window_samples=5`, `embargo_pct=0.01`, `n_samples=100` and a validation block `[30, 50)`: confirm exactly 5 purged and 1 embargoed observation, 74 training observations, that bars 25–29 and 50 are absent from training, and that bars 24 and 51 are present — a gap, not a truncation.
- **Nested isolation (regression).** Record every index set the callback receives over a 3-outer × 2-inner run on 300 samples. For each outer fold, confirm the outer test block intersects **no** inner training or validation set, and that the single out-of-sample call validates on exactly that block. Against the pre-2.0 implementation this check fails with 95–100 of the 100 test observations visible in every tuning call.
- **Outer purge/embargo (regression).** Confirm the out-of-sample training set excludes the 5-bar purge zone before the test block and the 3-bar embargo zone after it, while retaining the bars immediately beyond both.
- **Isolation flag is checked, not asserted.** Inject a contaminated inner pool and confirm `structural_isolation_verified` flips to `False` and an ERROR is logged.
- **No fabrication.** With a callback whose score depends only on its parameters, confirm `leakage_overestimation_haircut` and `selection_bias_haircut` are **exactly 0.0**, and that two identical runs return equal reports. With a callback that rewards retaining the 5 overlapping bars, confirm the haircut is strictly positive and equals `leaky − nested` to 6 places.
- **No evaluation of caller text.** Confirm a grid whose values have non-executable `repr`s selects correctly and returns the original objects.
- **Luck floor.** Confirm `expected_max_sharpe_under_null(1, σ)` returns the mean (no selection), that $N=2$ reduces to $\gamma \cdot Z^{-1}[1 - 1/(2e)]$ since $Z^{-1}[0.5] = 0$, that $N=10, \sigma=1$ gives $(1-\gamma)(1.28155) + \gamma(1.78924) \approx 1.5746$, that it scales linearly in $\sigma$ and is strictly increasing in $N$.
- **Negative checks.** `outer_folds_count < 2`, `inner_folds_count < 2`, negative purge window, `embargo_pct` outside $[0,1)$, non-positive `n_samples`, an empty grid, a non-callable evaluator, a sample too short for the fold geometry, a configuration that purges a fold empty, and a NaN/Inf/non-numeric score must each raise `TuningError`.
- Run `python -m unittest discover -s skills/hyperparameter-tuning-without-target-leakage/scripts` and confirm a 100% pass rate.

## Related Skills

- `feature-engineering-without-leakage`
- `walk-forward-validation-setup`
- `walk-forward-hyperparameter-search-budget`
- `sample-weighting-for-overlapping-labels`
- `synthetic-labels-from-triple-barrier-method`
- `point-in-time-database-for-ml-training-data`
- `factor-research-multiple-testing-correction`
