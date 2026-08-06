# Workflows for Tail Correlation Analysis

```mermaid
flowchart TD
    A[Strategy Return Inputs] --> B[Compute 10th Percentile Downside Quantile]
    B --> C[Compute Unconditional Pearson Correlation]
    C --> D[Compute Lower Tail Dependence Lambda_L]
    D --> E[Compute Conditional Downside Exceedance Correlation]
    E --> F{Conditional Corr >= 0.70 OR Delta >= 0.40?}
    F -- Yes --> G[Issue Diversification Breakdown Warning & Cap Allocation]
    F -- No --> H[Diversification Confirmed: Approve Allocation]
```

## Step-by-Step Procedure

1. **Ingest Return Series**: Align strategy return series by timestamp, dropping non-overlapping missing values.
2. **Set Tail Quantile**: Use $\alpha = 0.10$ for standard stress analysis or $\alpha = 0.05$ for extreme tail events.
3. **Execute `TailCorrelationAnalyzerEngine`**: Run pair or portfolio matrix analysis.
4. **Evaluate Output**: Filter breakdown pairs and adjust portfolio weights to mitigate joint crash exposure.