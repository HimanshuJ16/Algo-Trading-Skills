# Workflows for Multi-Leg Atomicity

1. **Leg Sizing Validation**: Mathematically ensure the ratio of Leg 1 to Leg 2 strictly aligns with the intended exposure (e.g., 1:1 for a calendar spread, delta-weighted for an options delta-neutral spread).
2. **Liquidity Assessment**: Identify the "Anchor Leg" (lowest average daily volume or widest bid/ask spread) and the "Hedging Leg" (highly liquid proxy).
3. **Execution Routing**:
   - Route `Anchor Leg` as a standard passive Limit Order.
   - Hold `Hedging Leg` in memory.
4. **Fill Processing**: On receipt of a `FILLED` or `PARTIALLY_FILLED` FIX message for the Anchor Leg, immediately calculate the required quantity for the Hedging Leg.
5. **Hedging Route**: Route the `Hedging Leg` using an IOC (Immediate Or Cancel) or Aggressive Limit order.
6. **Reconciliation**: If the Hedging Leg times out or is rejected, trigger the `BrokenSpreadException` to invoke the firm's emergency hedge protocol.