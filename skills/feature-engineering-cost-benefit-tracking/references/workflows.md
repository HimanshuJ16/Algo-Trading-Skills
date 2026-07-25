# Deep Workflow Reference — feature-engineering-cost-benefit-tracking

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Quantify Marginal Feature Value**: Compute feature importance scores $I_i$ (e.g. Permutation Importance).
2. **Assign Feature Cost**: Record monthly data licensing and compute infrastructure cost $C_i$.
3. **Compute Feature ROI Ratio**: $\text{ROI}_i = \frac{I_i \times 100}{\max(1.0, C_i)}$.
4. **Enforce Pruning Decision Rules**:
   - `KEEP`: High importance, low/moderate cost ($I_i \ge 0.01$).
   - `PRUNE`: Low importance, high cost ($I_i < 0.01$ and $C_i > \$50/\text{mo}$).

## Production Implementation Reference

- Reference code: `scripts/feature_cost_benefit.py` (`FeatureCostBenefitTracker`, `FeatureCostBenefitRecord`, `FeatureCostBenefitReport`).
- Automated unit tests: `scripts/test_feature_cost_benefit.py`.
