# Workflows for CIRO (IIROC) Electronic Trading Compliance

1. **System Design**: Map the `CiroPreTradeRiskEngine` into the execution path directly after the algo generates the signal, but *before* the order hits the FIX session logic.
2. **Configuration**: 
   - Define `MAX_ORDER_VALUE` based on daily firm capital limits.
   - Define `MAX_PRICE_DEVIATION_PCT` (usually 5-10% for TSX large caps).
3. **Data Hydration**: Ensure the order object is enriched with the current Last Traded Price (LTP) and the trader's current net position in the asset.
4. **Execution**:
   - The engine evaluates `validate_order(order)`.
   - If `True`, proceed to routing.
   - If `False`, log the rejection with `ViolationCode` and halt routing.
5. **Audit Logging**: Write all rejections to an immutable WORM (Write Once Read Many) drive for regulatory audit purposes.