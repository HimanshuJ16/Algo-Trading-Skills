# Pre-Flight / Sign-off Checklist — feature-selection-stability-across-folds

## Input data
- [ ] The full candidate feature pool is recorded once and is **identical across every fold**.
- [ ] Candidate names are unique — duplicates inflate $M$ and shift $\Phi$ silently.
- [ ] No fold contains a feature absent from the candidate pool.
- [ ] Selected subsets are recorded per fold for $K \ge 5$ folds.
- [ ] The resampling scheme (bootstrap / purged k-fold / walk-forward) is recorded, because it determines how far the confidence interval can be trusted.

## Estimation
- [ ] Inclusion frequency $p_f$ computed for every candidate feature, not only the selected ones.
- [ ] The degenerate cases ($\bar{k} = 0$ or $\bar{k} = M$) are detected and reported as `DEGENERATE_SELECTION`, **never** as $\Phi = 1.0$ / `STABLE_FEATURE_SET`.
- [ ] $\Phi$ computed with the unbiased $s_f^2 = \frac{K}{K-1}p_f(1-p_f)$ and the chance-correction denominator $\frac{\bar{k}}{M}(1-\frac{\bar{k}}{M})$.
- [ ] A negative $\Phi$ is understood as valid (folds agree less than chance) and is checked against the $-\frac{1}{K-1}$ floor.
- [ ] $\Phi$ is compared to the threshold at full precision, not after rounding for display.

## Uncertainty
- [ ] Variance, confidence interval and the one-sided test against $\Phi_{\min}$ are computed, not just the point estimate.
- [ ] It is known whether the verdict is **significant** — a point estimate marginally above the threshold on 5 folds routinely is not.
- [ ] The independence violation from overlapping walk-forward folds is acknowledged: $\Phi$ is optimistic and the interval is too narrow.
- [ ] Fold counts below the recommended minimum of 5 are flagged and not used to gate a promotion.

## Consensus set
- [ ] Required fold count $\lceil p_{\min}K \rceil$ is computed on integers and surfaced in the report.
- [ ] It is understood what the inclusion threshold means at this $K$ (at $K=3$, 80% means all three folds).
- [ ] Unstable features are pruned from the production pipeline, not carried "just in case".
- [ ] Performance is re-validated on a period the selection step never saw — the consensus set already used every fold.

## Scope
- [ ] Stability is being read alongside an out-of-sample performance number, not instead of one.
- [ ] Defaults ($\Phi_{\min} = 0.70$, $p_{\min} = 0.80$, $K \ge 5$, 95% confidence) have been calibrated and the rationale recorded — they are library defaults, not published standards.

## Testing
- [ ] Automated Testing: Run `python scripts/test_feature_stability_analyzer.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
