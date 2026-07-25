# Workflows for Cross-Margin Optimization

1. **Data Ingestion**: 
   - Fetch real-time position deltas.
   - Fetch the latest 30-day exponentially weighted moving average (EWMA) correlation matrix from the quant research DB.
2. **Haircut Application**: 
   - Apply the broker's specific "correlation haircut" (e.g., if true correlation is 0.90, the broker might only credit 0.75 for margin offset purposes).
3. **Optimization Loop**:
   - The engine iterates through long vs. short exposures.
   - It calculates the `offset_benefit = min(margin_long, margin_short) * correlation_factor`.
   - It subtracts the `offset_benefit` from the total isolated margin.
4. **Capital Reallocation**: 
   - If the `Capital Efficiency Ratio` > 1.25, signal the allocation engine that excess capital is available to deploy into uncorrelated alpha streams.