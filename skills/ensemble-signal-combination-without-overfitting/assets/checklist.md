# Pre-Flight / Sign-off Checklist — ensemble-signal-combination-without-overfitting

Use this before considering the skill's implementation complete.

- [ ] **Signal Normalization:** Confirm sub-model signals are converted to Z-scores and clipped to $[-3.0, +3.0]$.
- [ ] **Non-Negative Weights:** Confirm $w_i \ge 0$ for all sub-models.
- [ ] **1/N Shrinkage:** Confirm shrinkage parameter $\lambda = 0.50$ blends weights toward equal allocation.
- [ ] **Weight Sum Normalization:** Confirm $\sum w_i = 1.0$.
- [ ] **Automated Testing:** Run `python scripts/test_ensemble_combiner.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
