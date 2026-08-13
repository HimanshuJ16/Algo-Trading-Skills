# Quantitative Engineering Standards

## Binary Protocol Implementation Standards

- **Endianness Is Per-Venue, Not Universal**: Always define endianness
  explicitly, taken from the venue's specification. It is **not** always
  big-endian:
  - **Nasdaq TotalView-ITCH 5.0** — big-endian; use `>` in Python `struct`.
  - **CME MDP 3.0** — Simple Binary Encoding, **little-endian**; use `<`.
    ([CME Group, MDP 3.0 - Simple Binary Encoding](https://www.cmegroup.com/confluence/display/EPICSANDBOX/MDP+3.0+-+Simple+Binary+Encoding);
    [B2BITS, White Paper: CME MDP 3.0](https://www.b2bits.com/product_support/e-library/white-paper-cme-mdp-30))

  Never carry an endianness assumption from one venue to another. Also avoid
  format strings with no endianness prefix: those use native alignment and
  insert padding (the ITCH Add Order layout is 36 bytes with `>`, 44 native).

- **Validate the Message Type Inside the Decoder**: Dispatching on the leading
  type byte is not sufficient on its own. A decoder must re-assert that the
  type byte matches the layout it implements, so that a dispatch bug or a
  misaligned offset raises instead of returning a plausible-looking message
  built from another message's bytes.

- **Fixed-Point Prices**: Carry the protocol's scaled integer (ITCH: divisor
  10,000) as the authoritative price. Convert to floating point only at the
  boundary of a component that requires it. For ITCH's uint32/10,000 domain the
  conversion itself is exact in IEEE-754 double, but float summation is not
  associative — aggregate notionals in integer ticks.

- **Allocation Discipline**: In systems languages, target zero heap allocation
  per message on the warmed-up hot path using pre-allocated object pools and
  ring buffers. **This target is not reachable in CPython**: each decoded
  message allocates `bytes` objects for character fields, `str` objects after
  decoding, and the message object itself. Treat the Python implementation in
  `scripts/` as a reference decoder and a research/replay tool, not as a
  zero-allocation hot path.

- **Latency**: Set a budget from measurement on your own hardware, not from a
  quoted figure. For calibration, this repo's reference implementation measured
  on CPython 3.11.15 (`timeit`, best of 5 repeats × 200k iterations, warm
  cache, single core): `struct.Struct.unpack_from` alone ~186 ns/message; the
  full validated decode including dataclass construction ~1.08 µs/message.
  Quote the measurement method alongside any such number. These are indicative
  single-machine numbers and will vary with CPU, build, and Python version.
  Sub-microsecond per-message decoding requires C++/Rust; sub-100 ns generally
  implies FPGA or hand-tuned SIMD, and should be verified against a
  hardware-timestamped measurement rather than assumed.

- **Branch Prediction**: On systems-language hot paths, prefer a lookup table
  (array of function pointers indexed by `MsgType`) over long `if/else` chains.
  In CPython this is a dictionary dispatch and the benefit is interpreter
  overhead reduction, not branch prediction.

## Specification Sources

- Nasdaq TotalView-ITCH 5.0 specification — the normative source for message
  layouts, field offsets, and the 10,000 price divisor. Published by Nasdaq at
  `nasdaqtrader.com` (Technical Support → Specifications → Data Products).
  Always decode against the exact protocol version the feed publishes; field
  offsets change between versions.
- CME MDP 3.0 SBE templates — CME publishes machine-readable SBE XML schemas;
  generate decoders from the schema rather than hand-writing offsets.

> Verification note: the ITCH Add Order layout used in `scripts/binary_parser.py`
> (36 bytes; MsgType@0, StockLocate@1, Tracking@3, Timestamp@5 as 6-byte
> nanoseconds-since-midnight, OrderRef@11, BuySell@19, Shares@20, Stock@24
> left-justified space-padded, Price@32 scaled by 10,000) is asserted directly
> by the skill's test suite. It was corroborated against independent ITCH 5.0
> parser implementations and Nasdaq's published field conventions; the Nasdaq
> PDF itself was not machine-readable from this environment, so treat the
> vendor specification as the tie-breaker if the two ever disagree.
