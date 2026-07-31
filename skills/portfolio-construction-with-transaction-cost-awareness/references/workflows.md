# Workflows for Portfolio Construction with Transaction Cost Awareness

1. **No-Trade Buffer Band Filtering**:
   - Suppress rebalancing trades where proposed weight change is within buffer threshold.
2. **Transaction Cost Calculation**:
   - Compute linear commission, spread costs, and quadratic market impact costs.
3. **Net Utility & Turnover Audit**:
   - Audit portfolio turnover and calculate net expected return after transaction costs.
4. **Audit Report Generation**:
   - Output structured TC-aware portfolio report.