# Trading Workflow Integration

## Pre-Trade Lifecycle
1. **Order Generation**: Alpha model generates a signal for a binary option.
2. **Context Assembly**: Trade context (underlying, strike, expiry, notional, client type, jurisdiction) is compiled.
3. **Compliance Gate**: 
   - Check if jurisdiction allows the trade for the specified client type.
   - Reject if venue is unregulated.
4. **Risk Gate**:
   - Check aggregate notional limit.
   - Check pin risk and Greek limits.
5. **Execution**: Send to execution algorithm if approved.
6. **Post-Trade**: Log all rejected trades for compliance audits.