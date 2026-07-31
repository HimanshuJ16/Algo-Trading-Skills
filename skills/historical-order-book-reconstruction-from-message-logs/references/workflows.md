# Workflows for Historical Order Book Reconstruction

1. **L3 Message Ingestion**:
   - Ingest ADD, CANCEL, EXECUTE, and REPLACE message events.
2. **Order Map Update**:
   - Update active order map using $O(1)$ hash map lookup.
3. **L2 Depth Aggregation**:
   - Aggregate active orders into sorted Bids and Asks by price level.
4. **BBO & Integrity Audit**:
   - Compute BBO, Mid-Price, Spread, and audit for crossed books.
