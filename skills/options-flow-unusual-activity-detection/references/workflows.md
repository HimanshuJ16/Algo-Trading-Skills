# Workflows for Options Flow Unusual Activity Detection

1. **Trade Metric Computation**:
   - Compute Volume-to-OI ratio (V/OI), Volume-to-ADV ratio (V/ADV), and total USD premium.
2. **Aggressor Side Identification**:
   - Classify trade as execution at/above Ask (BUY_AT_ASK) or at/below Bid (SELL_AT_BID).
3. **Unusual Activity Classification**:
   - Flag V/OI >= 1.5, V/ADV >= 2.0, and Premium >= $100,000 as UNUSUAL_BULLISH_SWEEP or UNUSUAL_BEARISH_SWEEP.
4. **Audit Report Generation**:
   - Output structured options flow anomaly report.