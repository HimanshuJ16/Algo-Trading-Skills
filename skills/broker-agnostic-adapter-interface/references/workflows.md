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
   - Create subclass implementations for each targeted broker (`MockZerodhaAdapter`,
     `MockAlpacaAdapter`, `MockIBKRAdapter` are *simulated* references, not clients).
   - Define `_STATUS_MAP` on each class, keyed **upper-case**; the base class upper-cases
     before lookup so matching is case-insensitive. IBKR returns mixed-case strings
     (`PreSubmitted`, `ApiCancelled`), and a case-sensitive lookup would resolve `"Filled"`
     while sending `"FILLED"` to the unmapped branch.
   - Populate the map from the broker's **documented** enumeration (see
     `references/standards.md`), not from the statuses observed during testing.
   - Call `self._validate_request(request)` first in `place_order`. Validation lives on the
     base class precisely so a new adapter cannot omit it.
   - Echo `request.client_order_id` onto the returned `OrderResult` so callers can
     correlate responses with requests.
   - Map domain `OrderRequest` into broker SDK parameters and unpack broker responses back
     into a standard `OrderResult`.

4. **Register with Broker Factory:**
   - The registry **starts empty**. Register your real adapter explicitly; call
     `register_simulated_adapters()` only for offline work.
   - `register()` rejects non-`BaseBrokerAdapter` classes, so a mis-wired registration
     fails at startup rather than on the first order.
   - Registration is case-insensitive for config-based injection.
   - The registry is process-wide class state — use `reset()` for test isolation.

## Status Normalization Policy

An unmapped status resolves to `OrderStatus.UNKNOWN` and logs at ERROR. It must **not**
fall back to `PENDING`.

- `PENDING` asserts the order is live and working. Applied to an unclassified status, that
  is an active lie: the strategy waits on an order that is finished, or re-sends one that
  already resolved.
- `UNKNOWN` is neither live nor terminal — `OrderResult.is_terminal` is False for it — so
  the correct handling is to re-query or reconcile, never to act.
- Its appearance is a defect report. Confirm the status's meaning against the broker's
  documentation, then add it to `_STATUS_MAP`.

## Failure Modes Observed in Production

- **Unknown-Status Fallback:** Defaulting an unrecognized broker status to `PENDING`.
  Terminal states a hand-written map misses — Kite `LAPSED`, IBKR `ApiCancelled`, Alpaca
  `done_for_day` / `replaced` — are then reported as working orders.
- **Case-Sensitive Status Lookup:** Matching `"Filled"` but not `"FILLED"`, so a filled
  order reads as still pending.
- **Precision Loss / Rounding Errors:** Passing standard floats into exchange APIs which
  expect strict tick sizes, leading to order rejection. Reject `float` at the boundary
  rather than coercing it.
- **Falsy Price Checks:** `if request.price` treats `Decimal("0")` as absent, so a zero
  limit price is silently replaced by a default instead of rejected.
- **Leaky Abstraction Leaks:** Permitting broker SDK exceptions (like
  `requests.exceptions.Timeout` underlying an SDK) to spill past the adapter — and, easily
  missed, `decimal.InvalidOperation` raised by comparing a `NaN` quantity. Catch these and
  raise a standardized `NetworkError` / `OrderExecutionError`.
- **Simulated Adapters Under Production Names:** A factory that resolves `config["broker"]`
  to a mock reports every order FILLED at an invented price, silently.
- **Cancel Acknowledgement Mistaken For Cancellation:** `cancel_order` returning True means
  the request was accepted; the order can still fill before the venue acts on it.

## Production Implementation Reference

- Primary interface: `scripts/broker_adapter.py`
- Automated test suite: `scripts/test_broker_adapter.py`
