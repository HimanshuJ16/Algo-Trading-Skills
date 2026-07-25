# Deep Workflow Reference — broker-failover-secondary-account-routing

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Adapter Registration & Symbol Mapping**:
   - Initialize primary and secondary broker adapters.
   - Register symbol translations between brokers (e.g. IBKR `AAPL STK SMART` vs Alpaca `AAPL`).

2. **Intercept Order Submissions & Track Failures**:
   - Wrap `place_order()` with error handling. Increments `primary_failures` on 5xx or connection exception.

3. **Trip Breaker & Reroute**:
   - When `primary_failures >= max_consecutive_failures` (default 3), set primary status to `DOWN`.
   - Automatically redirect all subsequent orders to secondary adapter.

4. **Unified Exposure Ledger**:
   - Aggregate holdings across both account IDs for portfolio risk limits.

## Production Implementation Reference

- Reference code: `scripts/failover_router.py` (`BrokerFailoverRouter`, `MockBrokerAdapter`).
- Automated unit tests: `scripts/test_failover_router.py`.
