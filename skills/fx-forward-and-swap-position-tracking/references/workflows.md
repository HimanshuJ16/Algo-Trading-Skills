# Workflows for FX Forward & Swap Position Tracking

1. **Contract Ingestion**:
   - Ingest FX forward/swap contract terms (pair, notional, rate, maturity).
2. **CIRP Pricing**:
   - Compute theoretical forward rate and swap points using spot and interest rate curves.
3. **Mark-to-Market Valuation**:
   - Calculate current fair value and unrealized MtM PnL.
4. **Net Risk Aggregation**:
   - Aggregate net currency exposures across maturity buckets.