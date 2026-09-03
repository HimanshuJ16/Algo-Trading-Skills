---
name: sample-weighting-for-overlapping-labels
description: >-
  Use when labels span multiple bars and overlap, so consecutive observations share the
  same price moves; computes label concurrency and average uniqueness to weight samples.
  Fold-boundary leakage still needs purging.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: financial-ml
  tags: financial-ml, sample-weighting, overlapping-labels, sample-uniqueness, label-concurrency, triple-barrier-method, lopez-de-prado
  brokers_frameworks: "Overlapping Sample Weighter; Python standard library; scikit-learn / XGBoost sample_weight interface"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this when a supervised model's target is realised over a **window of bars rather than at a single bar** — a triple-barrier outcome, an $h$-bar forward return, a meta-label attached to a trade's holding period. Labels generated on consecutive bars then share the same price moves: a 5-day label started on Monday and one started on Tuesday are built from four of the same daily returns. Standard estimators assume observations are IID; here they are not, and the model sees the same information several times over, which inflates in-sample accuracy and the apparent significance of any feature.

The remedy (López de Prado, *Advances in Financial Machine Learning*, Wiley 2018, Ch. 4) is to down-weight redundant observations. This engine computes:

- **label concurrency** $c_t$ — how many labels are active on bar $t$ (Snippet 4.1);
- **average uniqueness** $u_i = \text{mean}(1/c_t)$ over the label's span (Snippet 4.2), which is $1.0$ for a label sharing no bar with any other and $0.5$ for one perfectly overlapped by a single neighbour;
- **sample weights** by uniqueness, by absolute return attribution (Snippet 4.10) or with a time decay (Snippet 4.11), normalised so $\sum_i w_i = N$.

Feed the normalised weights to `fit(X, y, sample_weight=...)` in scikit-learn, XGBoost or LightGBM. The reported average uniqueness is also the value AFML §4.4 recommends for a bagging classifier's `max_samples`.

## When NOT to Use

- **As a substitute for purged cross-validation.** Weighting fixes redundancy *inside* a training set. It does nothing about a label whose window straddles a fold boundary and leaks the validation outcome into training — that needs purging and embargoing (op. cit. Ch. 7). Use `hyperparameter-tuning-without-target-leakage` and `walk-forward-validation-setup`. A weighted model scored on unpurged folds is still measuring leakage.
- **On single-bar, non-overlapping labels.** If every label resolves on the bar that generated it, $c_t = 1$ everywhere, every $u_i = 1$, and the weights are uniform. The engine will run and tell you exactly that; there is nothing to correct.
- **As the fix for bootstrap redundancy in bagged learners.** A `RandomForest` still draws each tree's sample IID from the weighted set. Set `max_samples` to the average uniqueness this skill reports, or use sequential bootstrapping (op. cit. §4.4-4.5), which this engine does not implement.
- **With spans expressed in tick, millisecond or timestamp units.** Concurrency is materialised one entry per index covered, so a span of $[1{,}600{,}000{,}000, 1{,}600{,}086{,}400]$ allocates 86,401 entries. Convert to bar ordinals first.
- **As a rebalancing or class-imbalance tool.** Uniqueness weights say nothing about the label distribution. For a rare positive class see `class-imbalance-handling-for-rare-signal-events`; the two weight schemes multiply, and multiplying them re-breaks the $\sum w_i = N$ normalisation unless you re-normalise afterwards.

## Prerequisites

- One `LabelSpan` per training row: `sample_id`, `start_time_idx`, `end_time_idx`, and (for return attribution) `realized_return`.
- **Bar indices, inclusive of both endpoints.** `[1, 5]` and `[6, 10]` do not overlap; `[1, 5]` and `[5, 9]` share bar 5. This matches Snippet 4.1's `count.loc[tIn:tOut] += 1`. A label that resolves on the bar it was opened on is `[t, t]`, not `[t, t-1]` — an inverted span is rejected.
- `sample_id` unique across the set, because weights are joined back onto the training matrix by it.
- For **exact** return attribution: a mapping of bar index → **log** return realised over that bar, covering every bar of every span. Log returns specifically, because attribution sums returns across bars and only log returns are additive. Without this mapping the engine falls back to a documented approximation (see Workflow step 3).
- A weighting method you can defend, and — for `TIME_DECAY` — a `time_decay_last_weight` in $(-1, 1]$.

