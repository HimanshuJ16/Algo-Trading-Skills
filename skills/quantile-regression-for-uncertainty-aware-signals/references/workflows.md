# Deep Workflow Reference — quantile-regression-for-uncertainty-aware-signals

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Evaluate Pinball Loss**: Compute $L_{\tau}(y, \hat{y}) = \max(\tau(y - \hat{y}), (\tau - 1)(y - \hat{y}))$.
2. **Train Multi-Quantile Model**: Fit parameter vectors for $\tau = 0.10, 0.50, 0.90$.
3. **Enforce Monotonicity**: Sort predicted quantiles to prevent quantile crossing.
4. **Scale Position Size by Confidence**: Scale position size by ratio of median forecast to interquartile width.

## Production Implementation Reference

- Reference code: `scripts/quantile_regression_model.py` (`QuantileRegressionSignalModel`, `QuantilePrediction`).
- Automated unit tests: `scripts/test_quantile_regression_model.py`.
