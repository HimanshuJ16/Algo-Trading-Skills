# Pre-Flight Checklist for Binary Parsers

## Layout & Encoding
- [ ] Endianness is taken from **this venue's** specification and stated
      explicitly in the format string (ITCH: `>`; CME MDP 3.0 SBE: `<`).
      No endianness assumption has been carried over from another venue.
- [ ] The format string carries an explicit endianness prefix, so native
      alignment padding cannot silently change the struct size.
- [ ] The compiled struct size is asserted against the specification's stated
      message length (ITCH Add Order: 36 bytes).
- [ ] Field offsets are asserted independently of the format string, not just
      round-tripped through the code's own pack function.
- [ ] The protocol version being decoded matches the version the feed publishes.
- [ ] 6-byte timestamps (uint48) are reassembled explicitly (`int.from_bytes`
      or bitwise shifts), never truncated into a 32-bit field.

## Validation & Failure Behavior
- [ ] The decoder re-validates the message-type byte against its own layout and
      raises rather than returning a message built from another type's bytes.
- [ ] Enumerated character fields (e.g. Buy/Sell `'B'`/`'S'`) are validated;
      an unexpected value is treated as corruption or misalignment.
- [ ] Frame length is checked against the declared/expected length **before**
      unpacking, accounting for the read offset within the packet.
- [ ] Decode failures raise a single documented exception type carrying the
      byte offset, so a bad frame can be located within its packet.
- [ ] Unknown message types skip exactly `declared_length` bytes and continue,
      rather than guessing a layout or abandoning the rest of the packet.
- [ ] Decode failures are counted and logged with packet sequence number and
      offset, and cannot silently terminate the feed thread.
- [ ] Encode-side helpers reject out-of-range values and over-long symbols
      instead of truncating them into a different instrument.

## Numerics
- [ ] Prices are carried as the protocol's scaled integer ticks; float is
      derived at the consumer boundary only.
- [ ] Notional and size aggregation is done in integer ticks, not floats.

## Performance
- [ ] `struct.Struct` is precompiled once, not rebuilt per message.
- [ ] Messages are read at an offset into a `memoryview`; the receive buffer is
      not sliced per message (slicing `bytes` copies).
- [ ] Latency budget is set from measurement on the target hardware, not from a
      quoted figure, and the measurement method is recorded.
- [ ] For systems-language implementations: profiling confirms zero dynamic
      heap allocations per message on the warmed hot path. (Not achievable in
      CPython — do not sign this off for a Python decoder.)

## Coverage
- [ ] Tests cover: valid round-trip, wrong message type, invalid enum byte,
      short/truncated frame, non-ASCII bytes in character fields, offset-based
      walking of concatenated messages, and field boundary values (uint48 max).
- [ ] Regression tests demonstrably fail against the pre-fix behavior.
- [ ] Downstream book-state bounds (e.g. order reference IDs within tracked
      limits) are checked by the consumer, not assumed by the parser.
