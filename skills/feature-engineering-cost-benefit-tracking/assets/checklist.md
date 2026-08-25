# Pre-Flight / Sign-off Checklist — feature-engineering-cost-benefit-tracking

Use this before acting on any pruning recommendation.

## Inputs

- [ ] **Held-out measurement:** Confirm importance was measured on a held-out set, not the training set.
- [ ] **Units:** Confirm importance is a fraction of the model score on the same scale as `min_importance_threshold` (`0.05` = 5 percentage points, not `5.0`). Check the log for a unit-mismatch warning.
- [ ] **Finite inputs:** Confirm no NaN/Inf importance or cost reaches the tracker — a NaN falls through every `<` comparison and would otherwise be reported as `KEEP`.
- [ ] **Dispersion recorded:** Confirm `importance_std` is supplied from the permutation repeats wherever the confidence gate is wanted; without it the gate is inert.
- [ ] **Cost attribution:** Confirm monthly cost is assigned per feature, and that features billed on one vendor licence share a `cost_pool` with the licence cost split across them.
- [ ] **Correlation clusters tagged:** Confirm correlated features carry a shared `group` so the dilution guard can fire.
- [ ] **Latency measured:** Confirm `compute_latency_ms` is measured, not estimated, and that `max_latency_ms` is set from the strategy's tick-to-trade budget if latency is to be enforced at all.

## Configuration

- [ ] **Thresholds calibrated:** Confirm `min_importance_threshold`, `max_acceptable_cost_usd`, and `high_cost_review_usd` were calibrated for this strategy — the defaults are illustrative, not standards.
- [ ] **Noise floor:** Confirm `min_importance_threshold` sits above the importance a known-random feature scores on this model.
- [ ] **Threshold consistency:** Confirm `high_cost_review_usd >= max_acceptable_cost_usd` (the constructor rejects the inverse).

## Outputs

- [ ] **Verdicts reconcile:** Confirm `kept + review + pruned == total_features_analyzed`.
- [ ] **Dilution guard:** Confirm no member of a correlated `group` whose aggregate importance clears the threshold is marked `PRUNE`.
- [ ] **Realizable savings:** Confirm `potential_monthly_savings_usd` excludes shared licences that are only partially pruned, and that any unrealizable amount is understood.
- [ ] **Latency savings:** Confirm reclaimed latency is reported and reconciles against the inference budget.
- [ ] **ROI used correctly:** Confirm ROI is used only to rank `REVIEW` candidates, and not compared between two features that both cost less than `roi_cost_floor_usd`.

## After Pruning

- [ ] **Refit and re-measure:** Confirm the model was refitted on the reduced feature set and importances re-measured — surviving features' importances change once a diluting neighbor is removed.
- [ ] **Out-of-sample re-validation:** Confirm strategy performance was re-validated out-of-sample before the reduced feature set was deployed.
- [ ] **Change control:** Confirm the feature-set change is recorded as a model change (see `model-card-documentation-for-trading-models`, `model-versioning-and-rollback`).
- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/feature-engineering-cost-benefit-tracking/scripts` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (research/paper/live): ___________________________
