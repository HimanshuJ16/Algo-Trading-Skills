# Workflows for B3 Exchange API Connectivity

1. **Protocol Definition**: Decide on the protocol suite based on latency requirements. HFT algorithms should use `MODERN_BINARY_SBE`. Standard execution algorithms (VWAP, TWAP) can utilize `LEGACY_FIX_FAST`.
2. **Network Provisioning**: Provision cross-connects within the B3 data center or via a certified extranet provider. Obtain Multicast IP ranges for the UMDF feeds.
3. **Engine Initialization**: Instantiate `B3IntegrationEngine` with the required parameters. Ensure `enable_application_gap_recovery` is set to `True` if using SBE.
4. **Session Logon**: Call `.connect()` to initialize the socket and perform the FIX 4.4 or FIXP Logon sequence.
5. **Feed Processing**: Subscribe to the UMDF Multicast feeds. If sequence gaps are detected on SBE, the application must buffer incoming packets and request a replay.
