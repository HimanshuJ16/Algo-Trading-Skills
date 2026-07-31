# Workflows for Portfolio Stress Test Including Liquidity Crunch Scenarios

1. **Stressed Liquidity & DTL Calculation**:
   - Apply liquidity drop haircut to ADV and calculate Days-to-Liquidate (DTL) per asset.
2. **Price Shock & Liquidity Haircut Evaluation**:
   - Compute macro price shock losses and add liquidity slippage haircuts.
3. **Illiquidity Bottleneck Audit**:
   - Audit assets exceeding max allowed Days-to-Liquidate limits.
4. **Audit Report Generation**:
   - Output structured stress test report.