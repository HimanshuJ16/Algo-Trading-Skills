# Workflows for Real-Time VaR Backtesting Kupiec Test

1. **Input Collection**:
   - Collect observation window size ($T$) and exception counts ($x$).
2. **Likelihood Ratio & Binomial Test**:
   - Compute binomial $p$-value for $x$ exceptions out of $T$ observations against target $p = 1 - \alpha$.
3. **Hypothesis Evaluation**:
   - Evaluate $p$-value against significance level ($\alpha_{\text{stat}} = 0.05$).
4. **Audit Report Output**:
   - Return structured Kupiec test result.
