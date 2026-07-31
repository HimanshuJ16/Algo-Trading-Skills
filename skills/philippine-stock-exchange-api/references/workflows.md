# Workflows for Philippine Stock Exchange API Integration

1. **Board Lot & Tick Size Lookup**:
   - Determine required tick size and minimum board lot based on price schedule.
2. **Order Divisibility & Tick Increment Validation**:
   - Verify quantity is a multiple of board lot and price is a valid tick increment.
3. **Static Price Band Audit**:
   - Ensure price is within 50% ceiling/floor band of previous close.
4. **Audit Report Generation**:
   - Output structured PSE execution report.
