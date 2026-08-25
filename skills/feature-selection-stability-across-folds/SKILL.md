---
name: feature-selection-stability-across-folds
description: Use when auditing whether a feature selection step (Lasso, Boruta, RFE,
  mutual-information filters) picks the same features across cross-validation folds,
  using the chance-corrected Nogueira stability index and its confidence interval,
  before a consensus feature set is promoted to a production model
domain: algorithmic-trading
subdomain: financial-ml
tags:
- financial-ml
- feature-selection
- nogueira-index
- kuncheva-index
- cross-validation
- fold-stability
- overfitting-control
brokers_frameworks:
- Nogueira Stability Estimator (JMLR 2018)
- Kuncheva Consistency Index
- Python Standard Library
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this when a feature selection algorithm runs inside a cross-validation or walk-forward loop and you need to know whether its output is a property of the *signal* or a property of each *fold*. If Lasso keeps 12 features on fold 1, a mostly different 9 on fold 2, and 15 on fold 3, the union of those sets is not a feature set — it is a record of noise, and a model trained on it has already overfitted before a single hyperparameter was tuned.

This skill computes the **Nogueira stability estimator $\Phi$**, which is corrected for chance (a random selector scores $0$, not "whatever the subset size implies"), together with per-feature inclusion frequencies $p_f$, the estimator's **confidence interval**, and a **one-sided test** of whether $\Phi$ genuinely clears your threshold rather than clearing it by sampling luck.

Notation used here, which differs from the source paper — map it before reading the paper alongside this skill:

| Here | Source paper | Meaning |
|---|---|---|
| $M$ | $d$ | number of candidate features |
| $K$ | $M$ | number of folds / feature sets |
| $\bar{k}$ | $\bar{k}$ | average features selected per fold |

## When NOT to Use

- **As a substitute for out-of-sample performance.** A selector that returns the same arbitrary subset every fold scores $\Phi = 1.0$. Stability bounds how much you can trust a feature set; it says nothing about whether that set predicts anything. Read it beside a performance number, never instead of one.
- **When the candidate pool changes across folds.** $\Phi$ is defined relative to the total number of features the selector *could* have chosen. If an instrument's alternative-data feature only exists after 2019, the folds have different $M$ and the values are not comparable. Fix the pool first, or restrict the audit to the intersection.
- **When the selector returns a ranking, not a subset.** $\Phi$ needs binary selected/not-selected sets. Cutting a ranking at top-$n$ is legitimate, but the resulting $\Phi$ describes *that cut*, and changing $n$ changes it.
- **On a single split.** $K \ge 2$ is a mathematical requirement and $K \ge 5$ is the house minimum: the confidence interval is asymptotic in the number of folds and is not trustworthy below that.
- **As a live drift monitor.** This is an offline validation gate over folds of one training run. For importance shifting in production between retrains, see `feature-importance-drift-monitoring`.

## Prerequisites

- The **full candidate feature pool** of size $M$, identical across every fold, with no duplicate names.
- The selected feature subsets $[S_1, \dots, S_K]$ for $K \ge 5$ folds, each a subset of the candidate pool.
- An inclusion threshold $p_{\min}$ (default $0.80$) and a stability threshold $\Phi_{\min}$ (default $0.70$). **Both are house defaults, not published standards** — the source paper defines the estimator and its sampling distribution but prescribes no cut-off. See `references/standards.md`.
- A confidence level for the interval and test (default $95\%$).

## Workflow

1. **Validate the selection matrix before computing anything.**
   - Reject duplicate candidate names — they inflate $M$ and shift $\Phi$ with no visible error.
   - Reject any feature that appears in a fold but not in the candidate pool: it raises $\bar{k}$ without contributing a $p_f$, silently biasing the estimate.
   - **Decision point — if $K < 5$**, the estimate is still computable but the interval is not dependable. Report the number, flag the fold count, and do not gate a production promotion on it.

2. **Compute inclusion frequencies.**
   - $p_f = \frac{1}{K}\sum_{k=1}^{K}\mathbf{1}_{f \in S_k}$ for every candidate feature.

3. **Detect the degenerate cases before computing $\Phi$.**
   - $\Phi$ is **undefined** when $\bar{k} = 0$ or $\bar{k} = M$ — nothing was selected anywhere, or everything was selected everywhere. The denominator is exactly zero.
   - **Decision point — these are not "perfectly stable".** A selector whose regularisation collapsed to the empty set produces identical (empty) sets in every fold; scoring that $1.0$ passes the gate on a pipeline that has no features to train on. Flag `DEGENERATE_SELECTION` and fix the selection step.

4. **Compute the Nogueira stability index.**
   $$s_f^2 = \frac{K}{K-1}\,p_f(1 - p_f), \qquad \bar{k} = \frac{1}{K}\sum_{k=1}^{K}|S_k|$$
   $$\Phi = 1 - \frac{\frac{1}{M}\sum_{f=1}^{M} s_f^2}{\frac{\bar{k}}{M}\left(1 - \frac{\bar{k}}{M}\right)}$$
   - $\Phi = 1$ if and only if all $K$ feature sets are identical; $\mathbb{E}[\Phi] = 0$ under random selection.
   - $\Phi$ is bounded below by $-\frac{1}{K-1}$, so at $K = 5$ the floor is $-0.25$. **A negative $\Phi$ is a valid reading, not a bug** — the folds agree *less* than chance.

