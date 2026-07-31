# Workflows for Real-Time vs Delayed Data Entitlement Handling

1. **User Subscription Audit**:
   - Verify exchange entitlements and subscriber category (Professional vs Non-Pro).
2. **Execution Compliance Verification**:
   - Block order placement if trading execution is requested using delayed market data.
3. **Data Stream Delivery**:
   - Deliver real-time stream (0 min delay) or delayed stream (15 min delay) with required display tags.
4. **Audit Report Generation**:
   - Output structured entitlement audit report.
