# Workflows for Adaptive Execution Under Volatility Spikes

## Production Execution Pipeline

1. **Volatility Calculation**: Continuously ingest 1-minute or tick-level price data. Compute a rolling z-score of standard deviation or True Range to identify volatility anomalies.
2. **Pre-Trade Check**: Before routing a child order (from a parent VWAP/TWAP), pass the `current_volatility` to the `AdaptiveExecutionUnderVolatilitySpikesEngine`.
3. **Execution Parameter Modification**: 
   - Apply the returned `participation_rate` to the current volume bin.
   - Slice the order using the returned `child_order_size`.
   - Peg limit orders using the `limit_offset_bps`.
4. **Circuit Breaker Activation**: If `halt_trading` is True, cancel all open working orders for the parent order and pause the execution loop until volatility normalizes or manual intervention occurs.