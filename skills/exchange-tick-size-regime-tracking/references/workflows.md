# Workflows for Exchange Tick Size Regime Tracking

1. **Venue Tick Regime Lookup**:
   - Determine active venue tick rules (US SEC 612, EU RTS 11, DFM).
2. **Price Band Evaluation**:
   - Lookup price band to determine active minimum tick size.
3. **Price Alignment**:
   - Round proposed order price to valid tick multiple.
4. **Compliance Audit**:
   - Verify order tick compliance before routing.