## Workflow

1. **Build the concurrency map $c_t$**:
   - `compute_concurrency(spans)` increments every bar in $[t_{i,0}, t_{i,1}]$ for each label. Bars no label covers are absent from the map rather than present with a zero.
   - **Decision point — an inverted span is an error, not an empty one.** A span with `end < start` covers no bars, contributes nothing to concurrency, and would then score $u_i = 1.0$: the malformed row would receive the *largest* weight in the dataset. The engine raises instead.

2. **Compute average uniqueness $u_i$**:
   - $u_i = \frac{1}{\tau_i} \sum_{t=t_{i,0}}^{t_{i,1}} \frac{1}{c_t}$, always in $(0, 1]$.
   - **Decision point — the concurrency map must cover every bar of every span.** A missing bar is rejected rather than defaulted to $c_t = 1$; defaulting would report a heavily overlapped label as perfectly unique, which is the exact error this skill exists to prevent.
   - Read the dataset average before going further: at $\bar{u} \approx 0.1$ your 10,000 rows carry roughly the information of 1,000 independent ones, and *no* weighting scheme creates the missing 9,000.

3. **Choose a weighting method**:
   - `UNIQUENESS_ONLY`: $w_i = u_i$. The default, and the right choice when the label is a classification outcome whose magnitude carries no information.
   - `RETURN_ATTRIBUTED`: Snippet 4.10, $w_i = \left| \sum_{t=t_{i,0}}^{t_{i,1}} r_t / c_t \right|$, so a label spanning a violent move outweighs one spanning a quiet drift.
   - **Decision point — supply `bar_log_returns` or accept a labelled approximation.** Without per-bar returns the engine computes $u_i \cdot |r_i|$, which equals the snippet only if the span's per-bar returns are uniform. It is not silently substituted: `report.return_attribution_is_exact` is `False` and the audit notes say `APPROXIMATION`. For a label whose path oscillates, the two differ in both magnitude and ranking.
   - `TIME_DECAY`: $w_i = u_i \cdot d_i$, with $d_i$ the Snippet 4.11 piecewise-linear decay over **cumulative uniqueness** — newest label $d = 1$, oldest tending to `time_decay_last_weight`, clipped at zero. Decay runs on cumulative uniqueness, not calendar time, so a dense cluster of redundant labels ages faster than a sparse run of unique ones.
   - **Decision point — decay follows chronology, not argument order.** The engine sorts spans by $(t_0, t_1)$ internally and maps the factors back to input order, so an unsorted list cannot hand the oldest label the largest weight.

4. **Normalise and hand off**:
   - $w_i \leftarrow w_i \cdot N / \sum_j w_j$, so weights sum to the sample count and the effective learning rate is unchanged relative to unweighted training.
   - **Decision point — weights are returned unrounded.** Rounding them for display breaks $\sum w_i = N$; round at the print site, not in the pipeline.
   - **Decision point — all-zero raw weights are a data failure, not a weighting outcome.** If every label realised exactly zero return under `RETURN_ATTRIBUTED`, the engine logs a WARNING, substitutes uniform weights and sets `degenerate_uniform_fallback`. Treat that flag as a build failure, not a default.
   - Join `normalized_weight` to the training matrix by `sample_id` and pass it as `sample_weight`. Pass the same weights to the scoring function — a weighted fit scored unweighted reintroduces the bias at evaluation time.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating overlapping labels as IID.** Training on raw overlapping labels is the single most common source of a backtest that cannot be reproduced live: the model has effectively seen each price move $1/\bar{u}$ times and reports the resulting memorisation as accuracy.
