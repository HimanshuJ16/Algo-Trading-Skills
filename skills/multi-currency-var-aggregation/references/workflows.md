# Workflows for Multi-Currency VaR Aggregation

1. **Base Currency Portfolio Sizing**:
   - Convert all multi-currency positions into base reporting currency.
2. **Joint Asset-FX Return Synthesis**:
   - Synthesize base-currency return series combining native price returns and FX spot returns.
3. **Parametric & Historical VaR Computation**:
   - Compute Parametric VaR, Historical Simulation VaR, and Expected Shortfall (CVaR).
4. **Audit Report Generation**:
   - Output structured multi-currency VaR report.