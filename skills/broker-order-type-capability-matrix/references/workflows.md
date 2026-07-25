# Deep Workflow Reference — broker-order-type-capability-matrix

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Broker Capability Matrix Registration**:
   - Populate `DEFAULT_CAPABILITIES` with target broker profiles.
   - Register supported native order types (`MARKET`, `LIMIT`, `STOP_LIMIT`, `TRAILING_STOP`, `BRACKET`, `OCO`, `ICEBERG`, `TWAP`, `VWAP`, `PEGGED`) per broker.
   - You can also register custom API endpoints via `matrix.register_broker()`.

2. **Order Execution Planning**:
   - Before dispatch, evaluate `matrix.plan_order_execution(...)`.
   - The result object `SynthesizedOrderPlan` indicates whether to route natively or synthetically.
   - If `is_native=True`, dispatch directly to broker API using their native multi-leg format.

3. **Software Order Synthesizer Fallback**:
   - If `is_native=False`, the matrix decomposes the complex order.
   - The `primary_order_type` must be fired immediately (if applicable).
   - The returned `emulated_legs` must be loaded into the local EMS (Execution Management System) engine.
     - **Price-triggered legs**: Track Level 1 NBBO/Trade quotes locally, and fire the `action` when `trigger_price` is crossed.
     - **Time-triggered feeders**: e.g., Iceberg slices or TWAP intervals, must register a timer task in the EMS that fires subsequent market/limit orders.

## Production Implementation Reference

- Reference code: `scripts/capability_matrix.py` (`BrokerOrderCapabilityMatrix`, `BrokerCapabilities`, `SynthesizedOrderPlan`, `EmulatedLeg`).
- Automated unit tests: `scripts/test_capability_matrix.py`.
