# Workflows for Adverse Selection Measurement

## Execution Analysis Pipeline

1. **Trade Ingestion**: Extract all passive execution fills from the broker FIX logs or EMS databases. Exclude active/aggressing orders (like Market orders), as adverse selection only applies to resting liquidity.
2. **Market Data Alignment**: Load Level 1 quote data (Top of Book) covering the exact execution timestamps plus the maximum forward horizon (e.g., +60 seconds).
3. **Engine Evaluation**: Pass the fills and the market data into `MarkoutEngine`.
4. **Markout Curve Analysis**:
   - If the curve drops sharply negative in the first 100ms, your feed handler or cancellation logic is too slow (Latency Arbitrage).
   - If the curve slopes downward over 1 to 5 minutes, your alpha model is wrong (Directional Adverse Selection).
