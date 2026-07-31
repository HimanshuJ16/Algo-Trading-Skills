# Workflows for Record-Keeping Requirements for Tax Audit Defense

1. **Mandatory Field Completeness Audit**:
   - Validate all trade records contain required fields (trade_id, symbol, side, quantity, price, trade_date, cost_basis_usd).
2. **Holding Period Classification**:
   - Classify each trade as Short-Term ($\le 365$ days) or Long-Term ($> 365$ days).
3. **Wash Sale & Retention Policy Check**:
   - Verify wash sale flags populated for sell transactions; enforce 7-year minimum retention.
4. **Audit Report Generation**:
   - Output structured tax audit compliance report.