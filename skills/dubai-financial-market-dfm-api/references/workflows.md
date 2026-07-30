# Workflows for Dubai Financial Market (DFM) API Integration

1. **NIN Investor Identification**:
   - Attach mandatory 10-digit National Investor Number (NIN) to FIX Tag 1 (`Account`).
2. **AED Tick Size Audit**:
   - Audit order price against DFM price-band tick rules (0.001, 0.01, 0.02, 0.05 AED).
3. **Price Band Circuit Breaker Verification**:
   - Verify order price is within $\pm 10\%$ of prior settlement.
4. **FIX 4.4 Dispatch**:
   - Dispatch FIX 4.4 New Order Single message.
