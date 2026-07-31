---
name: nyse-arca-integrated-feed-handling
description: >-
  NYSE Arca Integrated Feed (XDP) binary protocol parsing engine unpacking Add Order (100), Modify Order (101), Delete Order (102), and Execution (103) messages for L3 order book reconstruction.
domain: Market Microstructure & Latency
subdomain: Binary Feed Parsing & L3 Order Book Reconstruction
tags: ["nyse-arca", "xdp-protocol", "integrated-feed", "binary-feed", "l3-order-book", "little-endian", "struct-unpack"]
brokers_frameworks: ["NYSE XDP Common Client Spec", "Python Struct Binary Unpacking", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when ingesting raw binary direct market data from NYSE, NYSE Arca, or NYSE American exchanges using the Exchange Data Protocol (XDP). The NYSE Integrated Feed delivers full order book depth (L3 depth), order modifications, cancels, executions, and stock imbalances. Unpacking binary XDP packets using little-endian byte layouts (`<`), 4-decimal price scaling ($Price / 10,000.0$), and symbol index mapping enables high-frequency trading engines to maintain precise order book depth and track queue positions.

## Prerequisites

- NYSE Integrated Feed (XDP) binary packet byte stream over UDP multicast.
- Binary field format specification (Little-endian `<` struct unpacking, 4-decimal price divisor $10,000.0$, SymbolIndex mapping).

## Workflow

1. **XDP Packet & Message Header Unpacking**:
   - Read Packet Header (16 bytes: `PktSize`, `DeliveryFlag`, `NumberMsgs`, `SeqNum`, `SendTime`, `SendTimeNS`).
   - Read Message Header (4 bytes: `MsgSize`, `MsgType`).
2. **Message Payload Unpacking**:
   - Unpack message payload using `<` little-endian struct format:
     - **Add Order (`MsgType = 100`)**: `OrderID` (8B), `Price` (4B int $/ 10,000$), `Volume` (4B), `Side` (1B: `'B'`/`'S'`), `SymbolIndex` (4B).
     - **Modify Order (`MsgType = 101`)**: `OrderID` (8B), `Price` (4B int $/ 10,000$), `Volume` (4B).
     - **Delete Order (`MsgType = 102`)**: `OrderID` (8B).
     - **Execution (`MsgType = 103`)**: `OrderID` (8B), `Price` (4B int $/ 10,000$), `Volume` (4B).
3. **L3 Order Book Maintenance**:
   - Maintain `active_orders` map by `OrderID`. Update open volume on execution/modification, and delete entry on zero volume or Delete Order message.
4. **Audit Report Generation**: Output structured `NYSEFeedReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using Big-Endian Instead of Little-Endian**: Using `>` format specifier instead of `<` little-endian, corrupting XDP sequence numbers, prices, and OrderIDs.
- **Forgetting Symbol Index Mapping**: Failing to map numeric `SymbolIndex` IDs to ticker symbols (e.g. `101` $\rightarrow$ `SPY`), breaking symbol attribution.
- **Handling Multi-Message Packets Incorrectly**: Reading only the first message in an XDP packet instead of iterating through `NumberMsgs`.

## Verification

- Instantiate `NYSEArcaIntegratedFeedEngine`. Pack XDP Add Order (MsgType 100) for `SPY` @ $450.00 (4500000 int price, 500 shares, OrderID 88001) $\implies$ unpack and verify order state. Pack Order Execution (MsgType 103) for 200 shares $\implies$ verify 300 remaining shares. Pack Delete Order (MsgType 102) $\implies$ verify order deletion.
- Run `python scripts/test_nyse_arca_integrated_feed_handling.py`.

## Related Skills

- `nasdaq-totalview-itch-feed-parsing`
- `binary-protocol-parsing-for-low-latency-feeds`
---
