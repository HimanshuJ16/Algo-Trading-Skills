# Workflows for Real-Time Liquidity Risk Monitoring

1. **Days to Liquidate (DTL) Calculation**:
   - Calculate DTL per symbol based on position size, ADV, and participation cap.
2. **Spread Spike & L2 Depth Audit**:
   - Compare current bid-ask spread and L2 depth against historical baseline.
3. **Liquidity-Adjusted VaR (L-VaR)**:
   - Compute L-VaR combining market risk and liquidation cost.
4. **Audit Report Generation**:
   - Output structured liquidity risk monitoring report.