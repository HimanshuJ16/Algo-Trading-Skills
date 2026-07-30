# Workflows for Futures Expiry Week Handling

1. **Order Book Microstructure Ingestion**:
   - Ingest spread, depth, and days to expiration for front-month contract.
2. **Spread & Depth Haircut Audit**:
   - Apply 50% position size haircut if depth drops $< 30\%$ of baseline.
3. **Market Order Prohibition**:
   - Prohibit market orders if bid-ask spread exceeds 2.0 ticks.
4. **Mandatory Roll Enforcement**:
   - Force contract roll if DBE $\le 2$ days.
