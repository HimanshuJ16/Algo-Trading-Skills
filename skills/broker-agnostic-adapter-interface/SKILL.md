---
name: broker-agnostic-adapter-interface
description: Use when designing quantitative trading systems to decouple strategy
  logic from broker APIs using a unified abstract adapter interface, standardized
  order models, and pluggable broker factories. This institutional-grade implementation
  utilizes precise Decimal arithmetic for all currency calculations, robust typed
  exceptions, and normalized cross-venue status mapping.
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- adapter-pattern
- broker-agnostic
- trading-architecture
- order-routing
- institutional-grade
brokers_frameworks:
- Zerodha Kite
- Alpaca
- Interactive Brokers
- Upstox
version: '2.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever building a trading platform or strategy engine intended to run across multiple brokers or exchange venues. Coupling strategy code directly to specific broker SDKs (e.g. Zerodha `kiteconnect`, Alpaca `alpaca-py`, or IBKR `ibapi`) creates codebase fragmentation, risks floating point inaccuracies, and prevents seamless venue migration. Designing an abstract broker adapter interface (`BaseBrokerAdapter`) with standardized data models (`OrderRequest`, `OrderResult`, `Position`, `AccountBalance`) using `Decimal` isolates strategy code from broker API drift and preserves financial precision.

## Prerequisites

- Python `abc` (Abstract Base Classes) module for strict interface definition.
- Python `decimal.Decimal` for exact arithmetic on quantities and prices to prevent float truncation.
- Unified enum definitions for order sides (`BUY`, `SELL`), order types (`MARKET`, `LIMIT`, `STOP`), and order statuses (`PENDING`, `FILLED`, `REJECTED`, etc.).
- Robust custom exception hierarchy (`BrokerAdapterError`, `OrderExecutionError`, etc.).
- Factory pattern registry for dynamic broker adapter instantiation.

## Workflow

1. **Define Standardized Domain Data Models**:
   - Create unified dataclasses for `OrderRequest`, `OrderResult`, `Position`, and `AccountBalance`.
   - Ensure all price, quantity, and balance fields strictly utilize `Decimal`.
   - Normalize broker-specific field names into standard attributes (e.g., standardizing `commissions` and `timestamps`).

2. **Define Abstract Base Adapter Class (`BaseBrokerAdapter`)**:
   - Declare mandatory abstract methods `@abstractmethod`:
     - `place_order(request: OrderRequest) -> OrderResult`
     - `cancel_order(order_id: str) -> bool`
     - `get_order_status(order_id: str) -> OrderResult`
     - `get_positions() -> List[Position]`
     - `get_account_balance() -> AccountBalance`
     - `normalize_status(broker_status: str) -> OrderStatus`

3. **Implement Concrete Broker Adapters**:
   - Subclass `BaseBrokerAdapter` for each broker (e.g., `MockZerodhaAdapter`, `MockAlpacaAdapter`, `MockIBKRAdapter`). 
   - Translate standard `OrderRequest` into broker-specific payloads.
   - Implement the `normalize_status` mapping dictionary to translate string-based broker responses into standardized `OrderStatus` Enum values.

4. **Register with Broker Factory**:
   - Implement `BrokerAdapterFactory.register("broker_name", AdapterClass)` to enable runtime instantiation of broker adapters via configuration strings.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Floating Point Truncation**: Using standard Python `float` instead of `Decimal` for currency pairs or crypto quantities. Always use exact precision.
- **Leaky Abstractions**: Allowing broker-specific exception types (like `kiteconnect.exceptions.NetworkException`) or raw JSON dictionary responses to leak past the adapter boundary into strategy logic.
- **Inconsistent Enum Normalization**: Leaving status strings like `"COMPLETE"` (Zerodha) vs `"filled"` (Alpaca) vs `"Submitted"` (IBKR) un-normalized.
- **Tight Coupling to Specific SDKs**: Hardcoding SDK dependencies inside strategy modules instead of injecting abstract `BaseBrokerAdapter` instances.

## Verification

- Instantiate multiple concrete adapters (`MockZerodhaAdapter`, `MockAlpacaAdapter`, `MockIBKRAdapter`) via `BrokerAdapterFactory`.
- Execute `place_order()` across different adapters and verify responses return uniform `OrderResult` objects with `Decimal` metrics.
- Verify status normalization accurately maps diverse broker-specific status codes to standard `OrderStatus`.
- Run unit test suite `python -m unittest test_broker_adapter.py` and confirm 100% pass rate.

## Related Skills

- `order-placement-idempotency`
- `headless-broker-auth-patterns`
- `multi-broker-rate-limit-handling`
