# Workflows for Iceberg Order Detection

1. **Trade & Depth Ingestion**:
   - Track trade executions and resting depth per price level.
2. **Cumulative Volume Discrepancy Analysis**:
   - Compare cumulative traded volume against initial displayed depth.
3. **Iceberg Confirmation & Capacity Estimation**:
   - Confirm iceberg presence when volume ratio $\ge 1.5\times$ and estimate hidden quantity.
4. **Microstructure Signal Generation**:
   - Classify `BULLISH_HIDDEN_BUY` vs `BEARISH_HIDDEN_SELL` and generate report.