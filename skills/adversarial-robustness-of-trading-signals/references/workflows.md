# Workflows for Adversarial Robustness of Trading Signals

## Pre-Deployment Governance Pipeline

1. **Extract Validation Dataset**: Provide a representative out-of-sample feature matrix `X_clean`.
2. **Define Epsilon Boundary**: Set `epsilon` (e.g., 0.01) based on the expected maximum spread or bid-ask variance in the underlying asset's microstructure.
3. **Execute Adversarial Test**: Pass the candidate model's `.predict()` function into the `SignalAdversarialTester`.
4. **Gate Deployment**: 
   - If `vulnerability_score_pct` < 5%: Approve model deployment.
   - If `vulnerability_score_pct` > 5%: Reject model. Route back to quant research for Adversarial Training (training the model on perturbed data to smooth out the decision boundaries).
