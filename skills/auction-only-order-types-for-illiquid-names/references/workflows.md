# Workflows for Illiquid Auction Execution

1. **Pre-Trade ADV Assessment**: Before executing any parent order, the execution system queries the 30-day Average Daily Volume (ADV) for the target instrument.
2. **Routing Decision**: Pass the `total_qty` and `average_daily_volume` to `IlliquidAuctionExecutionEngine.generate_routing_plan()`.
3. **Continuous Slicing**: If `continuous_qty > 0`, route that portion to a standard VWAP or TWAP execution algorithm designed to finish by 3:45 PM ET.
4. **Auction Submission**: If `auction_qty > 0`, submit a Limit-on-Close (LOC) order prior to the exchange's strict cutoff time (e.g., 3:50 PM ET for NYSE). The Limit Price should be set based on the trader's acceptable slippage tolerance from the current mid-price.
