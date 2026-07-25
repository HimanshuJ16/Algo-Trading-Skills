---
name: binary-protocol-parsing-for-low-latency-feeds
description: >-
  Use when connecting to high-frequency binary market data feeds (NASDAQ ITCH, CME MDP 3.0, FIX/FAST, SBE) to unpack binary structs with sub-microsecond zero-copy parsers, decoding price scaling and orderbook messages without JSON text overhead.
domain: algorithmic-trading
subdomain: real-time-architecture
tags: ["real-time-architecture", "binary-parsing", "itch-protocol", "low-latency", "cme-mdp", "struct-unpack"]
brokers_frameworks: ["Binary Feed Parser", "Python Struct Engine"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when building ultra-low-latency feed handlers connected to institutional exchanges or high-frequency crypto venues emitting binary streams (e.g., NASDAQ ITCH 5.0, CME MDP 3.0, Simple Binary Encoding / SBE). Standard JSON/REST parsing wastes significant CPU cycles converting ASCII numbers to floats. Unpacking binary byte structs directly via native C/struct layouts reduces parsing latency from microseconds to nanoseconds.

## Prerequisites

- Exchange binary specification (struct field offsets, byte order / endianness, message type codes).
- Integer price scaling factor (e.g., ITCH uses $10^4$ for stock prices).

## Workflow

1. **Read Fixed-Width Binary Header**:
   - Unpack packet header (Message Type 1B, Stock Locate 2B, Tracking Number 2B, Timestamp 6B).

2. **Branch by Message Type Code**:
   - `'A'`: Add Order (Order Reference ID, Buy/Sell Indicator, Shares, Stock Symbol, Price).
   - `'E'`: Order Executed (Order Reference ID, Executed Shares, Match ID).
   - `'X'`: Order Cancel (Order Reference ID, Cancelled Shares).

3. **Decode Scaled Integer Prices**:
   - Convert fixed-point integer prices to floating-point:
     $$\text{Price}_{\text{float}} = \frac{\text{Price}_{\text{int}}}{10^4}$$

4. **Emit High-Speed Event Struct**:
   - Dispatch decoded frame directly to orderbook state engine without dictionary allocations.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Endianness Mismatch**: Using little-endian (`<`) unpacking on big-endian (`>`) exchange feeds, producing corrupt prices and negative quantities.
- **Ignoring Price Scale Factors**: Forgetting to divide integer prices by $10^4$ or $10^7$, causing 10,000x price scaling errors.
- **Allocating Dict Objects in Hot Loops**: Instantiating Python dictionaries inside the binary parse loop instead of reusing fixed slots or namedtuples.

## Verification

- Pack a mock NASDAQ ITCH Add Order 'A' binary frame and unpack, verifying exact symbol, shares, and scaled price.
- Benchmark 100,000 binary unpacks vs JSON parses and confirm $>10\times$ execution speedup.
- Run `python scripts/test_binary_parser.py` and confirm 100% pass rate.

## Related Skills

- `orderbook-l2-l3-reconstruction`
- `memory-mapped-ring-buffer-for-ultra-low-latency`
- `high-frequency-time-synchronization-ptp-ntp`
---
