---
name: feature-engineering-cost-benefit-tracking
description: >-
  Use when a feature pipeline has accumulated expensive inputs with negligible marginal
  accuracy; weighs each feature's permutation importance against its licensing cost,
  compute cost and inference latency before pruning.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: financial-ml
  tags: financial-ml, feature-cost-benefit, permutation-importance, shapley-value, cost-benefit-pruning, feature-selection, inference-latency-budget
  brokers_frameworks: "Feature Cost-Benefit Tracker Engine; scikit-learn; SHAP"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill when optimizing an ML feature pipeline for production deployment. Production pipelines accumulate feature bloat: dozens of expensive features (high-latency order-book depth metrics, alternative-data API subscriptions, multi-resolution convolutions) that add negligible marginal accuracy while inflating infrastructure spend and inference latency. This skill scores each feature's marginal contribution against its monthly cost and its share of the inference latency budget, and returns a `KEEP` / `REVIEW` / `PRUNE` verdict per feature plus a *realizable* savings figure for the pipeline.

## When NOT to Use

- **Feature *selection* during model fitting.** This skill audits an already-fitted model's feature set. Choosing features as part of training (and validating that choice across folds) belongs to `feature-selection-stability-across-folds`.
- **Detecting that a feature's importance has *changed*.** A drop in importance over time is drift, not a cost-benefit question — see `feature-importance-drift-monitoring`.
- **When importance was measured on the training set.** Training-set importance measures what the model fitted, not what generalizes. Re-measure on a held-out set before pruning anything.
- **When you cannot attribute cost to features at all.** With no cost data every verdict reduces to a bare importance threshold, which is a feature-selection problem, not a cost-benefit one.
- **As a substitute for a leakage audit.** A leaky feature shows *high* importance and will be kept enthusiastically. Run `feature-engineering-without-leakage` first.

## Prerequisites

- Per-feature importance scores measured on a **held-out** set, as a **fraction** of the model's score (`0.05` = a 5 percentage-point score drop when the feature is permuted) — the same scale as the configured threshold.
- Optionally, the standard deviation of each importance across permutation repeats (`n_repeats` in scikit-learn), which enables the confidence gate below.
- Monthly cost per feature in USD. For features billed together on one vendor licence, the *attributed share* of that licence, plus a shared `cost_pool` tag.
- Optionally, measured per-inference compute latency in milliseconds, and a `group` tag for each cluster of correlated features.

## Workflow

1. **Quantify marginal feature value.** Compute importance $I_i$ on held-out data. Permutation importance is the drop in score when feature $i$ is shuffled, averaged over `n_repeats` shuffles; record the standard deviation $\sigma_i$ across those shuffles. A **negative** $I_i$ is legitimate — shuffling a pure-noise feature can improve the score — and is a strong prune signal, not an input error.

2. **Assign feature cost.** Record monthly licensing + compute cost $C_i$ (USD/month) and per-inference latency $L_i$ (ms). Where several features are billed on one licence, split the cost across them and tag them with a shared `cost_pool`: cancelling one feature on a shared licence saves nothing.

3. **Compute the feature ROI ratio** as a *ranking* statistic:
   $$\text{ROI}_i = \frac{100 \cdot I_i}{\max(F, C_i)}, \qquad F = \text{roi\_cost\_floor\_usd} = \$1.00$$
   Units are importance percentage points per USD/month. The denominator is floored so a near-free feature does not report unbounded ROI; consequently ROI is **flat below the floor** and must not be compared across features that both cost less than $F$. ROI ranks `REVIEW` candidates for human triage — it does **not** decide the verdict.

4. **Apply the decision rules, in this order.** The first matching rule wins:
   - $I_i < I_{\min}$ but $I_i + k\sigma_i \ge I_{\min}$ (default $k = 1$) → **`REVIEW`**: the estimate is not distinguishable from the threshold. Re-measure with more shuffles rather than pruning on noise.
   - $I_i + k\sigma_i < I_{\min}$ **and** ($C_i > C_{\max}$ **or** $L_i > L_{\max}$) → **`PRUNE`**. Latency alone is sufficient: a $0/month feature that eats 18 ms of a 2 ms budget is expensive.
   - $I_i < I_{\min}$ with cost and latency inside budget → **`REVIEW`** (low value, but cheap to carry).
   - $I_i \ge I_{\min}$ but $L_i > L_{\max}$ → **`REVIEW`**: optimize the computation or accept the latency explicitly.
   - $I_i \ge I_{\min}$ but $C_i > C_{\text{high}}$ (default \$500/mo) → **`REVIEW`**: confirm the marginal P&L covers the licence before renewal.
   - Otherwise → **`KEEP`**.

