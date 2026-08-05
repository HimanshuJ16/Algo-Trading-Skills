# Workflows for Smart Order Routing Across Venues

1. **Quote Consolidation**:
   - Aggregate lit venue quotes to derive NBBO mid, bid, and ask.
2. **Net Price Calculation**:
   - Compute net execution price considering exchange taker fees and maker rebates.
3. **Liquidity Slicing**:
   - Slice parent order into child orders targeting top-of-book depth per venue.
4. **Execution Audit**:
   - Log parent order plan, child routes, and net expected execution cost.