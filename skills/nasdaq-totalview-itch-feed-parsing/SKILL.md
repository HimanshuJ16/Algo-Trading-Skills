---
name: nasdaq-totalview-itch-feed-parsing
description: >-
  Nasdaq TotalView-ITCH 5.0 binary protocol parsing engine unpacking Add Order (A), Order Executed (E), Cancel (X), and Delete (D) messages for L3 order book reconstruction.
domain: Market Microstructure & Latency
subdomain: Binary Feed Parsing & L3 Order Book Reconstruction
tags: ["nasdaq-itch", "binary-protocol", "totalview-itch", "itch-5.0", "l3-order-book", "feed-parsing", "struct-unpack"]
brokers_frameworks: ["Nasdaq TotalView-ITCH 5.0 Spec", "Python Struct Binary Unpacking", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when processing raw binary direct market data feeds from Nasdaq exchanges (Nasdaq, Nasdaq BX, Nasdaq PSX). Nasdaq TotalView-ITCH 5.0 delivers full order book depth (L3 depth) with nanosecond timestamps over MoldUDP64 or SoupBinTCP transport protocols. Unpacking binary ITCH 5.0 message structures using big-endian byte layouts (`>`) enables high-frequency trading engines to maintain precise order book state, track individual limit order queue positions, and compute micro-price dynamics.

## Prerequisites

- Nasdaq TotalView-ITCH 5.0 binary byte stream.
- Binary field layout specification (Big-endian `>` struct unpacking, 4-decimal price divisor $10,000.0$, 6-byte nanosecond timestamps).

## Workflow

1. **Binary Header & Message Type Inspection**:
   - Read 1-byte message type prefix (`'A'`, `'F'`, `'E'`, `'X'`, `'D'`, `'P'`).
2. **Big-Endian Struct Unpacking**:
   - Unpack message payload using `struct.unpack`:
     - **Add Order (`'A'`)**: `Stock Locate` (2B), `Tracking` (2B), `Timestamp` (6B), `OrderRefNum` (8B), `Buy/Sell` (1B), `Shares` (4B), `Stock` (8B), `Price` (4B int $/ 10,000$).
     - **Order Executed (`'E'`)**: `OrderRefNum` (8B), `ExecutedShares` (4B), `MatchNumber` (8B).
     - **Order Cancel (`'X'`)**: `OrderRefNum` (8B), `CanceledShares` (4B).
     - **Order Delete (`'D'`)**: `OrderRefNum` (8B).
3. **L3 Order Book State Maintenance**:
   - Maintain `orders_by_ref` map. Update active share counts upon execution/cancellation, and remove order entry on delete or zero remaining shares.
4. **Audit Report Generation**: Output structured `ITCHParserReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Wrong Endianness**: Unpacking ITCH binary fields with little-endian (`<`) or native endianness instead of big-endian (`>`), corrupting prices and order IDs.
- **Forgetting Price Scaling**: Failing to divide raw integer prices by $10,000.0$, resulting in $10,000\times$ inflated stock prices.
- **6-Byte Timestamp Overflows**: Misinterpreting 6-byte 48-bit nanosecond timestamps as 32-bit or 64-bit standard integers.

## Verification

- Instantiate `NasdaqITCH50ParserEngine`. Pack binary Add Order ('A') for AAPL @ $150.00 (1500000 int price, 100 shares, OrderRef 1001) $\implies$ unpack and verify order state. Pack Order Executed ('E') for 40 shares $\implies$ verify 60 remaining shares. Pack Order Delete ('D') $\implies$ verify order removal.
- Run `python scripts/test_nasdaq_totalview_itch_feed_parsing.py`.

## Related Skills

- `binary-protocol-parsing-for-low-latency-feeds`
- `historical-order-book-reconstruction-from-message-logs`
---
