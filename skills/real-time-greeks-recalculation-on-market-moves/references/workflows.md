# Workflows for Real-Time Greeks Recalculation on Market Moves

1. **Price Shift Detection**:
   - Compare new underlying spot price against last model spot price.
2. **Hybrid Recalculation Dispatch**:
   - Apply Taylor expansion for micro-ticks ($\le 0.5\%$).
   - Execute full Black-Scholes model for larger moves ($> 0.5\%$).
3. **Portfolio Greeks Aggregation**:
   - Aggregate net position Delta, Gamma, and Vega across all active options.
4. **Audit Report Generation**:
   - Output structured real-time Greeks report.