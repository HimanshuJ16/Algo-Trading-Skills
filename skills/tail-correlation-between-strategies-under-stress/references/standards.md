# Risk Management Standards for Tail Correlation

## Key Thresholds & Parameters

- **Tail Quantile ($\alpha$)**: Default $0.10$ (10th percentile downside returns).
- **Lower Tail Dependence ($\lambda_L$)**: $\mathbb{P}(R_B \le q_B \mid R_A \le q_A) \ge 0.50$ indicates severe downside copula tail coupling.
- **Breakdown Threshold**: Lower tail correlation $\ge 0.70$ triggers capital allocation freeze.
- **Max Correlation Delta ($\Delta \rho_{\text{tail}}$)**: $\rho_{\text{tail}} - \rho_{\text{uncond}} \ge 0.40$ flags regime-dependent diversification loss.