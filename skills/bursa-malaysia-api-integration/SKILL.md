---
name: bursa-malaysia-api-integration
description: Institutional-grade FIX 5.0 SP1 engine for Bursa Malaysia BTS2 (Bursa
  Trade Securities 2) trading gateway integration.
domain: Execution
subdomain: Venue Integration
tags:
- bursa-malaysia
- fix-protocol
- bts2
- order-routing
- asian-equities
brokers_frameworks:
- Bursa Malaysia BTS2
- FIX 5.0 SP1
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when building direct market access (DMA) connectivity to Bursa Malaysia's BTS2 gateway. Institutional order flow to Bursa Malaysia must be routed over their standardized FIX 5.0 SP1 interface. This integration handles the specific constraints of the BTS2 engine, including the `FIXTRADER` and `FIXNEGDEAL` target identifiers required for lit-orderbook and direct business transactions.

## Prerequisites

- Approved BTS2-A1 onboarding form from Bursa Malaysia.
- Site-to-Site VPN or Bursa Connectivity Services (BCS) cross-connect.
- Assigned `SenderCompID` and `TargetCompID`.

## Workflow

1. **Establish Connectivity**: Establish a TCP connection over the VPN/cross-connect.
2. **Logon Sequence**: Send a FIX Logon (MsgType=A) specifying `FIX.5.0SP1`.
3. **Route Orders**: Construct and send `NewOrderSingle` (MsgType=D) messages with mandatory BTS2 specific fields (e.g., specific Broker Code mappings).
4. **Process Executions**: Listen asynchronously for `ExecutionReport` (MsgType=8) messages to track partial and full fills.
5. **Teardown**: Send Logout (MsgType=5) for clean session termination.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Incorrect TargetCompID**: Sending lit-market limit orders to the `FIXNEGDEAL` target instead of the `FIXTRADER` target.
- **Heartbeat Timeout**: Failing to respond to BTS2 `TestRequest` (MsgType=1) messages, resulting in sudden session drops.
- **Invalid Protocol Version**: Attempting to use FIX 4.2 or 4.4 when BTS2 strictly enforces FIX 5.0 SP1.

## Verification

- Simulate an end-to-end Limit order submission and partial fill execution report.
- Run `python scripts/test_bursa_malaysia_api_integration.py` to verify the state machine.

## Related Skills

- `borsa-istanbul-api-integration`
- `broker-failover-secondary-account-routing`
