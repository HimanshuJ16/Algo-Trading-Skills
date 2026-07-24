# Deep Workflow Reference — ensemble-signal-combination-without-overfitting

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Normalize Sub-Model Signals:**
   - Apply Z-score standardization: $Z_{i,t} = \frac{S_{i,t} - \mu_i}{\sigma_i}$.
   - Clip normalized signals to $[-3.0, +3.0]$.

2. **Compute Regularized Weight Vector ($w$):**
   - Calculate raw non-negative weights $w_{\text{raw}}$.
   - Apply $1/N$ Shrinkage: $w_{\text{shrunk}} = (1 - \lambda) w_{\text{raw}} + \lambda (1/N)$.
   - Normalize sum of weights to $1.0$.

3. **Aggregate Composite Ensemble Signal:**
   - Compute weighted sum: $S_{\text{ensemble}, t} = \sum w_i Z_{i,t}$.

4. **Verify Out-of-Sample Stability:**
   - Verify zero negative weights ($w_i \ge 0$) and maximum weight caps.

## Failure Modes Observed in Production

- **Unconstrained Regression Overfitting:** Using standard OLS to fit model weights, generating extreme negative weights.
- **Un-Normalized Scale Distortion:** Combining sub-model signals with different native scales without Z-score normalization.

## Production Implementation Reference

- Reference code: `scripts/ensemble_combiner.py` (`EnsembleSignalCombiner`, `SignalStream`, `EnsembleResult`).
- Automated unit tests: `scripts/test_ensemble_combiner.py`.
