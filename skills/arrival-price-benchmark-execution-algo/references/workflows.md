# Workflows for Arrival Price Execution

1. **Portfolio Manager Decision**: The PM decides to buy 10,000 shares of AAPL. The mid-price at this exact second is $150.00. This is the **Arrival Price**.
2. **Urgency Assessment**: 
   - If the alpha signal decays in 5 minutes, the PM assigns `UrgencyLevel.HIGH`.
   - If the trade is a multi-day rebalance, the PM assigns `UrgencyLevel.LOW`.
3. **Trajectory Generation**: The `ArrivalPriceTrajectoryGenerator` creates an array of child order sizes.
4. **Execution Routing**: An execution bot loops through the time bins, sending Limit or Market orders sized according to the array.
5. **Implementation Shortfall (IS) Calculation**: At completion, calculate: `(Average Execution Price - Arrival Price) * Total Shares`. This is the cost of the trade.