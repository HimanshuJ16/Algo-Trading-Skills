# Workflows for Strategy Decommissioning & Position Unwind Procedure

1. **Decommissioning Initiation**:
   - Hard-block new entry orders; transition state to `ORDER_ENTRY_BLOCKED`.
2. **Liquidation Slicing**:
   - Generate VWAP/TWAP liquidation slices constrained to $\le 10\%$ ADV per slice.
3. **Fill Reconciliation**:
   - Record executed fills and update position inventory.
4. **Treasury Capital Return**:
   - Transition to `FULLY_UNWOUND` when residual position reaches 0.