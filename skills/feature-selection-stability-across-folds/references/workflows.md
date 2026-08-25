# Deep Workflow Reference — feature-selection-stability-across-folds

This file holds the full technical procedure referenced by `SKILL.md`. Notation:
$M$ = number of candidate features, $K$ = number of folds, $\bar{k}$ = average number
of features selected per fold. (The source paper writes $d$ and $M$ for the first two.)

## Full Procedure

0. **Record the selection matrix correctly.**
   - Capture the **full candidate pool** once, before any fold runs, and confirm it is
     identical for every fold. $\Phi$ is normalised by $M$; a pool that grows because a
     data source starts mid-history makes the folds incomparable.
   - Capture the selected subset per fold as a set of feature names. Duplicates inside
     a fold are one selection, not two; duplicates in the candidate pool are an error.
   - Record which resampling produced the folds (bootstrap, purged k-fold, walk-forward).
     This determines how far the confidence interval can be trusted — see step 5.

1. **Validate before estimating.**
   - Duplicate candidate names inflate $M$, lowering the chance-correction denominator
     and shifting $\Phi$ with no visible error. Reject them.
   - A feature that appears in a fold but not in the candidate pool contributes to
     $\bar{k}$ but has no $p_f$, so the numerator and denominator are computed over
     different feature sets. Reject it rather than absorbing it.
   - $K \ge 2$ is required by the estimator. $K < 5$ is computable but the interval in
     step 5 is not dependable; flag it and do not gate a promotion on it.

2. **Compute inclusion frequencies.**
   $$p_f = \frac{1}{K}\sum_{k=1}^{K}\mathbf{1}_{f \in S_k}, \qquad f = 1 \dots M$$

3. **Screen for the degenerate cases first.**
   - $\bar{k} = 0$ (nothing selected in any fold) or $\bar{k} = M$ (everything selected
     in every fold) makes the denominator exactly zero, and $\Phi$ is **undefined**.
   - Both are detected on integer totals ($\sum_k |S_k| = 0$ or $= MK$) so the test is
     exact rather than a float comparison.
   - Report `DEGENERATE_SELECTION`. Do **not** report $\Phi = 1.0$: the sets really are
     identical, but the selector has stopped discriminating between features, and the
     stability gate would otherwise pass a pipeline with nothing to train on. The usual
     cause is a regularisation path that collapsed (Lasso $\alpha$ too high) or a
     selection rule whose threshold never binds.

4. **Compute the Nogueira stability index (Definition 4).**
   $$s_f^2 = \frac{K}{K-1}\,p_f(1-p_f), \qquad
     \Phi = 1 - \frac{\frac{1}{M}\sum_{f=1}^{M}s_f^2}{\frac{\bar{k}}{M}\left(1-\frac{\bar{k}}{M}\right)}$$
   - $s_f^2$ carries the $\frac{K}{K-1}$ Bessel correction: it is the *unbiased* sample
     variance of the selection indicator, not the population variance $p_f(1-p_f)$.
   - Interpretation anchors: $\Phi = 1$ iff every fold produced the identical set;
     $\mathbb{E}[\Phi] = 0$ under random selection; $\Phi \ge -\frac{1}{K-1}$, so a
     negative reading down to $-0.25$ at $K=5$ is valid and means the folds agree
     *less* than chance would predict.

5. **Quantify the uncertainty (Theorem 7, Corollary 8, Sec. 4.2.4).**
   - Per-fold influence, with $\text{denom} = \frac{\bar{k}}{M}(1-\frac{\bar{k}}{M})$
     and $k_i = |S_i|$:
     $$\Phi^{(i)} = \frac{1}{\text{denom}}\left[\frac{1}{M}\sum_f z_{i,f}p_f
       - \frac{k_i\bar{k}}{M^2}
       + \frac{\Phi}{2}\left(\frac{2k_i\bar{k}}{M^2} - \frac{k_i}{M} - \frac{\bar{k}}{M} + 1\right)\right]$$
   - Variance: $v(\Phi) = \frac{4}{K^2}\sum_{i=1}^{K}\left(\Phi^{(i)} - \overline{\Phi^{(\cdot)}}\right)^2$.
   - Interval: $\Phi \pm z_{1-\alpha/2}\sqrt{v(\Phi)}$.
   - Test: $V = (\Phi - \Phi_{\min})/\sqrt{v(\Phi)}$, reject $H_0$ when $V \ge z_{1-\alpha}$.
     When $v(\Phi) = 0$ — every fold contributed the same influence, which includes the
     perfectly reproducible case — compare $\Phi$ to $\Phi_{\min}$ directly instead of
     dividing by zero.
   - **The independence caveat is not optional.** The distribution assumes each fold's
     selection is an independent sample. Overlapping walk-forward windows are not, so
     the interval is narrower and $\Phi$ higher than the truth. Widening the gap
     between folds (purging and embargoing) narrows the violation; nothing removes it.

6. **Extract the consensus set.**
   - Required fold count: $\lceil p_{\min}K \rceil$, compared against integer selection
     counts. Comparing $p_f \ge p_{\min}$ as floats is unreliable at the boundary
     because $p_f$ is a ratio of small integers.
   - Surface that number in the report. At $K=3$ an 80% threshold means "all three
     folds"; at $K=5$ it means four. The threshold's real meaning changes with $K$ and
     is invisible when only the percentage is shown.
   - Prune everything below it. A feature selected in 1 of 10 folds injects
     fold-specific noise into every prediction made in the other nine.

7. **Re-validate after pruning.**
   - The consensus set was chosen using information from all $K$ folds. A cross-validated
     score computed on those same folds is selection-biased regardless of fold count.
     Re-estimate on a held-out period the selection step never saw.
   - Re-run the whole audit after any change to the candidate pool, the selector's
     hyperparameters, or the resampling scheme. $\Phi$ is a property of the
     *procedure*, not of the data alone.

## Production Implementation Reference

- Reference code: `scripts/feature_stability_analyzer.py`
  (`FeatureStabilityAnalyzerEngine`, `StabilityStatistics`,
  `FeatureStabilityAuditReport`, `normal_cdf`, `normal_quantile`).
- Automated unit tests: `scripts/test_feature_stability_analyzer.py`, including
  hand-derived closed-form values, the Theorem 1 pairwise-intersection identity, the
  Kuncheva equivalence for constant-cardinality folds, and regression tests for the
  degenerate cases.
- Statuses: `STABLE_FEATURE_SET`, `UNSTABLE_OVERFITTED_FEATURE_SET`,
  `DEGENERATE_SELECTION`. `nogueira_stability_index_phi` is `None` for the last one —
  callers must handle it rather than comparing it to a threshold.
