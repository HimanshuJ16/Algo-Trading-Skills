# Workflows for Iceberg Order Execution Routing

1. **Venue Capability Evaluation**:
   - Audit broker venue for native iceberg field support (`displaySize` / `icebergQty`).
2. **Native Iceberg Dispatch**:
   - Dispatch single parent order with display size for native-supported venues.
3. **Synthetic Slice Management**:
   - For non-native venues, slice parent order into randomized child orders.
4. **Refill Latency Audit & Logging**:
   - Track fill events and output execution report.