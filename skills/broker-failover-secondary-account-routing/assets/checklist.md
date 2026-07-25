# Integration Checklist: Broker Failover Router

Before deploying the `BrokerFailoverRouter` in a live trading environment, verify the following institutional requirements:

- [ ] **Threading/Concurrency**: Verified that `self.lock` appropriately wraps all state mutations and symbol mapping lookups.
- [ ] **Recovery Timeout Configuration**: Tuned `recovery_timeout_seconds` to match your infrastructure requirements. (e.g., 60s for REST APIs, 10s for Websocket/FIX).
- [ ] **Symbol Translation Validation**: Verified that all traded symbols are correctly registered using `register_symbol_map()`.
- [ ] **Idempotency Checks**: Implemented UUID-based client order IDs in the broker adapters to prevent duplicate fills during HTTP timeout uncertainty.
- [ ] **Mock Adapter Replacement**: Replaced `MockBrokerAdapter` with live production wrappers (e.g., IBKR, Alpaca, or FIX Engine adapters).
- [ ] **Unit Tests Passed**: Confirmed `python -m unittest test_failover_router.py` returns 100% OK.
