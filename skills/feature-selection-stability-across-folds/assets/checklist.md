# Pre-Flight Checklist

- [ ] Are feature selection subsets recorded for each CV fold ($K \ge 5$)?
- [ ] Is feature inclusion probability ($p_i$) calculated for all candidate features?
- [ ] Is Nogueira Stability Index ($\Phi \ge 0.70$) computed?
- [ ] Are unstable features ($p_i < 0.80$) pruned from final production model pipeline?
