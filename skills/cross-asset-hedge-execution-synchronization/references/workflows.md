# Workflows for Cross-Asset Hedge Execution Synchronization

1. **Fill Event Ingestion**:
   - Ingest primary fill ($Q_{primary}, t_{primary\_fill}$).
2. **Hedge Order Generation**:
   - Calculate $\text{Hedge Qty} = -1.0 \times Q_{primary} \times \text{Hedge Ratio}$.
3. **Execution Routing**:
   - Immediately dispatch hedge order to execution venue.
4. **Latency Measurement**:
   - Compute synchronization delay $\Delta t = t_{hedge\_fill} - t_{primary\_fill}$.
   - If $\Delta t > \text{Max Sync Delay MS} \implies$ Trigger aggressive market-crossing repricing.
5. **Emergency Unwind**:
   - If hedge remains unfilled after timeout, execute emergency unwind of primary leg.