# Workflows for Exchange Fee Tier and Rebate Analysis

1. **Fee Schedule Ingestion**:
   - Ingest venue fee tiers (thresholds, maker/taker rates).
2. **Current Tier Classification**:
   - Classify 30-day volume into target fee tier.
3. **Net Cost Calculation**:
   - Compute gross taker fees, gross maker rebates, and net transaction cost.
4. **Tier Optimization Opportunity**:
   - Calculate remaining volume needed for tier jump and estimated fee savings.
