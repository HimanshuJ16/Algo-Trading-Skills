# Pre-Flight Checklist: broker-agnostic-adapter-interface

Use this checklist during architecture reviews or PR approvals when introducing a new broker adapter into the strategy codebase.

## Domain Models
- [ ] Are `OrderRequest` and `OrderResult` fully decoupled from the broker's native JSON/SDK structure?
- [ ] Are all prices, quantities, balances, and P&L metrics typed as `decimal.Decimal`?
- [ ] Are standard Enums (`OrderSide`, `OrderType`, `OrderStatus`) utilized strictly across all interfaces?
- [ ] Are `client_order_id`s uniquely generated and tracked throughout the order lifecycle?

## Base Interface & Implementation
- [ ] Does the new adapter inherit cleanly from `BaseBrokerAdapter`?
- [ ] Are all `@abstractmethod` implementations present and correctly signature-typed?
- [ ] Does the `normalize_status()` method exhaustively cover the broker's native string statuses?
- [ ] Is error handling wrapped in the standardized exception hierarchy (e.g., `OrderExecutionError`, `NetworkError`) rather than leaking `requests` or `SDK` exceptions?

## Factory Registry & Tests
- [ ] Is the new adapter successfully registered in `BrokerAdapterFactory` using a lowercase key?
- [ ] Do unit tests directly invoke the factory using `BrokerAdapterFactory.create('broker_name')`?
- [ ] Are order placement edge cases (like zero or negative quantity) validated and rejected gracefully?
- [ ] Do 100% of unit tests pass under `python -m unittest test_broker_adapter.py`?
