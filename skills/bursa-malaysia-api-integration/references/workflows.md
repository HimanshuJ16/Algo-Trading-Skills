# Workflows for Bursa Malaysia BTS2 Integration

1. **Onboarding**: Submit the BTS2-A1 form to Bursa Malaysia. Obtain the UAT/Certification IP addresses, `SenderCompID`, and `TargetCompID`.
2. **Certification**: Run the mandatory ISV FIX certification test-cases provided by the exchange to validate order state transitions (New, Partially Filled, Filled, Canceled, Rejected).
3. **Initialization**: Instantiate `BursaMalaysiaFixEngine` with your firm's specific config.
4. **Trading Loop**: 
   - Call `connect()` to simulate the FIX Logon.
   - Use `submit_order()` for alpha-driven signals.
   - Use `cancel_order()` to retract stale liquidity.
   - Consume `ExecutionReport` callbacks to synchronize portfolio positions.
