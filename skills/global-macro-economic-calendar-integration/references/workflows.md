# Workflows for Global Macro Calendar Integration

1. **Economic Event Schedule Ingestion**:
   - Ingest macroeconomic releases with country, impact severity, and timestamps.
2. **Blackout Window Calculation**:
   - Define pre-event and post-event blackout duration buffers.
3. **Surprise Index Calculation**:
   - Compute surprise metric upon release of actual data.
4. **Automated Order Cancellation**:
   - Trigger limit order mass-cancel during active blackout windows.
