# Workflows for Options Greeks Real-Time Portfolio Aggregation

1. **Position Greeks Scaling**:
   - Compute scaled Delta, Dollar Delta, Gamma, Theta, and Vega for each contract position.
2. **Portfolio-Level & Asset Grouping**:
   - Sum net Greeks across the entire portfolio and group by underlying symbol.
3. **Risk Limit Compliance Audit**:
   - Audit Dollar Delta, Theta decay, and Vega exposure against portfolio limits.
4. **Audit Report Generation**:
   - Output structured portfolio Greeks report.