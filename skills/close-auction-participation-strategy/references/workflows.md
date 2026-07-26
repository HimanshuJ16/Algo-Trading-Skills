# Workflows for Closing Auction Participation

1. **NOII Data Stream Connection**:
   - Subscribe to Nasdaq TotalView ITCH (NOII message type 'N') or NYSE Order Imbalance feed.
2. **Imbalance Metrics Calculation**:
   - Extract `paired_shares`, `imbalance_shares`, `imbalance_direction` ('B' = Buy, 'S' = Sell, 'N' = No Imbalance), `far_price`, `near_price`.
3. **Time-Based Decision Engine**:
   - T-10 min to T-5 min: Calculate baseline imbalance and track indicative price drift.
   - T-5 min (Cutoff Window): Finalize contra-side participation quantity ($Qty = \min(Max\_Participation \times Imbalance\_Shares, Target\_Portfolio\_Shares)$).
4. **Order Placement**:
   - Submit `LOC` (Limit-On-Close) or `IO` (Imbalance-Only) order before the cutoff timestamp.
5. **Post-Cross Execution Processing**:
   - Match execution reports at 4:00:00 PM ET against the official closing price.