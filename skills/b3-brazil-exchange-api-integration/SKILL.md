---
name: b3-brazil-exchange-api-integration
description: Connectivity engine for the B3 Exchange (Brasil Bolsa Balcão) PUMA Trading System, supporting both Legacy (FIX/FAST) and Modern (Binary SBE) architectures.
domain: global-market-integration
subdomain: exchanges
tags:
  - b3
  - brazil
  - puma-trading-system
  - fix-fast
  - umdf
  - sbe
  - binary-order-entry
brokers_frameworks:
  - direct-market-access
version: 1.1.0
author: System
license: MIT
---

## When to Use

Use this skill when building Direct Market Access (DMA) to the **B3 (Brasil Bolsa Balcão) PUMA Trading System**. B3 currently operates a dual-protocol architecture. 

This engine allows quantitative trading desks to configure and manage connection states for either:
- **Legacy Stack**: FIX 4.4 for Order Entry and UMDF (FIX/FAST) for market data.
- **Modern Stack**: Binary Order Entry (BOE via FIXP) and Binary UMDF (SBE - Simple Binary Encoding) for ultra-low latency, Market-by-Order (MBO) data.

## Prerequisites

- Python 3.9+
- Network connectivity to the B3 Co-Location facility or a certified B3 network provider.
- Pre-certified B3 SenderCompID and TargetCompID.

## Workflow

1. **Protocol Selection**: Instantiate `B3ConnectionConfig` with `B3ProtocolSuite.MODERN_BINARY_SBE` for HFT algorithms, or `LEGACY_FIX_FAST` for standard execution algos.
2. **Topology Validation**: The engine enforces strict topological rules. For example, Binary UMDF (SBE) relies on UDP Multicast and requires explicit Application-Level Gap Recovery logic, as it lacks native TCP recovery channels.
3. **Session Management**: Call `connect()` to establish the socket connections and initialize the FIX/FIXP logon sequence.

## Common Pitfalls

- **Assuming TCP Recovery on SBE**: The modern B3 Binary UMDF feed does not have a native TCP recovery channel for gap filling. Missing UDP packets require the algo to handle sequence gaps manually. The engine actively warns developers if gap recovery is disabled on SBE.
- **Conflated vs. MBO Data**: Misunderstanding that Binary SBE provides un-conflated Market By Order (MBO) depth, whereas Legacy FAST may provide conflated updates depending on the feed configuration.

## Verification

Run `python scripts/test_b3_brazil_exchange_api_integration.py` to confirm that the engine properly enforces B3-specific architecture rules (like requiring gap recovery on Binary SBE).

## Related Skills

- `binary-protocol-parsing-for-low-latency-feeds`
- `fix-protocol-session-management-across-venues`
