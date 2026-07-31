# Workflows for Quanto Options and Cross-Currency Derivative Structures

1. **Quanto Drift Adjustment**:
   - Compute effective drift $r_{\text{quanto}} = r_f - q - \rho \cdot \sigma_S \cdot \sigma_X$.
2. **Black-Scholes $d_1, d_2$ Calculation**:
   - Compute $d_1$ and $d_2$ using quanto drift.
3. **Quanto Option Pricing & Greeks**:
   - Discount payoff at domestic rate $r_d$ multiplied by fixed FX conversion multiplier $F_X$.
   - Calculate Delta, Gamma, Vega, and FX Correlation Sensitivity.
4. **Audit Report Generation**:
   - Output structured quanto option pricing report.
