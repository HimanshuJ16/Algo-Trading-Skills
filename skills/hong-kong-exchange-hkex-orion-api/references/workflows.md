# Workflows for HKEX Orion API Integration

1. **5-Digit Stock Code Padding**:
   - Zero-pad stock code to 5-digit string representation.
2. **Spread Table Tick Size Calculation**:
   - Lookup HKEX tick size based on price tier.
3. **Board Lot Validation**:
   - Verify quantity is an integer multiple of Board Lot size.
4. **Order Dispatch & Audit Logging**:
   - Dispatch order payload and output audit report.
