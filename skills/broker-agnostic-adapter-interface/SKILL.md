---
name: broker-agnostic-adapter-interface
description: >-
  Use when designing quantitative trading systems to decouple strategy logic from broker APIs using a unified abstract adapter interface, standardized order models, and pluggable broker factories
domain: algorithmic-trading
subdomain: broker-integration
tags: ["broker-integration", "adapter-pattern", "broker-agnostic", "trading-architecture", "order-routing"]
brokers_frameworks: ["Zerodha Kite", "Alpaca", "Interactive Brokers", "Upstox"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever building a trading platform or strategy engine intended to run across multiple brokers or exchange venues. Coupling strategy code directly to specific broker SDKs (e.g. Zerodha `kiteconnect`, Alpaca `alpaca-py`, or IBKR `ibapi`) creates codebase fragmentation and prevents seamless venue migration. Designing an abstract broker adapter interface (`BaseBrokerAdapter`) with standardized data models (`OrderRequest`, `OrderResult`, `Position`, `AccountBalance`) isolates strategy code from broker API drift.

## Prerequisites

- Python `abc` (Abstract Base Classes) module for strict interface definition.
- Unified enum definitions for order sides (`BUY`, `SELL`), order types (`MARKET`, `LIMIT`), and order statuses (`PENDING`, `FILLED`, `REJECTED`).
- Factory pattern registry for dynamic broker adapter instantiation.

## Workflow

1. **Define Standardized Domain Data Models**:
   - Create unified dataclasses for `OrderRequest`, `OrderResult`, `Position`, and `AccountBalance`. Normalize broker-specific field names (e.g. `qty` vs `quantity` vs `shares`) into single standard attributes.

2. **Define Abstract Base Adapter Class (`BaseBrokerAdapter`)**:
   - Declare mandatory abstract methods `@abstractmethod`:
     - `place_order(order_request: OrderRequest) -> OrderResult`
     - `cancel_order(order_id: str) -> bool`
     - `get_order_status(order_id: str) -> OrderResult`
     - `get_positions() -> List[Position]`
     - `get_account_balance() -> AccountBalance`

3. **Implement Concrete Broker Adapters**:
   - Subclass `BaseBrokerAdapter` for each broker (e.g. `ZerodhaAdapter`, `AlpacaAdapter`, `IBKRAdapter`). Translate standard `OrderRequest` into broker-specific payloads and translate broker API responses back into standard models.

4. **Register with Broker Factory**:
   - Implement `BrokerAdapterFactory.register("broker_name", AdapterClass)` to enable runtime instantiation of broker adapters via configuration files.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Leaky Abstractions**: Allowing broker-specific exception types or raw JSON dictionary responses to leak past the adapter boundary into strategy logic.
- **Inconsistent Enum Normalization**: Leaving status strings like `"COMPLETE"` (Zerodha) vs `"filled"` (Alpaca) un-normalized.
- **Tight Coupling to Specific SDKs**: Hardcoding SDK dependencies inside strategy modules instead of injecting abstract `BaseBrokerAdapter` instances.

## Verification

- Instantiate multiple concrete adapters (`ZerodhaAdapter`, `AlpacaAdapter`) via `BrokerAdapterFactory`.
- Execute `place_order()` across different adapters and verify responses return uniform `OrderResult` objects.
- Verify status normalization maps broker-specific status codes to standard `OrderStatusEnum`.
- Run unit test suite `python scripts/test_broker_adapter.py` and confirm 100% pass rate.

## Related Skills

- `order-placement-idempotency`
- `headless-broker-auth-patterns`
- `multi-broker-rate-limit-handling`
---
