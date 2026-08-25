# Deep Workflow Reference — feature-importance-drift-monitoring

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

0. **Establish a comparable baseline**:
   - Record the training-time importance profile in the model registry, pinned to the
     model version and the data window it was computed on, together with the *method*
     used (permutation importance on held-out data, mean $|\text{SHAP}|$, gain).
     Without the method recorded, a future comparison cannot be shown to be
     like-for-like.
   - Fix the recomputation cadence for the live profile (per trading week, per N
     inferences) and keep it fixed. Changing the window length changes the estimator's
     noise level, which moves $\rho$ for reasons that have nothing to do with drift.
   - Include explicit `0.0` entries for features the model does not use. Explainers
     that emit only non-zero features turn a stable model into a feature-set mismatch.

1. **Importance Profile Ingestion & Validation**:
   - Reject non-finite values (`NaN`, `±inf`), negative values, empty maps, and maps
     that are entirely zero.
   - Negative permutation importance means "no better than noise" — clip to `0.0`
     upstream rather than letting a negative value take a rank below the unused
     features.
   - Treat any raised exception as a monitoring failure requiring escalation. It is
     never evidence of stability.

2. **Normalisation to importance shares**:
   $$s_i = \frac{I_i}{\sum_j I_j}$$
   - All magnitude comparisons happen between shares, making the degradation check
     invariant to the absolute scale of the importance metric. This does not make two
     *different* metrics comparable; it only removes the scale term.

3. **Feature-set reconciliation**:
   - Partition into common / baseline-only (dropped from live) / live-only (new in
     live); compute the overlap ratio $|common| / |union|$.
   - Rank correlation is defined only over the common set. Everything outside it is
     drift the coefficient cannot see, which is why the sets are reported explicitly
     and an overlap breach is its own trigger.
   - A top-N baseline feature that is baseline-only is recorded as degraded: it has no
     measurable live importance, and absence must not read as stability.

4. **Mid-rank assignment**:
   - Sort by share descending; rank $1$ is the most important feature. Features with
     equal shares receive the average of the positions they span (three features tied
     across positions 3–5 each receive $4.0$).
   - Mid-ranking is what makes the coefficient independent of dictionary insertion
     order and of feature names.

5. **Tie-corrected Spearman rank correlation**:
   $$\rho_{\text{rank}} = \text{Pearson}\left(R_{\text{base}}, R_{\text{live}}\right)$$
   - This is the *definition* of Spearman's $\rho$. The shortcut
     $1 - \frac{6\sum d_i^2}{M(M^2-1)}$ is an algebraic simplification that holds only
     when all ranks are distinct integers; it is not used here, and the unit tests
     verify the two agree across every untied permutation of five ranks.
   - A constant rank vector on either side makes the correlation undefined (zero
     variance) — raise, do not return $1.0$.
   - Interpretation floor: at $M = 3$ the attainable values are
     $\{-1, -0.5, +0.5, +1\}$; at $M = 4$, the multiples of $0.2$. See
     `references/standards.md` for the exhaustively enumerated random-pass
     probabilities.

6. **Top-N degradation audit**:
   - Rank the top-N over the whole baseline profile, not just the common set.
   - Flag feature $i$ when
     $\frac{s_{\text{live},i}}{s_{\text{base},i}} < 1 - \text{max\_degradation\_drop\_pct}$.
     The comparison is strict, so a drop of exactly the threshold does not trigger.
   - Report `top_n_rank_churn` — how many baseline top-N features are no longer in the
     live top-N — as a diagnostic beside $\rho$. It localises drift to the part of the
     ranking that actually drives predictions, which a whole-set coefficient dilutes
     when most features sit in a noisy near-zero tail.

7. **Alert composition and dispatch**:
   - Collect *all* trigger reasons (rank agreement, degradation, overlap), not only
     the first, so the resulting change request states the full basis.
   - Route the alert to model governance. Do **not** wire it into an automated
     retrain-and-redeploy: retraining an ML component is a change to a live trading
     algorithm and belongs in the firm's change-control process (ESMA supervisory
     briefing ¶30–31, ¶47 — see `references/standards.md`).
   - If window-to-window noise is material, gate the change request on K consecutive
     breaches. The engine is stateless and de-bouncing is the caller's responsibility.

## Production Implementation Reference

- Reference code: `scripts/feature_drift_monitor.py`
  (`FeatureImportanceDriftMonitorEngine`, `FeatureDriftAuditReport`,
  `FeatureRankDetail`).
- Automated unit tests: `scripts/test_feature_drift_monitor.py`, including exhaustive
  agreement with the untied shortcut formula over all permutations of five ranks,
  feature-name invariance under ties, scale invariance across importance metrics, and
  the dropped-top-feature and degradation-boundary cases.
