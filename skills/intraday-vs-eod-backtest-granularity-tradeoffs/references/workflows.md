# Workflows for Backtest Granularity Assessment

1. **Strategy Profile Ingestion**:
   - Ingest holding period, trade frequency, stop-loss usage, and universe size.
2. **OHLC Sequence Bias Audit**:
   - Audit in-bar execution ambiguity for intraday stop-loss strategies on EOD data.
3. **Granularity Recommendation & Footprint Estimation**:
   - Recommend resolution (`TICK_L2`, `INTRADAY_1MIN`, `DAILY_EOD`) and calculate storage GB.
4. **Audit Report Generation**:
   - Output structured backtest granularity report.
