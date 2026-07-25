# Workflows for Cboe Complex Order Integration

1. **Strategy Definition**: Define the multi-leg strategy (e.g., Iron Condor, Calendar Spread, Buy-Write).
2. **Leg Extraction**: Extract the individual instrument definitions (Symbol, Expiration, Strike, Put/Call, Ratio, Side).
3. **Ratio Normalization (CRITICAL)**:
   - Find the Greatest Common Divisor (GCD) of all leg ratios.
   - Divide all leg ratios by the GCD.
   - Multiply the total desired order quantity by the GCD.
   *Example: Want to buy 100 SPX Calls and sell 200 SPX Calls.*
   *Raw Ratio: 100:200. GCD: 100.*
   *Normalized Ratio: 1:2. Order Qty: 100.*
4. **FIX Formatting**:
   - `MsgType=AB` (New Order Multileg)
   - `NoLegs=N`
   - For each leg, populate `LegSymbol (600)`, `LegRatioQty (623)`, `LegSide (624)`, etc.
   - `Price (44)` = Net Price of the strategy.
5. **Auction Routing**: Add routing tags if the firm wishes to explicitly target or bypass the Complex Order Auction (COA).
