# Workflows for KRX Integration

1. **6-Digit Ticker Code Audit**:
   - Verify and zero-pad 6-digit numeric ticker code (`005930`).
2. **KRW Price Tick Tier Audit**:
   - Calculate dynamic tick size based on price tier.
3. **Daily Price Expansion Limit Audit**:
   - Verify price within $\pm 30\%$ limits.
4. **Audit Report Generation**:
   - Output structured KRX order report.
