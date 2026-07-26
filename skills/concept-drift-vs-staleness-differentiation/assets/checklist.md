# Pre-Flight Checklist

- [ ] Is feature timestamp staleness checked before calculating model error statistics?
- [ ] Is Population Stability Index (PSI) or Wasserstein Distance used for feature drift ($P(X)$)?
- [ ] Is target residual error ratio ($\text{MSE}_{curr} / \text{MSE}_{ref}$) used for concept drift ($P(Y|X)$)?
- [ ] Are corrective actions segregated (pipeline fix for staleness vs. model refactor for concept drift)?