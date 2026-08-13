---
name: binary-protocol-parsing-for-low-latency-feeds
description: >-
  Fixed-layout binary struct unpacking for low-latency market data feeds --
  frame validation, message-type dispatch, fixed-point tick handling, and
  offset-based zero-copy buffer walking, worked through NASDAQ ITCH 5.0.
domain: algorithmic-trading
subdomain: real-time-architecture
tags:
- real-time-architecture
- binary-protocol
- struct-unpack
- fixed-point-pricing
- zero-copy
brokers_frameworks:
- Nasdaq TotalView-ITCH 5.0
- CME MDP 3.0 (SBE)
- Python struct
- Python Dataclasses
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

# Binary Protocol Parsing for Low Latency Feeds

## Context & Rationale
Exchanges distribute market data as raw binary packets over UDP multicast. Text
and schema-driven serialization (JSON, XML, Protocol Buffers) costs orders of
magnitude more than a fixed-layout binary decode, so feed handlers read network
bytes directly into application structs using precompiled layouts.

The dominant production risk in this code is not slowness — it is a **silent
misparse**. A frame decoded against the wrong layout does not raise; it returns
a plausible message with a real-looking symbol, size, and price, which then
corrupts order book state and feeds bad inputs to alpha models. Every decode
path in this skill therefore validates before it returns.

## Core Concepts
- **Struct Unpacking**: Converting contiguous bytes into primitives using a
  precompiled `struct.Struct`, with endianness stated explicitly.
- **Offset-Based Buffer Walking**: Passing `(buffer, offset)` into the decoder
  rather than slicing per message. Slicing `bytes` copies; a `memoryview` plus
  an offset does not.
- **Fixed-Point Scaling**: Exchanges transmit prices as scaled integers (ITCH
  scales by 10,000). The integer tick is the authoritative value; a float is a
  derived convenience.
- **Message-Type Dispatch**: The leading type byte selects the layout. The
  decoder re-checks it so a dispatch bug fails loudly instead of silently.
- **Memory Slots**: `__slots__` (or packed structs in C++) reduces per-message
  memory overhead and speeds attribute access.

## Quick Start
See `scripts/binary_parser.py`, which decodes the 36-byte NASDAQ ITCH 5.0
Add Order (No MPID Attribution, type `'A'`) message.

```python
view = memoryview(packet)              # no copy
msg = BinaryFeedParserEngine.unpack_itch_add_order(view, offset=header_len)
book.add(msg.order_ref_id, msg.price_ticks, msg.shares)   # integer ticks
```

## When to Use

Use this skill when writing or reviewing a feed handler that decodes
**fixed-layout binary** market data — NASDAQ ITCH, CME MDP 3.0/SBE, Eurex T7
EMDI, or a venue's proprietary binary protocol — and you need the decode step
itself to be correct and fail-loud. It applies when you are:

- unpacking length-framed binary messages into typed application structs;
- walking multiple concatenated messages inside a single UDP payload;
- deciding how to carry exchange prices through the system (ticks vs floats);
- hardening an existing parser that trusts its input.

**When NOT to use it:**

- **Text or self-describing protocols** — FIX tag=value, JSON/WebSocket feeds.
  Those are parsed by field name, not byte offset; see
  `websocket-reconnect-without-duplicate-subscriptions`.
- **Full ITCH order book reconstruction.** This skill covers the decode layer
  generically. For the ITCH message *set* (`A`/`F`/`E`/`X`/`D`/`P`) and L3 book
  state machine, use `nasdaq-totalview-itch-feed-parsing`.
- **Transport concerns** — A/B multicast arbitration, gap detection, and
  retransmission are upstream of this skill. See
  `exchange-multicast-feed-handling` and
  `sequence-number-gap-detection-for-feeds`.
- **Latency budgeting in pure Python.** CPython is appropriate for research,
  replay, and reference decoding; see Prerequisites for measured numbers before
  assuming it belongs on a production hot path.

## Prerequisites

- The venue's **binary specification document**, at the exact version the feed
  publishes. Field offsets change between protocol versions.
- The venue's **endianness**, read from that spec rather than assumed —
  see the Common Pitfalls entry below.
- The venue's **price scaling divisor** and any per-instrument overrides.
- Python 3.10+ (`@dataclass(slots=True)`).
- Realistic expectations for CPython throughput. Measured on this repo's
  reference implementation (CPython 3.11.15, `timeit`, best of 5 repeats ×
  200k iterations, warm cache, single core):
  - `struct.Struct.unpack_from` alone: **~186 ns/message**
  - full `unpack_itch_add_order` incl. validation + dataclass: **~1.08 µs/message**

  These are indicative single-machine numbers, not a specification. Re-measure
  on your own hardware. Sustained full-depth ITCH rates need C++/Rust/FPGA.

