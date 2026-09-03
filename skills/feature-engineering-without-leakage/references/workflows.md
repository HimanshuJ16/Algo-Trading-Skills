# Deep Workflow Reference — feature-engineering-without-leakage

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

### 1. Target & Feature Cutoff Specification

- Define the prediction target timestamp explicitly, e.g. $Y_t = \text{sign}(\text{Close}_{t+1} - \text{Close}_t)$, realized only once bar $t+1$ closes.
- Require $t_{\text{feature}} < t_{\text{target realized}}$ for every feature instance.
- This is the **no-time-machine requirement**: a legitimate model builds only "on features with information from a time earlier (or sometimes, no later) than that of the target" (Kaufman et al. 2012, §3.2).
- Record the cutoff in writing before any feature code is written. Every screen below tests *against* this definition; none of them can infer it.

### 2. Point-In-Time As-Of Merge

For multi-frequency joins (daily fundamentals or macro releases onto intraday bars):

```python
merged = FeatureLeakageAuditor.point_in_time_asof_merge(
    left_df=bars, right_df=fundamentals,
    on_timestamp_col="timestamp", by_col="symbol",
)   # allow_exact_matches defaults to False
```

- `pandas.merge_asof(direction='backward')` selects "the last row in the right DataFrame whose 'on' key is **less than or equal to** the left's key", and `allow_exact_matches` defaults to `True`. The wrapper defaults it to `False`, so a record stamped at exactly the decision timestamp is excluded.
- Both frames are sorted on the key first — `merge_asof` requires ascending order.
- The right frame's timestamp must be the **publication/availability** time, not the effective or as-of-business date. Kaufman et al. make timestamps the concrete implementation of legitimacy tagging: "legitimacy tags are time-stamps with sufficient precision" (§4.1).
- Verify a join you did not perform yourself:

```python
findings = FeatureLeakageAuditor.verify_asof_timing(
    joined, decision_time_col="bar_ts", publish_time_col="filed_ts",
)   # -> ASOF_TIMING_VIOLATION findings
```

Unmatched rows (null publication timestamp) are logged, not flagged — but a silently unmatched join is its own defect and should be investigated. Mixing timezone-aware and timezone-naive timestamps raises rather than producing an opaque comparison error; tz confusion is itself a common source of apparent lookahead.

### 3. Structural Causality Screen (run this first)

```python
findings = auditor.audit_feature_causality(raw_df, build_features)
```

Prefix invariance: for a causal pipeline, `build_features(raw_df.iloc[:c])` must equal the first `c` rows of `build_features(raw_df)`. Any difference is proof of a forward read, and is reported as `UNSHIFTED_ROLLING`.

| Construction | Prefix-invariant | Why |
|---|---|---|
| `rolling(w).mean()` | yes | window ends at the current row |
| `expanding().max()` | yes | uses rows $\le t$ only |
| `ffill()` | yes | carries the last known value forward |
| `shift(+k)` | yes | pulls a past value forward |
| `rolling(w, center=True)` | **no** | window straddles the current row |
| `shift(-k)` | **no** | pulls a future value back |
| `bfill()` / `interpolate()` | **no** | fills from later rows |
| whole-sample z-score / rank / scaler fit | **no** | every row carries the full sample's statistics |

Requirements and limits:

- `feature_fn` must be pure and **index-preserving** — the audit raises if the output index differs from the input's, because it cannot otherwise align the comparison.
- Cost is `len(cut_fractions) + 1` invocations of `feature_fn` (default 11).
- **A cut only exposes a forward reach that crosses it.** A `bfill()` over 1-in-7 gaps is invariant at 2 and at 4 cuts and is caught only at 10. Widen `cut_fractions` for irregularly-gapped data.

### 4. Statistical Association Screen

```python
findings = auditor.audit_dataframe(
    df, target_col="target", timestamp_col="timestamp", max_lead_periods=5,
)
```

