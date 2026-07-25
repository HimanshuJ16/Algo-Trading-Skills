# Deep Workflow Reference — online-learning-for-adaptive-signal-models

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Perform Online Inference**: Compute prediction $\hat{y}_t = X_t^T W_t$.
2. **Observe Realized Target**: Receive true outcome $y_t$ at horizon completion.
3. **Compute Online Error & Update Weights**:
   - Update weight vector using SGD: $W_{t+1} = W_t + \eta \cdot (y_t - \hat{y}_t) \cdot X_t$.
4. **Clip Weight Norm**: Bound total $\|W\|_2 \le W_{\text{max}}$ to prevent weight explosion.
5. **Monitor Concept Drift**: Audit initial vs final Mean Absolute Error (MAE) to verify convergence.

## Production Implementation Reference

- Reference code: `scripts/online_adaptive_model.py` (`OnlineAdaptiveSignalModel`, `OnlinePredictionResult`, `OnlineModelAuditReport`).
- Automated unit tests: `scripts/test_online_adaptive_model.py`.