5. **Apply the correlation-dilution guard before finalizing any prune.** When two features are correlated and one is permuted, the model still reads the signal through the other, so *both* report a depressed importance. A member of a `group` is downgraded from `PRUNE` to `REVIEW` while the group's aggregate importance clears $I_{\min}$: decide the cluster jointly — drop every member, or keep one representative.

6. **Realize savings only where they are actually realizable.** A pruned feature's cost counts toward the savings figure only if it has no `cost_pool`, or if *every* member of its pool is also pruned. Latency, unlike a licence, is reclaimed per feature.

> Full procedure with worked rule ordering: see `references/workflows.md`.
> Verdict table, threshold-calibration guidance, and sources: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Evaluating correlated features in isolation.** Permutation importance is *diluted* across correlated features: permute one, and the model recovers the signal from its twin, so both look unimportant and both get pruned — destroying a signal that neither showed alone. Tag the cluster with a `group` and judge it in aggregate.
- **Summing permutation importances as if they were additive.** They are not: permutation importance is a marginal quantity, so a group total is a conservative heuristic, not a decomposition. SHAP values *are* additive by construction (local accuracy), so aggregate over those where the choice exists.
- **Treating a point estimate as the truth.** Permutation importance is an average over random shuffles with real dispersion. Pruning a feature whose importance is $0.008 \pm 0.009$ against a $0.01$ threshold is pruning noise. Supply `importance_std` and let the confidence gate hold the verdict.
- **Ignoring inference latency as a cost.** A feature can be free in dollars and ruinous in milliseconds. Configure `max_latency_ms` — otherwise the latency field is recorded and never acted on.
- **Claiming savings on a shared licence.** Pruning one of six features from a \$2,500/month vendor feed saves \$0 until the other five go too. Use `cost_pool` so the report separates realizable savings from attributed cost.
- **Mixing percent and fraction scales.** A threshold of `0.01` compared against importances expressed as `5.0` for "5%" keeps every feature silently. The tracker logs a warning on this pattern; do not ignore it.
- **NaN importance from a broken importance run.** Every `<` comparison against NaN is False, so an unvalidated NaN falls through every prune branch and is reported as `KEEP` — the worst possible default. Inputs are rejected at the boundary instead.
- **Measuring importance on the training set.** It reflects what the model fitted, not what generalizes; features that matter in-sample and not out-of-sample are exactly what should be pruned.
- **Treating the default thresholds as standards.** \$50/mo, \$500/mo, and 1 percentage point are illustrative starting points. Calibrate them against the strategy's P&L per unit of model score.

## Verification

- Submit features spanning the verdict space and confirm each rule fires: high-importance/cheap → `KEEP`; low-importance/expensive → `PRUNE`; low-importance/cheap → `REVIEW`; high-importance/very-expensive → `REVIEW`.
- Verify boundary behavior: $I_i = I_{\min}$ is `KEEP` (the rule is strict `<`), and $C_i = C_{\max}$ is within budget.
- Verify a NaN or infinite importance/cost raises `FeatureCostBenefitError` rather than returning a verdict.
- Verify a negative importance is accepted and treated as a prune signal.
- Verify a zero-dollar feature that breaches `max_latency_ms` is pruned on latency alone.
- Verify a correlated pair, each individually below threshold but jointly above it, is downgraded to `REVIEW` and contributes \$0 to savings.
- Verify a partially pruned `cost_pool` yields \$0 realizable savings and says so in the report message.
- Verify `kept_features_count + review_features_count + pruned_features_count == total_features_analyzed`.
- Verify `ROI` against an independently computed value: $I = 0.05$, $C = \$250$ gives $100 \times 0.05 / 250 = 0.02$.
- Run `python -m unittest discover -s skills/feature-engineering-cost-benefit-tracking/scripts` and confirm a 100% pass rate.

## Related Skills

- `feature-importance-drift-monitoring`
- `feature-selection-stability-across-folds`
- `feature-engineering-without-leakage`
- `model-inference-latency-budget-for-live-trading`
- `market-data-cost-optimization-tiered-subscriptions`
