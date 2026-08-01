# Workflows for Regime Detection for Strategy Switching

1. **ATR Volatility Z-Score Calculation**:
   - Compute 14-period ATR; z-score latest ATR vs historical mean/stddev.
2. **ADX/DMI Trend Strength Classification**:
   - Compute ADX, +DI, -DI; classify direction and strength.
3. **Raw Regime Classification + Hysteresis Filter**:
   - Map volatility/trend indicators to regime; require N consecutive bars before switching.
4. **Strategy Variant Routing**:
   - Map confirmed regime to appropriate strategy module.