## Workflow

1. **Frame before you parse.** Read the transport's length prefix (MoldUDP64
   for ITCH, the 2-byte message size field for CME MDP 3.0) and confirm the
   full frame is present. Never infer message length from the payload you hope
   is there.
2. **Dispatch on the type byte.** Read the leading message-type byte and select
   the layout from a dispatch table. If the type is unknown, skip exactly
   `declared_length` bytes and continue — do **not** guess a layout, and do not
   abandon the packet, or you will drop every subsequent message in it.
3. **Re-validate the type inside the decoder.** The decoder independently
   asserts the type byte matches its own layout. This is what converts a
   dispatch bug or an off-by-one offset from silent book corruption into an
   exception at the point of failure.
4. **Decode at an offset, do not slice.** Pass the `memoryview` and an offset.
   Advance by the message's declared length to reach the next message.
5. **Keep prices as integer ticks.** Store the raw scaled integer as the
   authoritative value. Convert to float only at the boundary of a component
   that requires one.
6. **Classify decode failures.** A frame that fails validation is a data
   integrity event: count it, log it with the packet sequence number and byte
   offset, and decide explicitly whether to skip the message or drop the
   packet. Do not let a decode exception silently kill the feed thread.
7. **Reconcile.** Periodically verify decoded state against the venue's
   snapshot/recovery channel; see
   `market-data-snapshot-plus-delta-reconciliation`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Assuming "network protocol" means big-endian.** ITCH is big-endian (`>`),
  but **CME MDP 3.0 uses Simple Binary Encoding, which is little-endian**.
  Applying a blanket `>` to an SBE feed corrupts every multi-byte field while
  still producing structurally valid-looking messages. Read the endianness off
  each venue's spec.
- **Decoding a frame without checking its type byte.** Feeding a type `'P'`
  (Non-Cross Trade) frame to an Add Order decoder does not raise — it returns a
  well-formed message with a wrong symbol, size, and price, which is then
  applied to the book.
- **Silently truncating over-long symbols at encode time.** Truncating a
  9-character symbol to 8 mints a frame for a *different instrument* than the
  caller named.
- **Slicing the receive buffer per message.** `buf[i:i+36]` copies on every
  message, defeating the point of the zero-copy path. Use an offset.
- **Treating a float price as the source of truth.** For ITCH's uint32/10,000
  domain the float conversion itself is exact, but float *arithmetic* is not
  associative — summing notionals across fills drifts. Aggregate in ticks.
- **Using native `struct` alignment.** A format string with no endianness
  prefix uses native alignment and inserts padding: the ITCH Add Order layout
  measures 36 bytes with `>` but 44 bytes natively.
- **Assuming a 48-bit timestamp fits a 32-bit int.** ITCH timestamps are 6-byte
  nanoseconds since midnight and must be reassembled explicitly.

## Verification

- Run `python -m unittest discover -s skills/binary-protocol-parsing-for-low-latency-feeds/scripts`.
- Confirm `BinaryFeedParserEngine.ADD_ORDER_FORMAT.size == 36` and that the
  format string begins with `>`.
- Assert wire offsets independently of the format string: MsgType@0,
  StockLocate@1, Tracking@3, Timestamp@5, OrderRef@11, BuySell@19, Shares@20,
  Stock@24, Price@32.
- Feed a frame whose type byte is not `'A'` and confirm `ITCHFrameError` is
  raised rather than a populated message being returned.
- Pack a known price (150.1234) and confirm `price_ticks == 1501234` exactly.
- Round-trip the maximum 48-bit timestamp (2**48 - 1).
- Walk a buffer of concatenated messages by offset and confirm each decodes
  with the expected symbol and tick price.

## Related Skills

- `nasdaq-totalview-itch-feed-parsing` — the full ITCH message set and L3 order
  book reconstruction built on top of this decode layer.
- `exchange-multicast-feed-handling` — A/B multicast arbitration and gap fill,
  upstream of parsing.
- `sequence-number-gap-detection-for-feeds` — detecting dropped packets before
  decoded messages reach the book.
- `market-data-snapshot-plus-delta-reconciliation` — recovering correct state
  after a decode failure or gap.
- `memory-mapped-ring-buffer-for-ultra-low-latency` — the zero-copy buffer this
  parser reads from.
- `order-book-depth-processing-l2-l3` — consuming decoded messages into book
  state.
