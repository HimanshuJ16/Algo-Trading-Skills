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
- [ ] Does `place_order()` call `self._validate_request(request)` **before** touching the SDK?
- [ ] Does `OrderResult` echo the request's `client_order_id`?
- [ ] Is error handling wrapped in the standardized exception hierarchy (e.g., `OrderExecutionError`, `NetworkError`) rather than leaking `requests` or `SDK` exceptions — including `decimal.InvalidOperation` from non-finite values?
- [ ] Is `cancel_order()` documented and treated as a cancellation **request**, with the outcome confirmed via `get_order_status()` before the order is considered dead?

## Status Normalization
- [ ] Is `_STATUS_MAP` built from the broker's **documented** status enumeration (see `references/standards.md`), not from statuses observed during testing?
- [ ] Does an unrecognized status return `OrderStatus.UNKNOWN` — **never** `PENDING` — and log at ERROR?
- [ ] Are the broker's terminal states mapped to terminal `OrderStatus` values? Check the easily-missed ones: Kite `LAPSED`, IBKR `ApiCancelled`, Alpaca `done_for_day` and `replaced`.
- [ ] Do case variants (`"Filled"`, `"FILLED"`, `"filled"`) normalize identically?
- [ ] Is `UNKNOWN` handled downstream by re-query/reconcile rather than being treated as live or terminal?

## Factory Registry & Tests
- [ ] Is the new adapter successfully registered in `BrokerAdapterFactory` using a lowercase key?
- [ ] Is the registry free of simulated adapters in any process with live market access? (`register_simulated_adapters()` must never be called there.)
- [ ] Does `register()` reject classes that do not implement `BaseBrokerAdapter`, so mis-wiring fails at startup rather than on the first order?
- [ ] Do tests call `BrokerAdapterFactory.reset()` between cases, given the registry is process-wide class state?
- [ ] Are order placement edge cases validated and rejected: zero/negative/NaN/Infinity quantity, `float` quantity or price, LIMIT without price, `Decimal("0")` limit price, MARKET carrying a price, STOP without stop price?
- [ ] Do 100% of unit tests pass under `python -m unittest discover -s skills/broker-agnostic-adapter-interface/scripts`?
