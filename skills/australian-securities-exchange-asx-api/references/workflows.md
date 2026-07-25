# Workflows for ASX API Connectivity

1. **Environment Setup**: Determine if the deployment is targeting the CDE (Customer Development Environment) or Production.
2. **Topology Verification**: If the strategy is high-frequency market making, provision servers within the ALC (Australian Liquidity Centre). If it is a slower, VWAP execution algorithm, a standard ASX Net Global connection is sufficient.
3. **Engine Initialization**: Instantiate `AsxIntegrationEngine` with the correct `AsxProtocol` (FIX for standard, OUCH for HFT).
4. **Session Logon**: Call `.connect()` to initialize the TCP socket and perform the FIX/OUCH Logon sequence.
5. **Heartbeating**: Maintain the connection via protocol-specific heartbeat messages.
