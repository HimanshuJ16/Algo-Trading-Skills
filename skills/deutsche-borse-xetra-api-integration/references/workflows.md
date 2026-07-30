# Workflows for Deutsche Börse Xetra API Integration

1. **Header & Body Assembly**:
   - Construct T7 ETI binary header and FIX 5.0 SP2 tags.
2. **Tick Size Audit**:
   - Verify order price against Xetra price-band tick rules.
3. **MiFID II Tag Verification**:
   - Verify account classification (`P`, `A`, `M`) and DEA short codes.
4. **Order Execution & Report**:
   - Process T7 execution report and update order state.
