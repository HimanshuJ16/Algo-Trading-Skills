---
name: iceberg-order-native-broker-support-vs-simulation
description: >-
  Execution routing engine for evaluating Native Exchange Iceberg orders vs Synthetic Client-Side Iceberg simulations, managing display slice randomization, and tracking refill latency.
domain: Execution Algorithms
subdomain: Order Slicing & Native Broker Capabilities
tags: ["iceberg-orders", "order-slicing", "native-iceberg", "synthetic-simulation", "display-quantity", "refill-latency", "smart-order-routing"]
brokers_frameworks: ["Interactive Brokers (TWS displaySize)", "Binance (icebergQty)", "CME / NASDAQ Native", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in smart order routers, institutional execution algorithms, and broker API integration gateways. Institutional orders (e.g. 50,000 shares) are sliced into smaller display sizes (e.g. 500 shares) to hide total order size and minimize adverse market impact. Venues supporting **Native Iceberg** (IBKR, Binance, CME) reload display slices directly on the exchange matching engine without client network roundtrips ($0\text{ ms}$ refill latency). Venues lacking native support require **Synthetic Simulation**, dispatching client-side child orders with slice size randomization ($\pm 20\%$) and network latency penalties ($20\text{ms} - 50\text{ms}$).

## Prerequisites

- Parent order request (`symbol`, `side`, `total_quantity`, `target_display_quantity`, `limit_price`).
- Broker venue capabilities (`broker_name`, `supports_native_iceberg`: `True`/`False`, `client_network_latency_ms`).

## Workflow

1. **Broker Venue Capability Audit**:
   - Audit `supports_native_iceberg`.
   - If `True` $\implies$ Route as `NATIVE_ICEBERG`.
   - If `False` $\implies$ Initialize `SYNTHETIC_SIMULATION` sliced child manager.
2. **Native Iceberg Payload Construction (`NATIVE_ICEBERG`)**:
   - Format native broker parameters (`displaySize` for IBKR, `icebergQty` for Binance, `DisplayQty` for CME).
   - Single parent order ID sent to exchange; refills executed at matching engine level ($0\text{ ms}$ latency).
3. **Synthetic Client-Side Slice Manager (`SYNTHETIC_SIMULATION`)**:
   - Calculate randomized child slice size: $Q_{\text{child}} = Q_{\text{display}} \times (1 \pm \text{variance\_pct})$.
   - Dispatch single child order. Upon fill event, wait for client network roundtrip ($\Delta t_{\text{refill}}$) and dispatch next child slice.
4. **Audit Report Generation**: Output structured `IcebergExecutionReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using Synthetic Icebergs on Native Venues**: Dispatching client-side synthetic child orders on IBKR or CME instead of native iceberg parameters, incurring unnecessary network latency and losing matching engine queue priority.
- **Fixed Display Slice Sizes on Synthetic Venues**: Dispatching identical child sizes (e.g., exactly 500 shares every time), enabling HFT algos to detect and front-run the synthetic iceberg pattern.
- **Ignoring Client Network Disconnects during Synthetic Execution**: Failing to handle WebSocket disconnects mid-way through a synthetic iceberg lifecycle, leaving remaining parent quantity unexecuted.

## Verification

- Instantiate `IcebergExecutionRouterEngine`. Route 10,000 share order on IBKR (`supports_native_iceberg=True`, `display=500`) $\implies$ verify engine routes `NATIVE_ICEBERG` with 1 parent order and $0\text{ ms}$ refill latency. Route 10,000 share order on Basic REST Broker (`supports_native_iceberg=False`, `display=500`, `variance=20%`) $\implies$ verify engine initializes `SYNTHETIC_SIMULATION`, randomizes child slices ($400 - 600$ shares), and tracks client refill latency penalty.
- Run `python scripts/test_iceberg_order_native_broker_support_vs_simulation.py`.

## Related Skills

- `iceberg-order-execution-algorithm`
- `algo-order-type-capability-matrix`
---
