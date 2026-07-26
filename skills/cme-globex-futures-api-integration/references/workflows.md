# Workflows for CME Globex Futures Integration

1. **Tag 50 Registration & Assignment**:
   - Register all Operator IDs (Tag 50) in CME Request Center (ESS).
   - Map each trading algorithm / human trader instance to its assigned Operator ID.
2. **Contract Parameter Loading**:
   - Load tick size (e.g. 0.25 for ES), price band variation (e.g. 12.00 index points), and Market-With-Protection points (e.g. 6.00 index points / 24 ticks).
3. **Pre-Submission Risk Validation**:
   - Verify `operator_id` length (2 to 18 characters) and format.
   - Calculate upper and lower price band limits relative to current reference price:
     $\text{Max Price} = \text{Ref Price} + \text{Price Band}$, $\text{Min Price} = \text{Ref Price} - \text{Price Band}$.
   - Reject any order with price outside $[\text{Min Price}, \text{Max Price}]$.
4. **Order Execution & MWP Handling**:
   - For `MARKET` orders, calculate protection price:
     - BUY: $\text{Limit Price} = \text{Ask} + (\text{Protection Ticks} \times \text{Tick Size})$.
     - SELL: $\text{Limit Price} = \text{Bid} - (\text{Protection Ticks} \times \text{Tick Size})$.
   - Convert order type to `STOP_LIMIT` or `LIMIT_WITH_PROTECTION`.
5. **Execution Reporting**:
   - Process iLink 3 execution reports (fills, partial fills, resting MWP balances).
