# Pre-Flight / Sign-off Checklist — feature-importance-drift-monitoring

## Baseline provenance
- [ ] Baseline feature importances are recorded in the model registry, pinned to the model version and the data window they were computed on.
- [ ] The importance *method* (permutation / mean $|\text{SHAP}|$ / gain) is recorded, and the live profile uses the same one.
- [ ] Baseline and live importances are computed on comparable held-out data, not one on train and one on production.
- [ ] Correlated features are clustered and represented by one monitored feature, or the resulting rank churn is knowingly accepted.

## Input hygiene
- [ ] Live importances are recomputed on a fixed cadence and a fixed window length.
- [ ] Features the model no longer uses are supplied as explicit `0.0`, not omitted.
- [ ] Negative permutation importances are clipped to `0.0` before the audit.
- [ ] Non-finite (`NaN`/`inf`), negative, empty and all-zero profiles are rejected, not ranked.

## Metric correctness
- [ ] Importances are normalised to shares of their own profile before any magnitude comparison.
- [ ] Ranks are mid-ranks, so tied importances do not make the coefficient depend on feature names or dictionary order.
- [ ] $\rho_{\text{rank}}$ is the Pearson correlation of the rank vectors, not the untied $1 - 6\sum d^2 / (M(M^2-1))$ shortcut.
- [ ] The number of common features is large enough for the threshold to mean anything (at $M = 4$ a random reordering passes 0.70 one time in six).

## Detection coverage
- [ ] Dropped and newly-appearing features are reconciled and reported, not silently intersected away.
- [ ] A top-N baseline feature missing from the live profile raises an alert rather than a stable reading.
- [ ] The top-N share-degradation check runs alongside the rank correlation, and `top_n_rank_churn` is reviewed with $\rho$.
- [ ] Distribution-drift monitoring (PSI/KS) runs as a complementary detector, not a substitute.

## Thresholds and response
- [ ] $\rho_{\text{min}}$ and the degradation percentage have been calibrated against this model's own healthy-period distribution, and the rationale is recorded — they are library defaults, not standards.
- [ ] Nobody has documented the 0.70 threshold as a regulatory requirement; no regulator publishes one.
- [ ] A raised exception from the audit is escalated as a monitoring failure, never swallowed and treated as a pass.
- [ ] Alert de-bouncing (K consecutive breaches) is configured if window-to-window noise is material.
- [ ] The alert opens a change request; it does not trigger automated redeployment. Retraining an ML component is a material change requiring testing, approval and a timestamped record.
- [ ] Audit reports are retained as evidence for periodic model validation (EU investment firms: RTS 6 Article 9 annual self-assessment).

## Testing
- [ ] Automated Testing: Run `python scripts/test_feature_drift_monitor.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
