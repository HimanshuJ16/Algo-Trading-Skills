# Deep Workflow Reference — broker-agnostic-adapter-interface

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Define Domain Data Models:**
   - Define immutable/normalized dataclasses (`OrderRequest`, `OrderResult`, `Position`, `AccountBalance`).
   - Define normalized enums (`OrderSide`, `OrderType`, `OrderStatus`).

2. **Construct Abstract Base Class Interface:**
   - Inherit from Python `abc.ABC`.
   - Require `@abstractmethod` implementations for `place_order`, `cancel_order`, `get_positions`, and `get_account_balance`.

3. **Develop Concrete Broker Adapters:**
   - Create subclass implementations for each targeted broker (`ZerodhaAdapter`, `AlpacaAdapter`, `IBKRAdapter`).
   - Map domain `OrderRequest` into broker SDK parameters and map broker responses into standard `OrderResult`.

4. **Register with Broker Factory:**
   - Add adapter classes to `BrokerAdapterFactory` for dynamic runtime resolution from configuration.

## Failure Modes Observed in Production

- **Leaky Abstraction Leaks:** Allowing broker-specific exception types or raw HTTP dictionaries to spill past the adapter interface into strategy code.
- **Un-Normalized Enum Mapping:** Failing to translate broker status codes (`"COMPLETE"` vs `"filled"`) into unified `OrderStatus` values.

## Production Implementation Reference

- Reference code: `scripts/broker_adapter.py` (`BaseBrokerAdapter`, `BrokerAdapterFactory`, `OrderRequest`, `OrderResult`).
- Automated unit tests: `scripts/test_broker_adapter.py`.
