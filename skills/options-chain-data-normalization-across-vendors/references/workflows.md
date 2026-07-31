# Workflows for Options Chain Data Normalization Across Vendors

1. **Vendor Payload Parsing**:
   - Parse Polygon, IBKR, Bloomberg, or OPRA raw options contract payloads.
2. **OSI Symbology Generation**:
   - Build 21-character OCC OSI standard symbol: Ticker (pad 6) + YYMMDD + Type + Strike (pad 8).
3. **Data Quality Audit**:
   - Calculate mid price and spread; audit Bid <= Ask and Strike > 0.
4. **Audit Report Generation**:
   - Output structured options normalization report.
