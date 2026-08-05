# Workflows for Singapore Exchange SGX API Integration

1. **Session Logon**:
   - Initiate FIX session with SGX TITAN gateway.
2. **Pre-Trade Tick Alignment**:
   - Verify limit order price aligns with SGX contract tick size (e.g. 2.5 for CN, 5.0 for NK).
3. **Order Routing**:
   - Send order payload to gateway and capture execution reports.
4. **Session Disconnect**:
   - Gracefully logout FIX session at end of trading day.
