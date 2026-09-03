---
name: feature-engineering-without-leakage
description: >-
  Use when designing or auditing features for a model that predicts a future price move,
  to guarantee each feature is computable strictly before the target is realised;
  catches same-bar returns and label-derived inputs.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: financial-ml
  tags: financial-ml, data-leakage, point-in-time, feature-auditing, lookahead-bias
  brokers_frameworks: "Feature Leakage Auditor; Python pandas; Python NumPy"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this while designing or auditing the feature set for any ML model that predicts a future price move, direction, or trading signal. Target leakage in financial ML is especially easy to introduce because many natural-seeming features (same-bar returns, same-bar volatility, labels derived from a window that overlaps the feature window) contain information that is only knowable after the outcome you're trying to predict — a model trained on leaked features shows excellent backtest/validation accuracy and then performs at or below random in live inference, because the leaked information simply doesn't exist yet at real prediction time.

The governing rule is Kaufman et al.'s **no-time-machine requirement**: a feature is legitimate only if "every instance is observed earlier than its ... related target instance" (ACM TKDD 6(4), §3.2, condition 3).

## When NOT to Use

- **As a substitute for point-in-time data capture.** Kaufman et al. reach the opposite conclusion from the one this skill's tooling might suggest: because every detection method "require[s] some degree of domain knowledge," they "place an emphasis on leakage avoidance during data collection, where we have more control over the data" (§5). Timestamp every observation with its *publication* time first (see `point-in-time-database-for-ml-training-data`); audit second.
- **As proof that a feature set is clean.** All three screens here are candidate filters. A clean report means no screen fired — see *What This Audit Cannot Detect* below.
- **For train/test-split leakage.** Contaminated *examples* (overlapping labels, hyperparameters tuned across the split, features selected on the full sample) are a different failure mode — see `walk-forward-validation-setup`, `hyperparameter-tuning-without-target-leakage`, and `sample-weighting-for-overlapping-labels`.
- **For backtest execution-timing bias.** Filling a signal at the same bar's close, or ignoring order latency, is a backtest defect rather than a feature defect — see `lookahead-bias-elimination`.
- **On a frame whose row order is not the time order and carries no timestamp.** Every lead/lag is computed by row position. `audit_dataframe` raises rather than returning a verdict it cannot justify, but a frame that was shuffled and *then* re-indexed is indistinguishable from a sorted one and will audit clean.

## Prerequisites

- A precise, written definition of the prediction target (exact timestamp the target is realized, e.g. "direction of close-to-close return from bar T to bar T+1") separate from the feature computation cutoff.
- A **publication/availability timestamp** — not just an effective date — on every non-price data source being joined.
- Enough observations for correlation to mean anything: the auditor's `min_observations` floor (default 30) governs when it reports UNDETERMINED instead of a verdict.
- Feature construction expressed as a **function of a raw frame** (`raw_df -> features`), not as in-place mutation, so the causality screen can re-run it on a truncated input.

## Workflow

1. **Write the target definition first, in timestamps.** "Sign of return from close(T) to close(T+1)" and "…to close(T+5)" impose different cutoffs. Every feature's cutoff is defined relative to whichever is chosen.

2. **State each feature's knowability timestamp** and confirm it precedes the cutoff, which itself precedes the target's realization. This is the only step that actually establishes legitimacy; the screens below only look for evidence that it was violated.

3. **Join multi-frequency sources by publication timestamp** via `point_in_time_asof_merge()`.
   - **Decision point — an exactly-simultaneous record is a leak by default.** `pandas.merge_asof(direction='backward')` matches on "less than *or equal to*" (`allow_exact_matches=True`). This wrapper defaults it to `False`, so a fundamental filed at exactly the bar timestamp is *not* attached. Set it to `True` only when the right frame's timestamps are already receipt times offset for dissemination latency — never merely to reduce nulls.
   - For data joined upstream by someone else, verify after the fact with `verify_asof_timing()`, which returns `ASOF_TIMING_VIOLATION` findings.

4. **Run the structural causality screen — `audit_feature_causality()` — before the statistical one.** A causal pipeline computes row *t* from rows ≤ *t*, so truncating the raw input after row *c* must leave rows 0..*c* of the output bit-identical. Any difference proves the pipeline read forward.
   - **Decision point — this is the screen that catches what correlation cannot.** Centred rolling windows, `shift(-k)`, `bfill()`, and whole-sample normalisation all leak while correlating with a noisy return target at levels far below any usable threshold. Only prefix invariance sees them.
   - **Decision point — a clean result at the default cuts is not proof.** A cut only exposes a forward reach that *crosses that cut*. A pipeline that looks forward at sparse, irregular rows can be invariant at every cut tried. Widen `cut_fractions` when the raw data has gaps.

5. **Run the statistical association screen — `audit_dataframe()`** — passing `timestamp_col` so row ordering is verified rather than assumed. It reports:
   - `SAME_BAR_CONTAMINATION` — the feature is a copy of the target, detected by Pearson **or Spearman** (a monotone copy such as `target ** 3` has Pearson ≈ 0.77 and Spearman exactly 1.0) **or**, for a categorical target, by perfect separation: rank AUC when the label is binary, disjoint ordered class intervals when it has 3–10 levels.
   - **Decision point — `sign(return)` usually has three levels, not two.** Any bar closing unchanged adds a zero class, which is why the separation test must not be binary-only. A constant target raises, since every correlation against it is NaN and the audit would otherwise call a leaked feature set clean.
   - `FUTURE_LOOKAHEAD` — association with the target at leads t+1..t+k, reported at the lead with the **largest** absolute association.
   - `UNDETERMINED` — too few overlapping observations, or a constant column. **Read these; they are not clean results.**

