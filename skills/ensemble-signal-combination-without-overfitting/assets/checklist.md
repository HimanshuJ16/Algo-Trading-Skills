# Pre-Flight / Sign-off Checklist — ensemble-signal-combination-without-overfitting

Use this before considering the skill's implementation complete.

- [ ] **Causal Normalization:** Confirm Z-scores at time $t$ use only observations $t' \le t$; appending future bars must not change earlier normalized values.
- [ ] **Clipping:** Confirm normalized signals are clipped to $[-3.0, +3.0]$ and warm-up bars emit $0.0$.
- [ ] **Finite Inputs:** Confirm NaN/Inf in any signal or target series is rejected at the boundary, not propagated into weights.
- [ ] **Target Alignment:** Confirm `INVERSE_VARIANCE` / `SHRUNK_NNLS` receive a realized forward return series of the same length as the signals.
- [ ] **Train/Apply Separation:** Confirm weights are fitted on a training window and applied to a later, unseen window.
- [ ] **Non-Negative Weights:** Confirm $w_i \ge 0$ for all sub-models.
- [ ] **1/N Shrinkage:** Confirm shrinkage parameter $\lambda \in [0, 1]$ (default $0.50$) blends weights toward equal allocation.
- [ ] **Weight Sum Normalization:** Confirm $\sum w_i = 1.0$.
- [ ] **Effective Weight Cap:** Confirm $\max_i w_i \le \max(\text{cap}, 1/N)$, and that the configured cap is feasible for the model count.
- [ ] **Weight Stability:** Confirm weight vectors do not flip materially between adjacent walk-forward refits.
- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/ensemble-signal-combination-without-overfitting/scripts` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
