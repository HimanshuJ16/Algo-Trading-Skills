# Checklist for B3 PUMA Integration

- [ ] Confirm `B3ProtocolSuite.MODERN_BINARY_SBE` strictly enforces `enable_application_gap_recovery=True`.
- [ ] Confirm the market data ingest logic correctly parses Market-by-Order (MBO) vs Market-by-Price (MBP) depending on the chosen feed.
- [ ] Verify that the FIX session maintains sequence numbers across disconnects for the Legacy FIX 4.4 protocol.
- [ ] Run test suite: `python scripts/test_b3_brazil_exchange_api_integration.py`.

## Sign-off
- Connectivity Engineer: ___________________________
- Date: ___________________________
