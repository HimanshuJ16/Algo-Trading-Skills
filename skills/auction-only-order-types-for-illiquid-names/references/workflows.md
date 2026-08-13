# Workflows for Illiquid Auction Execution

1. **Pre-Trade ADV Assessment**: Before executing any parent order, the execution system queries the 30-day Average Daily Volume (ADV) for the target instrument.
2. **Routing Decision**: Pass the `total_qty` and `average_daily_volume` to `IlliquidAuctionExecutionEngine.generate_routing_plan()`. Supply `reference_price`, `slippage_tolerance_bps`, and `side` ("BUY"/"SELL") so the engine can populate `suggested_limit_price` for the LOC order. The engine validates that `total_qty > 0`, `average_daily_volume > 0`, and `symbol` is non-empty.
3. **Limit-Price Hydration**: If `suggested_limit_price` is `None` (no reference price was supplied) but `auction_qty > 0` with `auction_order_type == LIMIT_ON_CLOSE`, the caller MUST assign a limit price to the plan before submission. An LOC order without a limit price will be rejected by the exchange (NYSE Rule 7.35(B)).
4. **Cutoff Enforcement**: Before routing, call `validate_submission_window(submission_time_et)` with a timezone-aware US/Eastern datetime. Submissions at or past 3:50 p.m. ET (`CLOSING_AUCTION_CUTOFF_ET`) raise `ValueError`. Nasdaq-only entry may relax to 3:58 p.m. ET (`NASDAQ_LOC_ENTRY_CUTOFF_ET`).
5. **Continuous Slicing**: If `continuous_qty > 0`, route that portion to a standard VWAP or TWAP execution algorithm designed to finish by 3:45 p.m. ET.
6. **Auction Submission**: If `auction_qty > 0`, submit a Limit-on-Close (LOC) order with the (hydrated or computed) limit price prior to the exchange cutoff. MOC must NOT be used for illiquid names.
