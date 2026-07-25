---
name: binary-protocol-parsing-for-low-latency-feeds
description: "Zero-copy binary struct unpacker for NASDAQ ITCH / CME MDP style binary market data feeds."
---

# Binary Protocol Parsing for Low Latency Feeds

## Context & Rationale
In quantitative trading, market data from exchanges like NASDAQ (ITCH) and CME (MDP 3.0) is distributed as raw binary packets via UDP multicast. Traditional serialization (JSON, XML, or even Protocol Buffers) introduces unacceptable latency overhead (milliseconds vs microseconds/nanoseconds). 
High-frequency and low-latency trading systems require custom binary parsers that read network bytes directly into application structs with zero copying, utilizing tight memory layouts and CPU cache-line optimizations.

## Core Concepts
- **Struct Unpacking**: Converting contiguous bytes (often big-endian/network byte order) into primitive data types.
- **Zero-Copy Architecture**: Passing `memoryview` or raw buffer pointers directly to the parser rather than slicing/copying bytes in memory.
- **Fixed-Point Scaling**: Floating point operations are slow and error-prone for precision. Exchanges scale prices to integers (e.g., NASDAQ multiplies by 10,000) for transmission.
- **Precompiled Layouts**: Use precompiled `struct.Struct` or C-extensions to avoid format string parsing overhead at runtime.
- **Memory Slots**: Using `__slots__` in Python (or packed structs in C++) reduces memory overhead and improves attribute access time.

## Quick Start
See the implementation in `scripts/binary_parser.py`. It showcases a highly optimized Python implementation for unpacking the 36-byte NASDAQ ITCH 5.0 Add Order message.

## Usage Guidelines
1. Never copy byte buffers during ingestion (use pointers or `memoryview`).
2. Pre-allocate message structs if possible (object pooling) to avoid garbage collection pauses.
3. Validate frame lengths immediately upon network read before passing to the parser.
