# Workflows for Eurex Market Data and Order API

1. **T7 EMDI Ingestion**:
   - Ingest UDP multicast market data depth feeds.
2. **Contract Specification Audit**:
   - Verify contract tick step and multiplier (€10.00/point for FESX).
3. **Price Reasonability Check**:
   - Confirm order price is within allowable band relative to mid-price.
4. **T7 ETI Dispatch**:
   - Dispatch binary FIX 5.0 SP2 order payload over ETI session.
