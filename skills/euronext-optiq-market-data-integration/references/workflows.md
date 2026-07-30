# Workflows for Euronext Optiq Market Data Integration

1. **SBE Packet Unpacking**:
   - Unpack Optiq MDG binary headers and payload fields.
2. **Order Book Reconstruction**:
   - Maintain L2 price-level depth arrays for Bids and Asks.
3. **Microstructure Calculation**:
   - Compute Mid-Price, Spread, and Order Book Imbalance ratio.
4. **State Transition Handling**:
   - Intercept SymbolStatus messages and update trading engine liveness.
