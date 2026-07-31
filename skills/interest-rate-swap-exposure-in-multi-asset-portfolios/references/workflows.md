# Workflows for IRS Exposure Management

1. **IRS Position Ingestion & Duration Modeling**:
   - Model modified duration and base DV01 for swap positions.
2. **Directional DV01 Sign Assignment**:
   - Assign $+ \text{DV01}$ for Pay-Fixed and $- \text{DV01}$ for Receive-Fixed.
3. **Multi-Asset Aggregation & Hedging**:
   - Sum bond DV01 and IRS DV01, calculating required swap notional for DV01 neutrality.
4. **Audit Reporting**:
   - Output structured IRS exposure report.