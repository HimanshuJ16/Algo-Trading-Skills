# Pre-Flight Checklist

- [ ] Is EWMA exponential decay weighting active for live strategy correlation recomputation?
- [ ] Is Ledoit-Wolf shrinkage applied to guarantee positive semi-definite correlation matrices?
- [ ] Are high correlation alerts triggered when pairwise strategy correlation $\rho \ge 0.70$?
- [ ] Is portfolio average inter-strategy correlation monitored against breakdown thresholds?