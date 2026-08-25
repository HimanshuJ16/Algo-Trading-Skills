# Deep Workflow Reference — feature-engineering-cost-benefit-tracking

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

### 1. Quantify marginal feature value

Compute importance $I_i$ on a **held-out** set, never the training set. Training-set
importance measures what the model fitted; a feature that is important in-sample and
worthless out-of-sample is precisely what this audit should remove.

For permutation importance, $I_i$ is the mean drop in the model's score when feature
$i$ is shuffled, averaged over `n_repeats` shuffles. Record the standard deviation
$\sigma_i$ across those repeats — it is the input to the confidence gate in step 4.

Two properties matter here:

- **$I_i$ may be negative.** Shuffling a pure-noise feature can *improve* the held-out
  score. This is legitimate output, not an error, and is a strong prune signal.
- **$I_i$ is not additive.** Permutation importance is marginal: two features'
  importances do not decompose the model's total score. SHAP values are additive by
  construction (local accuracy), so prefer them wherever a group total must be taken
  literally rather than as a guard.

### 2. Assign feature cost

Record for each feature:

- $C_i$ — monthly data-licensing plus compute cost, USD/month.
- $L_i$ — measured per-inference compute latency, milliseconds.
- `cost_pool` — an identifier shared by every feature billed on one vendor licence.
  When several features come from one feed, split that feed's monthly cost across
  them and tag them all with the same pool.
- `group` — an identifier shared by every feature in a correlated cluster.

`group` and `cost_pool` are deliberately separate axes. One vendor feed can supply two
statistically unrelated features, and two highly correlated features can arrive from
different vendors. Collapsing them into one tag produces wrong answers on both.

### 3. Compute the feature ROI ratio

$$\text{ROI}_i = \frac{100 \cdot I_i}{\max(F, C_i)}, \qquad F = \$1.00/\text{mo}$$

Units: importance percentage points per USD per month. The floor $F$ keeps the ratio
bounded for free and near-free features; the consequence is that ROI is **constant**
for every feature costing less than $F$, so ROI must not be used to rank two features
that are both effectively free.

ROI is a reporting and triage statistic. The verdict is decided by the explicit rules
in step 4, not by a threshold on ROI — there is no defensible universal ROI cutoff,
because the numerator's units depend entirely on the model's scoring function.

### 4. Apply the decision rules

Evaluated in order; the first match wins. Let $I_{\min}$ = `min_importance_threshold`,
$C_{\max}$ = `max_acceptable_cost_usd`, $C_{\text{high}}$ = `high_cost_review_usd`,
$L_{\max}$ = `max_latency_ms`, $k$ = `prune_confidence_sigma`.

| # | Condition | Verdict |
|---|---|---|
| 1 | $I_i < I_{\min}$ and $I_i + k\sigma_i \ge I_{\min}$ | `REVIEW` — estimate not distinguishable from the threshold |
| 2 | $I_i + k\sigma_i < I_{\min}$ and ($C_i > C_{\max}$ or $L_i > L_{\max}$) | `PRUNE` |
| 3 | $I_i < I_{\min}$, cost and latency within budget | `REVIEW` — low value, cheap to carry |
| 4 | $I_i \ge I_{\min}$ and $L_i > L_{\max}$ | `REVIEW` — useful but over the latency budget |
| 5 | $I_i \ge I_{\min}$ and $C_i > C_{\text{high}}$ | `REVIEW` — useful but very expensive |
| 6 | otherwise | `KEEP` |

Notes on the boundaries:

- The importance test is strict: $I_i = I_{\min}$ is **not** below the threshold and
  reaches `KEEP`.
- The cost test is strict: $C_i = C_{\max}$ is **within** budget.
- When $\sigma_i$ is not supplied, rule 1 can never fire and the confidence gate in
  rule 2 collapses to $I_i < I_{\min}$. Behavior then reduces to plain thresholds.
- Rule 2's confidence gate is a one-sided normal approximation used as a guard against
  pruning on noise. It is not a formal hypothesis test and makes no multiple-testing
  correction across the feature set — for that, see
  `factor-research-multiple-testing-correction`.

### 5. Correlation-dilution guard

Applied by `audit_pipeline` after every feature has a provisional verdict, because it
needs the whole feature set:

For each `group` with two or more members, compute the aggregate importance
$\sum_{i \in g} I_i$. If that aggregate is at or above $I_{\min}$, every member
currently marked `PRUNE` is downgraded to `REVIEW`.

The rationale: permuting one of two correlated features leaves the model able to read
the signal through the other, so both report a depressed importance and both get
pruned individually — deleting a signal that neither showed alone. The guard forces
the cluster to be decided jointly: drop every member, or keep one representative and
drop the rest.

`evaluate_feature` alone cannot apply this guard — it sees one feature at a time, so
any `PRUNE` it returns for a grouped feature is provisional.

### 6. Realize savings

- A pruned feature with no `cost_pool` contributes its full cost to
  `potential_monthly_savings_usd`.
- A pruned feature in a `cost_pool` contributes its cost only if **every** member of
  that pool is also pruned. Otherwise the licence must still be paid and the amount is
  reported separately as attributed-but-unrealizable.
- Latency is reclaimed per feature regardless of pools: `pruned_latency_ms` sums the
  latency of every pruned feature.

### 7. Re-measure after pruning

Importance is a property of the *fitted model*, not of the feature. After dropping
features, refit and re-measure: the surviving features' importances will change,
sometimes sharply where a dropped feature was diluting them. Treat pruning as
iterative, and re-validate strategy performance out-of-sample before deploying the
reduced feature set.

## Production Implementation Reference

- Reference code: `scripts/feature_cost_benefit.py`
  (`FeatureCostBenefitTracker`, `FeatureCostBenefitRecord`, `FeatureCostBenefitReport`,
  `Recommendation`, `FeatureCostBenefitError`).
- Automated unit tests: `scripts/test_feature_cost_benefit.py`. Run with
  `python -m unittest discover -s skills/feature-engineering-cost-benefit-tracking/scripts`.
