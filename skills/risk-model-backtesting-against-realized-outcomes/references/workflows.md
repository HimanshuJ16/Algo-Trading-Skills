# Workflows for Risk Model Backtesting Against Realized Outcomes

1. **Daily P&L & VaR Ingestion**:
   - Record daily realized P&L and 1-day forecast VaR for every trading session.
2. **Exception Identification**:
   - Flag exception if realized P&L < -forecast VaR; calculate breach amount in USD.
3. **Statistical Hypothesis Testing**:
   - Compute Kupiec's POF test LR statistic and p-value.
4. **Basel Traffic Light Classification**:
   - Assign Green, Yellow, or Red zone status and output compliance report.