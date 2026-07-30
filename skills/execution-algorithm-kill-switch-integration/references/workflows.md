# Workflows for Execution Algo Kill Switch Integration

1. **Risk Metric Audit**:
   - Continuously audit PnL, order rate, and cumulative net exposure.
2. **Emergency Triggering**:
   - Engage kill switch state upon threshold breach or manual override.
3. **Mass Cancel Dispatch**:
   - Broadcast FIX MassCancelRequest (Tag 530 = 7) across all broker gateways.
4. **Order Entry Lockout**:
   - Block and reject all new order submissions until manual admin reset.
