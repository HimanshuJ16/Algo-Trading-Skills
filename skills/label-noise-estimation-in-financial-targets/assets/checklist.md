# Pre-Flight Checklist

- [ ] Are out-of-fold predicted class probabilities used (purged/embargoed CV if labels overlap)?
- [ ] Are labels strictly binary, and are all probabilities finite and within $[0, 1]$?
- [ ] Are class-specific confidence thresholds $t_k$ computed per class, not fixed at $0.5$?
- [ ] Is the collision $\arg\max$ applied when both classes clear their thresholds?
- [ ] Are samples clearing no threshold excluded from the confident joint and reported separately?
- [ ] Is label noise ratio $\eta$ estimated, with $\eta \ge 20\%$ raising the high-noise warning?
- [ ] Does the downstream consumer receive the matrix orientation it expects — $P(\tilde{y}\mid y^*)$ vs $P(y^*\mid\tilde{y})$?
- [ ] Is exactly one remediation applied — relabel via `y_clean` **or** prune via sample weights, never both?
- [ ] Has the out-of-fold model been confirmed to have real predictive power before acting on $\eta$?
