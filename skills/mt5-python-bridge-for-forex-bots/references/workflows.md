# Workflows for MT5 Python Bridge

1. **Volume & Stop Level Audit**:
   - Verify volume is a positive float $\ge 0.01$ and validate SL/TP distance.
2. **MqlTradeRequest Dictionary Serialization**:
   - Construct MT5 dictionary payload (`action`, `symbol`, `volume`, `type`, `price`, `sl`, `tp`, `deviation`, `type_filling`, `magic`).
3. **IPC Execution & Retcode Validation**:
   - Invoke order execution and audit `retcode == 10009` (`TRADE_RETCODE_DONE`).
4. **Audit Report Generation**:
   - Output structured MT5 order report.