6. **Calibrate before trusting any clean verdict.** `run_intentional_leakage_calibration()` injects a known leak and returns the strength the auditor actually detected — **0.0 if it missed it**. A returned 0.0 means the screens are not sensitive enough for this dataset and their clean verdicts on the real features carry no assurance.

7. **Test shift direction on a hand-verifiable example.** `verify_shift_direction(series, periods, expected_lag=True)` asserts positive periods for features and negative for labels. Off-by-one `shift()` errors produce a model that looks superb because it is reading the answer.

> Full procedure: see `references/workflows.md`.
> Standards, citations, and audit limitations: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## What This Audit Cannot Detect

Stated explicitly because a sign-off checklist invites the opposite reading:

- **Weak but real leakage.** Financial targets are near-unpredictable, so a genuinely leaked feature can sit well under `correlation_threshold` and pass. The screens catch copies and structural violations, not every leak.
- **A leak present in every row of the raw data.** If the vendor's "historical" field was itself restated after the fact (adjusted close, backfilled fundamentals), the pipeline is perfectly causal *over that raw frame* and prefix-invariance passes. Only the publication timestamp reveals it.
- **Leakage across the train/test boundary** — that is `walk-forward-validation-setup`'s domain.
- **Order destroyed before the audit.** A shuffled-then-reindexed frame audits clean.

## Common Pitfalls

- **Reading zero findings as "no leakage."** Zero findings means no screen fired. Kaufman et al. present EDA, surprising performance, and field testing as ways of *filtering leakage candidates*, all requiring domain knowledge (§5) — not as proofs of absence.
- **Auditing an unsorted frame.** Lead/lag is computed by row position, so a shuffled frame reports a literal copy of next period's target as clean. Always pass `timestamp_col`.
- **Predicting the sign of a return while a feature carries the return.** Pearson between a normal variate and its own sign is only √(2/π) ≈ 0.798 — far under any same-bar threshold — yet the label is exactly recoverable. This is why the separation test exists, and why it must handle the three-level `{-1, 0, +1}` label that real return series produce rather than binary labels only.
- **Off-by-one `shift()`/`lag()` errors** silently turning a lagged feature into a leaked one.
- **Using adjusted close prices** as a same-day feature: the adjustment is applied retroactively to historical rows and encodes corporate actions not known at that date. See `adjusted-vs-unadjusted-price-series-pitfalls`.
- **Fitting a scaler, ranker, or imputer on the whole sample.** Every training row then carries the test period's mean and variance. Correlation cannot see this; prefix invariance can.
- **Centred or forward-looking window defaults** (`rolling(..., center=True)`, `bfill()`, `interpolate()`) reaching across the current bar.
- **Joining daily fundamentals or macro releases by calendar date** rather than publication timestamp — and then attaching a record stamped at exactly the decision time.
- **Treating unusually high validation accuracy as good news** rather than as the first thing to be suspicious of. Kaufman et al. cite the INFORMS 2010 financial forecasting results as leakage precisely because they "contradict prior evidence about the efficiency of the stock market" (§5).

## Verification

- **Association screen.** Build 400 rows of `target = pct_change().shift(-1)`. Confirm `FeatureLeakageAuditor().audit_dataframe(df, "target", timestamp_col="timestamp")` returns no findings for `shift(1)`/`shift(5)` features, and `SAME_BAR_CONTAMINATION` for `target ** 3` with `method="spearman"` and `correlation_value == 1.0` exactly (cubing preserves ranks).
- **Separation test.** With `target = sign(next return)` and `feature = 3 × next return`, confirm one `SAME_BAR_CONTAMINATION` finding with `method="rank_auc"` and `correlation_value == 1.0`, while both Pearson and Spearman against the same pair stay below 0.99. Repeat with a return series containing exact zeros so the label has three levels: the finding must still appear, with `method="class_separation"`.
- **Argmax lead.** With `feature = 0.5·Y(t+1) + 0.9·Y(t+3)` over iid standard normals, closed-form lead correlations are 0.486 and 0.874; at `correlation_threshold=0.40` both cross, and `max_correlation_lead` must be **3**, not 1.
- **Ordering.** Confirm that shuffling that frame makes `audit_dataframe` **raise**, not return `[]`.
- **Causality screen.** Confirm `rolling(5).mean()`, `expanding().max()`, and `ffill()` are prefix-invariant, while `rolling(5, center=True).mean()`, `shift(-1)`, `bfill()`, and a whole-sample z-score each produce `UNSHIFTED_ROLLING`.
- **Point-in-time join.** Confirm a right-frame record stamped at exactly the trade timestamp is **not** attached by default, and is attached under `allow_exact_matches=True`.
- **Calibration.** Confirm `run_intentional_leakage_calibration` returns `1.0` on a normal dataset and `0.0` — not `1.0` — when the injected leak cannot be screened.
- **Walk-forward gap.** Confirm out-of-sample performance (see `walk-forward-validation-setup`) is reasonably close to in-sample; a large unexplained gap after this audit indicates residual leakage.
- Run `python -m unittest discover -s skills/feature-engineering-without-leakage/scripts` and confirm a 100% pass rate.

## Related Skills

- `point-in-time-database-for-ml-training-data`
- `walk-forward-validation-setup`
- `lookahead-bias-elimination`
- `hyperparameter-tuning-without-target-leakage`
- `adjusted-vs-unadjusted-price-series-pitfalls`
- `offline-train-online-infer-deployment`
