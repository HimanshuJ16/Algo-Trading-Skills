# Workflows for IDX Indonesia Stock Exchange Integration

1. **Ticker & Board Validation**:
   - Verify 4-letter uppercase ticker and valid market board type (`RG`/`TN`/`NG`).
2. **Fraksi Harga Tick Size Calculation**:
   - Compute dynamic IDX tick size based on price tier.
3. **Board Lot & Price Bounds Audit**:
   - Verify quantity is a multiple of 100 shares and price is within ARB/ARA limits.
4. **Order Execution Logging**:
   - Output structured IDX order report.
