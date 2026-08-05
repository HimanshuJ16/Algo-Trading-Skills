# Workflows for Saxo Bank OpenAPI Integration

1. **UIC Lookup**:
   - Query `/ref/v1/instruments` with ticker keywords and `AssetType`.
2. **Order Submission**:
   - Post multi-asset order payload to `/trade/v1/orders`.
3. **Position Monitoring**:
   - Poll `/port/v1/positions` for position view and P&L.
