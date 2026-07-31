# Workflows for Perpetual Futures Funding Rate Handling

1. **Notional Value & Funding Payment Calculation**:
   - Compute position value (Notional = Qty * MarkPrice) and calculate signed funding payment.
2. **Annualized APR Calculation**:
   - Compute annualized funding APR (F * (365 * 24 / IntervalHours) * 100%).
3. **Adverse Drag Audit**:
   - Audit if adverse funding fee drag exceeds acceptable policy limits.
4. **Audit Report Generation**:
   - Output structured funding rate report.