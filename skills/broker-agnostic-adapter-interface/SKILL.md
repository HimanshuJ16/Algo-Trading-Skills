---
name: broker-agnostic-adapter-interface
description: Use when designing quantitative trading systems to decouple strategy
  logic from broker APIs using a unified abstract adapter interface, standardized
  order models, and pluggable broker factories. Enforces Decimal arithmetic at the
  boundary, typed exceptions, and cross-venue status normalization that reports an
  unrecognized broker status as UNKNOWN rather than guessing the order is live.
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- adapter-pattern
- broker-agnostic
- trading-architecture
- order-routing
- status-normalization
brokers_frameworks:
- Zerodha Kite Connect
- Alpaca Trading API
- Interactive Brokers TWS API
- Upstox API
version: "3.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever building a trading platform or strategy engine intended to run
across multiple brokers or venues. Coupling strategy code directly to broker SDKs
(Zerodha `kiteconnect`, Alpaca `alpaca-py`, IBKR `ibapi`) fragments the codebase, leaks
float imprecision into prices, and makes venue migration a rewrite. An abstract
`BaseBrokerAdapter` with standardized models (`OrderRequest`, `OrderResult`, `Position`,
`AccountBalance`) in `Decimal` isolates strategy code from broker API drift.

The two things this layer must get right, because everything downstream trusts it: the
**status normalization** (does the strategy believe this order is live?) and the
**request validation** (does a malformed order reach the venue?).

## When NOT to Use

- **As a working broker client.** `scripts/broker_adapter.py` defines the contract and
  ships *simulated* adapters that fabricate fills. It performs no network I/O, no auth,
  no rate limiting. Real adapters wrap real SDKs behind this interface.
- **As a single-broker abstraction.** If you will only ever trade one venue, the
  indirection costs more than it saves; use the SDK directly and keep the enum
  normalization.
- **For anything the interface does not model.** Bracket/OCO orders, order modification,
  multi-leg and options strategies, streaming order updates, and per-venue product types
  (Zerodha's MIS/CNC/NRML) are outside `OrderRequest`. Extend the model deliberately
  rather than smuggling them through a broker-specific side channel — that reintroduces
  the coupling this skill exists to remove.
- **As a substitute for idempotency or auth handling** — see the Related Skills.

## Prerequisites

- Python `abc` for the interface and `decimal.Decimal` for all monetary values.
- Each broker's **documented status enumeration**, not the four statuses you happened to
  see in testing. `references/standards.md` lists them with sources.
- A typed exception hierarchy (`BrokerAdapterError` and subclasses) that every adapter
  maps its SDK errors into.
- A registry key per broker, supplied by configuration.

## Workflow

1. **Model the domain in `Decimal`, and enforce it at the boundary.** `_to_decimal`
   accepts `Decimal` and `int` (exact) and **rejects `float`** with an explanatory error.
   A float that slips through survives every comparison and only fails much later, as a
   `TypeError` the first time it meets a `Decimal` in arithmetic — far from the code that
   introduced it.

2. **Validate the request on the base class, not in each adapter.** `_validate_request`
   enforces symbol, enum types, finite positive quantity, and order-type/price
   consistency: LIMIT and STOP_LIMIT require a positive price, STOP and STOP_LIMIT
   require a positive stop price, and MARKET must **not** carry one. Putting it on the
   base class means a newly written adapter cannot forget it.

3. **Normalize status conservatively.** `normalize_status` upper-cases and looks up
   `_STATUS_MAP`. **An unmapped status returns `OrderStatus.UNKNOWN` and logs at ERROR —
   never `PENDING`.** Handle `UNKNOWN` by re-querying or reconciling; it is neither live
   nor terminal, and `OrderResult.is_terminal` returns False for it. Treat its appearance
   as a defect report: the broker has a status your map does not know.

4. **Echo `client_order_id` on every `OrderResult`.** Without it the caller cannot
   correlate a response to the request that produced it, and retry-safe submission is
   impossible.

5. **Register adapters explicitly; the factory registry starts empty.** `create()` raises
   until you register a real adapter. The simulated adapters are **not** bound to
   production broker names by default — call `register_simulated_adapters()` to opt in
   for offline work. `register()` rejects any class that is not a `BaseBrokerAdapter`, so
   a bad wiring fails at startup rather than on the first order.

6. **Treat `cancel_order` as a request, not a cancellation.** A `True` return means the
   broker accepted the cancellation *request*. The order can still fill in the race
   window. Confirm with `get_order_status` before releasing risk budget or reusing the ID.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Documented status sets per broker, with sources: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Defaulting an unrecognized status to PENDING.** This is the worst default available:
  it asserts the order is live and working. Real terminal statuses get missed by
  hand-written maps — Zerodha's `LAPSED`, IBKR's `ApiCancelled`, Alpaca's `done_for_day`
  and `replaced` are all finished, and calling any of them PENDING leaves the strategy
  waiting on a dead order or re-sending one that already resolved.
- **Case-sensitive status lookup.** IBKR returns mixed-case strings (`PreSubmitted`,
  `ApiCancelled`). A lookup that matches `"Filled"` but not `"FILLED"` sends the variant
  to the default branch — a filled order reported as still working.
- **Mapping only the statuses you saw in testing.** Kite documents roughly a dozen order
  states and Alpaca a dozen more; the four obvious ones are not the contract.
- **Binding simulated adapters to production broker names.** A factory that resolves
  `config["broker"]` to a mock reports every order FILLED at an invented price, with no
  error anywhere.
- **Falsy-checking a price.** `if request.price` treats `Decimal("0")` as absent, so a
  zero limit price gets silently replaced by a default instead of rejected.
- **Floating point at the boundary.** `float` cannot represent ordinary decimal prices
  and tick sizes exactly; construct `Decimal(str(value))` where the value enters.
- **Leaky abstractions.** Broker SDK exceptions, raw JSON, and — easy to miss —
  `decimal.InvalidOperation` from a `NaN` comparison must all be wrapped into
  `BrokerAdapterError` subclasses before crossing the adapter boundary.
- **Treating a cancel acknowledgement as a cancellation.**
- **Mutating the shared registry from library code.** It is process-wide class state; use
  `reset()` for test isolation.

## Verification

- Run the unit suite and confirm every test passes:
  `python -m unittest discover -s skills/broker-agnostic-adapter-interface/scripts`
- For each adapter, assert an invented status string returns `UNKNOWN`, not `PENDING`.
  This is the highest-value single assertion in the suite.
- Assert every status in the broker's **documented** enumeration maps to something, and
  that terminal broker states map to terminal `OrderStatus` values.
- Assert case variants (`"Filled"`, `"FILLED"`, `"filled"`) normalize identically.
- Assert a `float` quantity or price is rejected, and that an `int` widens to `Decimal`
  losslessly.
- Assert `create()` raises on an empty registry, and that `register()` refuses a class
  that does not implement `BaseBrokerAdapter`.
- Place orders through every adapter and confirm `filled_quantity`, `average_price` and
  `commission` are all `Decimal` instances.

## Related Skills

- `order-placement-idempotency`
- `broker-api-idempotent-cancel-requests`
- `headless-broker-auth-patterns`
- `multi-broker-rate-limit-handling`
