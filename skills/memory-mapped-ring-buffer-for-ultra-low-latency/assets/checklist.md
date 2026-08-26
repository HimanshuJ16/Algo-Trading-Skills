# Pre-Flight / Sign-off Checklist — memory-mapped-ring-buffer-for-ultra-low-latency

Use this before considering the skill's implementation complete.

## Layout
- [ ] Both format strings carry an explicit endianness prefix, so native
      alignment padding cannot silently change a width.
- [ ] Compiled sizes are asserted against the spec (`SLOT_SIZE == 40`,
      `HEADER_SIZE == 32`), not just round-tripped through the code's own pack.
- [ ] Every slot field is fixed-width; no unpadded variable-length string is
      stored in a slot.
- [ ] `struct.Struct` objects are precompiled once, not rebuilt per tick.

## Concurrency
- [ ] Exactly one producer and one consumer. (If more, stop — this design has no
      compare-and-swap and will corrupt. Use a broker instead.)
- [ ] The producer writes **only** `write_head`; the consumer writes **only**
      `read_tail`. Neither rewrites the whole header.
- [ ] `push` writes the slot payload *before* advancing `write_head`.
- [ ] `pop` reads the slot payload *before* advancing `read_tail`.
- [ ] No `msync`/`flush` on the hot path for visibility reasons. (It is a
      durability call; shared mappings are coherent without it.)
- [ ] The atomicity limitation is understood and recorded: CPython packs a
      big-endian `Q` byte by byte and exposes no memory fence, so cross-process
      torn index reads are possible. Either the deployment accepts this, or a
      lock is held, or the hot path is a native port.
- [ ] The mapping is opened `access=mmap.ACCESS_WRITE`, not left to the platform
      default and never `ACCESS_COPY`.

## Indices
- [ ] `write_head` and `read_tail` are monotonic uint64, never taken modulo
      capacity. Only the slot index is reduced.
- [ ] Full is `head - tail >= capacity`; empty is `tail >= head`.
- [ ] Backlog (`len(ring)`) is exported as a live metric, not only logged.

## Lifecycle
- [ ] The consumer attaches; it does not re-run the creating constructor.
- [ ] `attach()` validates magic, format version, capacity, and file length
      before mapping.
- [ ] Only the creating process unlinks the backing file. A consumer's
      `close()` leaves it intact.
- [ ] `close()` is idempotent and a context manager is available.

## Validation
- [ ] `capacity` is rejected unless it is a positive int. (`capacity = 0` maps
      fine and then drops 100% of pushes behind a log line.)
- [ ] Out-of-range sequence numbers and timestamps raise a typed error before
      packing, not a raw `struct.error` from inside it.
- [ ] A rejected push does not advance `write_head`.
- [ ] A full ring returns `False` and leaves the tail slot untouched.
- [ ] It is documented that NaN/inf round-trip intact and that the **consumer**
      must reject nonsensical quotes.

## Performance
- [ ] The latency budget comes from measurement on the target hardware, with
      the method recorded — not from a quoted figure.
- [ ] No claim of sub-microsecond handoff is made for a CPython implementation.
      (Measured here: ~0.60 µs push, ~0.80 µs pop.)
- [ ] "Zero-copy" is not being read as "zero-allocation": every `pop` still
      allocates in CPython.

## Coverage
- [ ] Tests cover FIFO order, wraparound past capacity by a non-multiple,
      partial-drain wraparound, full and empty boundaries, uint64 and float64
      extremes, capacity validation, payload range validation, attach against
      missing/foreign/truncated/wrong-version files, file-lifetime ownership,
      and close idempotency.
- [ ] The lost-update regression tests demonstrably fail against a whole-header
      write and pass against split index ownership.
- [ ] `python -m unittest discover -s skills/memory-mapped-ring-buffer-for-ultra-low-latency/scripts`
      — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
