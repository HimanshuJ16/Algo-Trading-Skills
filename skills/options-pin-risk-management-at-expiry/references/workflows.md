# Workflows for Options Pin Risk Management at Expiry

1. **Pin Distance & Expiry Audit**:
   - Calculate percentage distance to strike (|Spot - Strike| / Spot * 100%) and check against hours to expiry.
2. **Action Resolution**:
   - For short options with High Pin Risk, trigger CLOSE_POSITION_BEFORE_EXPIRY or ROLL_POSITION.
3. **Assigned Notional Calculation**:
   - Compute max potential assigned share notional exposure (|Q| * 100 * Spot).
4. **Audit Report Generation**:
   - Output structured pin risk report.