5. **Quantify the uncertainty — do not gate on the point estimate alone.**
   - Variance $v(\Phi)$ from the estimator's asymptotic normality, interval $\Phi \pm z_{1-\alpha/2}\sqrt{v(\Phi)}$, and the one-sided test of $H_0: \Phi = \Phi_{\min}$ against $H_1: \Phi > \Phi_{\min}$.
   - **Decision point — if the point estimate clears $\Phi_{\min}$ but the test does not reject**, you have no evidence of stability, only an estimate that happened to land above the line. At $K=5$ a $\Phi$ of $0.75$ routinely carries a $95\%$ interval reaching below $0.60$. Add folds or widen the resampling before promoting.

6. **Extract the consensus set on exact fold counts.**
   - A feature is consensus when it was selected in at least $\lceil p_{\min} K \rceil$ folds — compared as integers, because $p_f$ is a ratio of small integers and a float comparison at the boundary is not reliable across fold counts.
   - **Decision point — check what the threshold actually means at your $K$.** At $K = 3$, $p_{\min} = 0.80$ silently means "selected in all three folds"; at $K = 5$ it means four.

7. **Re-validate after pruning.** The consensus set was chosen using information from every fold, so any performance figure computed on those same folds is contaminated. Re-estimate performance on a held-out period the selection never saw.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reading an undefined $\Phi$ as a perfect score.** When the selector picks nothing in any fold, the estimator's denominator is zero. Returning $1.0$ there reports "perfectly stable, zero consensus features" — a broken pipeline waved through the one gate that existed to catch it.
- **Gating on the point estimate with no interval.** $\Phi$ is an estimate from $K$ samples, and $K$ is typically 5–10. Comparing $0.71$ to a $0.70$ threshold at that sample size is comparing noise to a constant; the source paper exists in large part to supply the interval and the test that make the comparison meaningful.
- **Passing the union of selected features as the candidate pool.** $\Phi$ is normalised by $M$. Shrinking the pool to only what was selected changes both the numerator and the chance-correction denominator, and the resulting number is not comparable to any other run.
- **Trusting the interval on overlapping walk-forward folds.** The estimator's sampling distribution assumes each fold's selection is an *independent* sample. Walk-forward and purged k-fold splits share training data and are serially dependent, so $\Phi$ is optimistic and the interval is narrower than reality. Treat both as an upper bound on the evidence.
- **Selecting on all folds, then reporting those folds' cross-validated score.** The consensus set already saw every fold. That score is a selection-biased in-sample number no matter how many folds produced it.
- **Using a raw Jaccard or Dice overlap instead.** Uncorrected similarity measures rise systematically with subset size, so a selector that simply keeps more features scores as "more stable" — the effect the chance correction removes.
- **Assuming stability implies utility.** Maximum stability is achievable by a constant selector, including a broken one. It is a necessary condition for a trustworthy feature set, never a sufficient one.
- **Retaining unstable features "just in case".** A feature selected in 1 of 10 folds contributes fold-specific noise to every prediction the model makes in the other nine regimes.

## Verification

- Instantiate `FeatureStabilityAnalyzerEngine(min_nogueira_stability_threshold=0.70, min_inclusion_threshold=0.80)` with 10 candidate features across 5 folds.
  - **Identical selection** (all 5 folds pick `{f1,f2,f3}`): verify $\Phi = 1.0$ exactly, variance $0.0$, `STABLE_FEATURE_SET`, 3 consensus features and 7 pruned.
  - **Erratic selection** (`{f1,f2,f3}`, `{f4,f5,f6}`, `{f7,f8}`, `{f9,f10}`, `{f1,f4,f7}`): verify $\bar{k} = 2.6$ and $\Phi = -94/481 = -0.19543$ — negative, above the $-0.25$ floor for $K=5$ — flagged `UNSTABLE_OVERFITTED_FEATURE_SET` with 0 consensus features.
  - **Marginal selection** (four features in every fold, one dropout, two one-off additions): verify $\Phi = 153/203 = 0.75369$, which clears the 0.70 gate, while the $95\%$ interval reaches down to $0.5823$ and the one-sided test returns $p = 0.2696$ — reported as `STABLE_FEATURE_SET` but **not** significantly above the threshold.
  - **Degenerate selection** (every fold empty, or every fold containing all 10 features): verify the status is `DEGENERATE_SELECTION`, $\Phi$ is `None`, and it is never `STABLE_FEATURE_SET`.
- Negative checks: a feature present in a fold but absent from the candidate pool, a duplicated candidate name, fewer than 2 folds, an empty pool, and out-of-range thresholds must each raise.
- Cross-checks against the source paper: $\Phi$ must equal the value derived from the average pairwise fold intersection (Theorem 1) and must equal Kuncheva's consistency index whenever every fold selects the same number of features (Theorem 5). Both are asserted in the test suite.
- Run `python scripts/test_feature_stability_analyzer.py` and confirm a 100% pass rate.

## Related Skills

- `walk-forward-validation-setup`
- `feature-importance-drift-monitoring`
- `feature-engineering-without-leakage`
- `factor-research-multiple-testing-correction`
