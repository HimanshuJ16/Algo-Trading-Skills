---
name: australian-securities-exchange-asx-api
description: Institutional API integration adapter for the Australian Securities Exchange
  (ASX), supporting FIX 5.0 SP2, OUCH, and ITCH protocols.
domain: global-market-integration
subdomain: exchanges
tags:
- asx
- australia
- fix
- ouch
- itch
- market-connectivity
brokers_frameworks:
- direct-market-access
version: 1.1.0
author: System
license: MIT
---

## When to Use

Use this skill when building direct market access (DMA) connectivity to the Australian Securities Exchange (ASX). This engine provides the foundational configuration and state management required to interface with the ASX Customer Development Environment (CDE) or Production via the Australian Liquidity Centre (ALC).

It supports routing configurations for:
- **FIX 5.0 SP2**: Standard institutional order routing and drop-copy.
- **OUCH**: Ultra-low latency binary order entry.
- **ITCH**: Ultra-low latency binary multicast market data.

## Prerequisites

- Python 3.9+
- Exchange-assigned `CompID` and cross-connect details.
- For OUCH/ITCH protocols, the trading infrastructure must be co-located in the Australian Liquidity Centre (ALC).

## Workflow

1. **Protocol Selection**: The quant configures the `AsxConnectionConfig` with the required `AsxProtocol` (FIX, OUCH, or ITCH).
2. **ALC Validation**: The engine validates the topology. (e.g., OUCH binary order entry is rejected if the system is not flagged as ALC co-located, as remote OUCH routing is an anti-pattern).
3. **Session Initialization**: The engine initializes the TCP/IP or Multicast session state depending on the protocol.
4. **CDE Testing**: All endpoints must be pointed to the ASX CDE prior to production deployment.

## Common Pitfalls

- **Using FIX for HFT**: FIX 5.0 is excellent for standard execution algorithms (VWAP, TWAP), but utilizing FIX for latency-arbitrage on the ASX is a pitfall. Market making bots must be routed via OUCH.
- **Remote OUCH**: Attempting to route OUCH binary messages over a standard internet gateway (ASX Net Global) instead of an ALC cross-connect.

## Verification

Run `python scripts/test_australian_securities_exchange_asx_api.py` to confirm that the ALC topology rules and connection state handlers work as expected.

## Related Skills

- `fix-protocol-session-management-across-venues`
- `binary-protocol-parsing-for-low-latency-feeds`
