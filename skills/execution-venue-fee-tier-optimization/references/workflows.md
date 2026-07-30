# Workflows for Execution Venue Fee Tier Optimization

1. **Venue Schedule Ingestion**:
   - Ingest candidate venue volume tiers, taker fee rates, and maker rebate rates.
2. **Allocation Candidate Generation**:
   - Generate candidate volume allocation splits across venues.
3. **Net Fee Computation**:
   - Compute gross taker fees, gross maker rebates, and net costs per allocation.
4. **SOR Routing Update**:
   - Update Smart Order Router venue weights based on optimal allocation.