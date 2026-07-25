# Real-Time Architecture Standards — binary-protocol-parsing-for-low-latency-feeds

| Parameter | Specification | Description |
|---|---|---|
| ITCH Add Order Frame Size | 38 bytes | Compact packed binary layout |
| Endianness | Big-Endian (`>`) | Standard network byte order |
| Price Scale Factor | 10,000 ($10^4$) | 4-decimal integer price representation |
| Parse Overhead | $< 0.1 \mu\text{s}$ | Sub-microsecond C-level struct unpacking |

## Category

`real-time-architecture` — see top-level `mappings/` directory.
