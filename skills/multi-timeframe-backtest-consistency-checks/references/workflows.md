# Workflow — multi-timeframe-backtest-consistency-checks
## Procedure
1. Resample high-res bars to lower-res via OHLCV aggregation.
2. Compute indicators on both resolutions.
3. Compare signal values at matching timestamps.
4. Flag if max divergence exceeds tolerance.
## Reference
- `scripts/timeframe_consistency.py`, `scripts/test_timeframe_consistency.py`
