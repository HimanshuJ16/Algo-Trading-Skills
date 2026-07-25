---
name: broker-order-type-capability-matrix
description: >-
  Use when building multi-broker quantitative trading systems to maintain a capability matrix of native order types (Bracket, OCO, Trailing Stop, Iceberg, PEG) supported by each broker, and synthesize software-emulated order triggers when native support is missing.
domain: algorithmic-trading
subdomain: broker-integration
tags: ["broker-integration", "order-types", "capability-matrix", "bracket-orders", "oco-orders", "synthetic-orders"]
brokers_frameworks: ["Multi-Broker Capability Matrix", "Python Order Synthesizer"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when deploying algorithmic strategies across diverse broker APIs where native order type support varies significantly. For example, Interactive Brokers supports native Iceberg and OCO orders, whereas Alpaca or crypto exchanges support only basic Market, Limit, and Stop orders. Submitting unsupported native order types leads to API rejections; this skill validates native support and automatically synthesizes software-emulated bracket and OCO order triggers when needed.

## Prerequisites

- Target broker list and supported native order types (`MARKET`, `LIMIT`, `STOP_LIMIT`, `TRAILING_STOP`, `BRACKET`, `OCO`, `ICEBERG`).
- Local synthetic order execution engine for emulating unsupported order types.

## Workflow

1. **Query Broker Order Capabilities**:
   - Query `BrokerOrderCapabilityMatrix` for target broker (e.g., `IBKR`, `Alpaca`, `Binance`, `Zerodha`).

2. **Pre-Validate Proposed Order Type**:
   - Check if requested order type (e.g., `BRACKET`) is natively supported by the target broker.

3. **Software Emulation Fallback (Synthesizer)**:
   - If native support is missing, decompose complex order into primary market/limit order and register local synthetic triggers for stop-loss and take-profit legs.

4. **Order Execution & Registration**:
   - Dispatch order via broker API with full capability compliance.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Assuming Native OCO Atomicity**: Software-emulated OCO orders carry latency risk where one leg fills on exchange while local cancellation of the second leg is delayed.
- **Iceberg Size Floor Rejection**: Submitting synthetic iceberg orders with slice sizes below exchange minimum lot sizes.
- **Unmapped Broker Error Codes**: Treating capability rejections (e.g. HTTP 400 "Order type not supported") as general connection errors.

## Verification

- Query matrix for IBKR vs Alpaca and verify native vs emulated capability flags.
- Submit synthetic Bracket order to a broker lacking native support and verify software decomposition.
- Run `python scripts/test_capability_matrix.py` and confirm 100% pass rate.

## Related Skills

- `broker-agnostic-adapter-interface`
- `execution-algo-twap-vwap-slicing`
- `paper-to-live-promotion-checklist`
---
