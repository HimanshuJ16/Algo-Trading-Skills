# Deep Workflow Reference — broker-order-type-capability-matrix

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Broker Capability Matrix Registration**:
   - Query or register supported native order types (`MARKET`, `LIMIT`, `STOP_LIMIT`, `TRAILING_STOP`, `BRACKET`, `OCO`, `ICEBERG`) per broker.

2. **Order Execution Planning**:
   - Before dispatch, evaluate `supports_native(broker, order_type)`.
   - If native support exists, dispatch directly to broker API.

3. **Software Order Synthesizer Fallback**:
   - If native support is missing, decompose complex order into primary market/limit order and register local trigger legs (e.g. Stop-Loss and Take-Profit for Bracket orders, or slice feeders for Iceberg orders).

## Production Implementation Reference

- Reference code: `scripts/capability_matrix.py` (`BrokerOrderCapabilityMatrix`, `BrokerCapabilities`, `SynthesizedOrderPlan`).
- Automated unit tests: `scripts/test_capability_matrix.py`.
