---
name: broker-order-type-capability-matrix
description: Use when building multi-broker quantitative trading systems to maintain
  a capability matrix of native order types (Bracket, OCO, Trailing Stop, Iceberg,
  PEG, TWAP, VWAP) supported by each broker, and synthesize software-emulated order
  triggers (via local EMS) when native support is missing.
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- order-types
- capability-matrix
- bracket-orders
- oco-orders
- synthetic-orders
- execution-algorithms
brokers_frameworks:
- Multi-Broker Capability Matrix
- Python Order Synthesizer
version: '2.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when deploying algorithmic strategies across diverse broker APIs where native order type support varies significantly. For example, Interactive Brokers supports native Iceberg, TWAP, and OCO orders, whereas Alpaca or crypto spot exchanges may support only basic Market, Limit, and Stop orders. Submitting unsupported native order types leads to API rejections; this skill validates native support and automatically synthesizes software-emulated bracket, OCO, TWAP, and Iceberg order triggers for local EMS execution when needed.

## Prerequisites

- Target broker list and supported native order types (`MARKET`, `LIMIT`, `STOP_LIMIT`, `TRAILING_STOP`, `BRACKET`, `OCO`, `ICEBERG`, `PEGGED`, `MOC`, `TWAP`, `VWAP`).
- Local synthetic order execution engine (EMS) for emulating unsupported order types and managing local triggers/slices.

## Workflow

1. **Query Broker Order Capabilities**:
   - Query `BrokerOrderCapabilityMatrix` for target broker (e.g., `IBKR`, `Alpaca`, `Binance`, `Zerodha`).

2. **Pre-Validate Proposed Order Type**:
   - Check if requested order type (e.g., `BRACKET` or `TWAP`) is natively supported by the target broker.

3. **Software Emulation Fallback (Synthesizer)**:
   - If native support is missing, decompose complex order into primary market/limit order and register local synthetic triggers (for stop-loss and take-profit legs) or feeders (for Iceberg/TWAP slices).

4. **Order Execution & Registration**:
   - Dispatch primary native order legs via broker API and register emulated legs in local memory or database state for trigger checking.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Assuming Native OCO Atomicity**: Software-emulated OCO orders carry latency risk where one leg fills on exchange while local cancellation of the second leg is delayed, risking double execution.
- **Iceberg Size Floor Rejection**: Submitting synthetic iceberg orders with slice sizes below exchange minimum lot sizes.
- **Emulated Order State Loss**: If the local EMS crashes, the emulated stop-losses or profit-takes will fail to trigger. State persistence is critical.

## Verification

- Query matrix for IBKR vs Alpaca and verify native vs emulated capability flags.
- Submit synthetic Bracket order to a broker lacking native support and verify software decomposition.
- Run `python scripts/test_capability_matrix.py` and confirm 100% pass rate.

## Related Skills

- `broker-agnostic-adapter-interface`
- `execution-algo-twap-vwap-slicing`
- `paper-to-live-promotion-checklist`
---