- **Ordering is verified, not assumed.** Row order *is* the time axis, because every lead is produced by `shift()`. A non-monotonic `timestamp_col` (or index, when no timestamp is given) raises rather than returning a verdict.
- **Lag 0 → `SAME_BAR_CONTAMINATION`**, via whichever of Pearson/Spearman is larger in absolute value, at `same_bar_threshold` (default 0.99); or, for a categorical target (2–10 levels), via perfect separation — rank AUC $\ge$ `separation_threshold` (default 0.999) when binary, disjoint ordered class intervals otherwise. The multi-class path exists because `sign(return)` has three levels whenever a bar closes unchanged, and rank AUC is undefined for it.
- **A constant target raises.** Every correlation against it is NaN, so the audit would otherwise report a leaked feature set as clean.
- **Leads $t+1 \ldots t+k$ → `FUTURE_LOOKAHEAD`** at `correlation_threshold` (default 0.85), reported at the lead with the **largest** absolute association, not the first one over the threshold.
- **`UNDETERMINED`** for columns with fewer than `min_observations` overlapping non-null pairs, or that are constant. These are surfaced as findings precisely so they cannot be mistaken for clean results.
- Non-numeric columns (symbol strings, categoricals) are skipped with an INFO log; datetime columns coerce to integer time and are screened, which is itself a useful check — a feature that separates the target purely by position in time is a leak.

Why three association methods rather than Pearson alone:

| Contamination | Pearson | Spearman | Rank AUC |
|---|---|---|---|
| `feature = target` | 1.00 | 1.00 | — |
| `feature = target ** 3` | ≈0.77 (missed) | **1.00** | — |
| `target = sign(r)`, `feature = 3r` | ≈0.78 (missed) | ≈0.86 (missed) | **1.00** |

For a three-level `sign(r)` label (zeros present), the same detection comes from the ordered-interval check rather than AUC, reported as `method="class_separation"`.

The third row is the flagship failure mode of direction-classification models: a normal variate correlates with its own sign at only $\sqrt{2/\pi} \approx 0.798$, so both correlation measures pass a feature from which the label is exactly recoverable.

### 5. Intentional Leakage Calibration

```python
strength, findings = auditor.run_intentional_leakage_calibration(df, target_col="target")
```

Injects `target.shift(-1)` and returns the strength the auditor **actually reported**. A return of `0.0` means the known leak was missed: the screens are not sensitive enough on this dataset, and their clean verdicts on the real features carry no assurance. Run this before trusting any clean report.

### 6. Shift Direction Verification

`shift(+k)` is a **lag** (row $t$ receives the value from $t-k$); `shift(-k)` is a **lead**, legitimate only when constructing labels. `verify_shift_direction(series, periods, expected_lag=...)` enforces the sign in both directions and rejects a zero shift.

## Known Failure Modes

- **Negative Shift Inversion:** `.shift(-1)` instead of `.shift(1)`, feeding future price moves into current model inputs.
- **Same-Bar Direction Contamination:** predicting $\text{sign}(r_{t+1})$ while a feature carries $r_{t+1}$ under a scaling or monotone transform — invisible to correlation, caught by the separation test.
- **Whole-Sample Preprocessing:** `StandardScaler().fit_transform(X)` over the full history before splitting, so every training row carries the test period's mean and variance.
- **Centred Window Defaults:** `rolling(..., center=True)`, `bfill()`, and `interpolate()` reaching across the current bar.
- **Calendar Date Joins:** joining daily economic indicators by calendar date without the intraday release timestamp — or joining at exactly the release timestamp, which pandas' `merge_asof` default permits.
- **Restated Vendor History:** adjusted closes and backfilled fundamentals are causal *within* the delivered frame, so every screen here passes; only the publication timestamp exposes them.
- **Unchecked High Accuracy:** treating $\ge 95\%$ cross-validation accuracy in financial ML as success rather than as evidence of leakage.
- **Auditing an Unsorted Frame:** lead/lag is positional, so an unsorted frame reports a literal future copy as clean. The auditor now raises instead.

## Production Implementation Reference

- Reference code: `scripts/feature_audit.py` — `FeatureLeakageAuditor` (`audit_dataframe`, `audit_feature_causality`, `point_in_time_asof_merge`, `verify_asof_timing`, `run_intentional_leakage_calibration`), `LeakageFinding`, `LeakageType`, `verify_shift_direction`.
- Automated unit tests: `scripts/test_feature_audit.py`.

## Sources

- Kaufman, S., Rosset, S., Perlich, C., Stitelman, O. "Leakage in Data Mining: Formulation, Detection, and Avoidance." *ACM Transactions on Knowledge Discovery from Data* 6(4), Article 15, December 2012. DOI [10.1145/2382577.2382579](https://dl.acm.org/doi/10.1145/2382577.2382579). §3.2 (no-time-machine requirement), §4.1 (legitimacy tagging / learn-predict separation), §5 (detection methods and their limits).
- pandas, [`pandas.merge_asof`](https://pandas.pydata.org/docs/reference/api/pandas.merge_asof.html) — `direction` and `allow_exact_matches` semantics, ascending-sort requirement.