- **Believing weighting makes the CV honest.** It does not. Weighting and purging address different leaks, and a pipeline with only one of them is still leaking.
- **Weighting the fit but not the score.** `fit(sample_weight=w)` followed by an unweighted `score()` or an unweighted Sharpe means the redundant samples still dominate the number you make decisions on.
- **Time decay applied to an unsorted list.** Any implementation that decays by list position — including this skill's own pre-2.0.0 version — will silently up-weight the oldest data when the caller builds the list newest-first.
- **Reading $u_i \cdot |r_i|$ as Snippet 4.10.** It is the uniform-return approximation to it. On a label whose path swings up then down, the exact attribution can be near zero while $|r_i|$ is large.
- **Letting a NaN return through.** A single non-finite `realized_return` makes the raw-weight sum NaN; `NaN <= 0` is `False`, so the old code normalised anyway and returned an all-NaN weight vector, which most estimators accept without complaint. The engine now rejects non-finite inputs.
- **Duplicate `sample_id`s.** Weights are joined back by id; duplicates mis-assign them silently. Rejected on input.
- **Assuming high uniqueness means a large sample.** $\bar{u}$ tells you the effective sample size is roughly $\bar{u} N$. Use it when judging whether a result is statistically meaningful, not just when setting weights.

## Verification

- **Uniqueness bounds.** Non-overlapping spans `[1,5]`, `[6,10]` $\implies$ every $u_i = 1.0$. Two identical spans $\implies$ $u_i = 0.5$. Three identical spans $\implies$ $u_i = 1/3$ exactly, not `0.3333` (regression: uniqueness was rounded to 4 dp before being used as a weight).
- **Hand-checked partial overlap.** Spans `[0,2]`, `[1,3]`, `[2,4]` give $c = \{1,2,3,2,1\}$, $u = [11/18,\ 4/9,\ 11/18]$ and normalised weights $[1.1,\ 0.8,\ 1.1]$.
- **Exact return attribution.** Spans `[0,2]`, `[1,3]` with $r = \{0.01, 0.02, -0.01, 0.03\}$ give raw weights $|0.01 + 0.01 - 0.005| = 0.015$ and $|0.01 - 0.005 + 0.03| = 0.035$, normalising to $[0.6, 1.4]$ — and differing from the $u_i|r_i|$ approximation in both value and ratio.
- **Time decay.** Three single-bar spans with `time_decay_last_weight=0.5` give factors $[2/3,\ 5/6,\ 1]$ and weights $[0.8,\ 1.0,\ 1.2]$. `time_decay_last_weight=1.0` must reproduce `UNIQUENESS_ONLY` exactly; a negative setting must zero the oldest portion; the newest span's factor must be exactly $1.0$ under every setting.
- **Order independence (regression).** Pass the same spans chronologically and shuffled; per-`sample_id` weights must be identical. The pre-2.0.0 exponential-by-position decay gave the oldest label the *largest* weight under a shuffled list.
- **Normalisation.** $\sum_i w_i = N$ to 12 decimal places for every method.
- **Negative checks.** Empty span list, inverted span, duplicate `sample_id`, non-integer bar index, non-finite `realized_return`, non-finite or missing `bar_log_returns` entry, unknown method string, and `time_decay_last_weight` outside $(-1, 1]$ must each raise `SampleWeightingError` (a `ValueError` subclass).
- Run `python -m unittest discover -s skills/sample-weighting-for-overlapping-labels/scripts` and confirm a 100% pass rate.

## Related Skills

- `synthetic-labels-from-triple-barrier-method`
- `hyperparameter-tuning-without-target-leakage`
- `walk-forward-validation-setup`
- `feature-engineering-without-leakage`
- `label-noise-estimation-in-financial-targets`
- `class-imbalance-handling-for-rare-signal-events`
- `reproducible-ml-training-pipelines`
- `factor-research-multiple-testing-correction`
