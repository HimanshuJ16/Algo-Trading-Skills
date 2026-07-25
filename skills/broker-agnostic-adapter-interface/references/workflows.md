# Deep Workflow Reference — broker-agnostic-adapter-interface

This file holds the full institutional-grade technical procedure referenced by `SKILL.md`. Load this when actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Define Robust Domain Data Models:**
   - Define immutable/normalized dataclasses (`OrderRequest`, `OrderResult`, `Position`, `AccountBalance`).
   - Use `decimal.Decimal` extensively for prices, quantities, balances, and P&L calculations.
   - Embed universal identifiers (e.g., `uuid4` for `client_order_id`).
   - Define normalized enums (`OrderSide`, `OrderType`, `OrderStatus`).

2. **Construct Abstract Base Class Interface:**
   - Inherit from Python `abc.ABC`.
   - Establish standard custom exception classes (`BrokerAdapterError`, `OrderExecutionError`, `NetworkError`) for uniform error trapping.
   - Require `@abstractmethod` implementations for `place_order`, `cancel_order`, `get_order_status`, `get_positions`, `get_account_balance`, and `normalize_status`.

3. **Develop Concrete Broker Adapters:**
   - Create subclass implementations for each targeted broker (`MockZerodhaAdapter`, `MockAlpacaAdapter`, `MockIBKRAdapter`).
   - Internally define `_STATUS_MAP` dictionaries on each class to handle raw broker strings.
   - Validate incoming models (e.g. `request.quantity <= 0` raises `OrderExecutionError`).
   - Map domain `OrderRequest` into broker SDK parameters and reliably unpack broker responses back into standard `OrderResult`.

4. **Register with Broker Factory:**
   - Add adapter classes to `BrokerAdapterFactory` using class methods.
   - Enforce case-insensitive registration (`name.lower()`) for seamless config-based injection.

## Failure Modes Observed in Production

- **Precision Loss / Rounding Errors:** Passing standard floats into exchange APIs which expect strict tick sizes, leading to order rejection. Use `Decimal`.
- **Leaky Abstraction Leaks:** Permitting broker SDK exceptions (like `requests.exceptions.Timeout` underlying an SDK) to spill past the adapter. The adapter should catch these and raise a standardized `NetworkError`.
- **Un-Normalized Enum Mapping:** Failing to translate broker status codes (`"COMPLETE"`, `"Inactive"`) into unified `OrderStatus` values, which breaks universal strategy reconciliation loops.

## Production Implementation Reference

- Primary interface: `scripts/broker_adapter.py`
- Automated test suite: `scripts/test_broker_adapter.py`
