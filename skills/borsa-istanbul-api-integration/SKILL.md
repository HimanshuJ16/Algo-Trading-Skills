---
name: borsa-istanbul-api-integration
description: Advanced institutional integration skill for Borsa Istanbul (BIST) via BISTECH FIX 5.0 SP2 and OUCH/ITCH protocols.
domain: Execution
subdomain: Venue Integration
tags:
  - borsa-istanbul
  - fix-protocol
  - bistech
  - order-routing
  - market-data
brokers_frameworks:
  - quickfix
  - bistech-api
version: 1.0.0
author: Quant Engineering
license: MIT
---

## When to Use

Use this skill when building direct market access (DMA), order routing, or high-frequency trading (HFT) infrastructure connecting directly to Borsa Istanbul's BISTECH platform. It covers handling FIX 5.0 SP2 session management, Order Entry (MsgType=D), Order Cancel (MsgType=F), and processing Execution Reports (MsgType=8) accurately.

## Prerequisites

- Python 3.9+
- Network connectivity to BISTECH FIX Gateways or BIST Simulator environments.
- Approved SenderCompID and TargetCompID from Borsa Istanbul.
- BISTECH FIX Certification (required for production).

## Workflow

1. Initialize BIST FIX engine configuration (`BISTConfig`).
2. Establish a FIX session (Logon, MsgType=A) and maintain heartbeat.
3. Construct validated `FIXOrder` objects (validating limit prices, quantities, symbols like THYAO.E).
4. Transmit orders via the integration engine and track `client_order_id` mappings.
5. Process asynchronous `ExecutionReport` messages to update internal order state, filled quantities, and VWAP.

## Common Pitfalls

- Failing to manage sequence numbers correctly on disconnection, leading to Resend Requests.
- Not implementing BIST-specific required FIX tags (e.g., specific party roles or account types).
- Handling partial fills incorrectly and losing track of remaining open quantity.
- Connecting to the wrong environment (UAT/Simulator vs Production) without properly configuring TLS/VPN.

## Verification

- Run unit tests strictly mocking BISTECH Execution Reports.
- Perform connectivity tests against the official BIST FIX Simulator.

## Related Skills

- fix-protocol-fundamentals
- direct-market-access
- market-data-itch
