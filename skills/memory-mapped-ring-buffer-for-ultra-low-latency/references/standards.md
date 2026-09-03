# Real-Time Architecture Standards — memory-mapped-ring-buffer-for-ultra-low-latency

## Reference Layout

| Parameter | Value | Description |
|---|---|---|
| Header format | `>4sIQQQ` (32 bytes) | `magic` (4B, `MMRB`), `format_version` (4B), `capacity` (8B), `write_head` (8B), `read_tail` (8B) |
| `write_head` offset | 16 | Written by the producer only |
| `read_tail` offset | 24 | Written by the consumer only |
| Slot format | `>QQddd` (40 bytes) | `sequence_id`, `timestamp_ns` (uint64), `bid`, `ask`, `volume` (float64) |
| Total file size | `32 + capacity × 40` | Validated on attach against the header's declared capacity |
| Index arithmetic | Monotonic uint64 | Only the slot index is taken `mod capacity` |
| Overflow behaviour | Drop and warn | `head - tail >= capacity` → `push` returns `False` |

Both format strings carry an explicit `>` prefix. Without it, `struct` uses
native alignment and inserts padding, which changes the on-disk widths and
therefore every computed offset.

## Concurrency Model

- **Single producer, single consumer only.** Each index has exactly one writer.
  This is the arrangement Lamport proved correct for a bounded buffer without
  locks — see L. Lamport, *Proving the Correctness of Multiprocess Programs*,
  IEEE Transactions on Software Engineering SE-3(2), 1977
  (<https://lamport.azurewebsites.net/pubs/proving.pdf>). The proof holds under
  sequential consistency; it does not hold if it is relaxed, and it does not
  extend to multiple writers of the same index, for which you need a
  compare-and-swap claim sequence (the LMAX Disruptor approach).

- **Never rewrite the whole header from one side.** A producer that rewrites
  `read_tail` from its own snapshot reverts a tail the consumer already
  committed, and vice versa. The failure is silent: a published tick vanishes,
  the backlog under-reports, and the slot is reused.

- **CPython supplies neither atomicity nor ordering.** `struct.pack_into` for a
  big-endian `Q` goes through `_PyLong_AsByteArray`, filling the destination
  byte by byte rather than issuing one 8-byte store (CPython
  `Modules/_struct.c`, `bp_ulonglong`). A peer process can therefore observe a
  torn index, and Python exposes no acquire/release primitive to order the slot
  write against the head write. The GIL serializes bytecode within *one*
  interpreter and provides no cross-process guarantee. Treat the Python code in
  `scripts/` as a reference design and a research/replay tool; a production
  cross-process hot path wants a C++/Rust implementation with explicit
  `memory_order_release` / `memory_order_acquire`, or an OS-level lock and the
  latency that comes with it.

- **Visibility does not require `msync` or `flush`.** POSIX `mmap` specifies
  that "if MAP_SHARED is specified, write references shall change the
  underlying object"
  (<https://pubs.opengroup.org/onlinepubs/9699919799/functions/mmap.html>), and
  Linux `mmap(2)` states that with `MAP_SHARED` "updates to the mapping are
  visible to other processes mapping the same region"
  (<https://man7.org/linux/man-pages/man2/mmap.2.html>). `msync` is specified as
  writing "all modified data to permanent storage locations"
  (<https://pubs.opengroup.org/onlinepubs/9699919799/functions/msync.html>) —
  a durability control, not a visibility one. On this path it is a blocking
  syscall that fixes nothing.

- **Map write-through explicitly.** Pass `access=mmap.ACCESS_WRITE`.
  `ACCESS_COPY` maps copy-on-write, and every write becomes invisible to the
  peer with no error raised. Python's `mmap` defaults to `MAP_SHARED` on Unix
  and a write-through mapping on Windows
  (<https://docs.python.org/3/library/mmap.html>), but stating it is what keeps
  the intent readable.

## Latency

Set a budget from measurement on your own hardware, not from a quoted figure.
For calibration, this repo's reference implementation measured on CPython 3.11.4
(`timeit`, best of 5 repeats × 200,000 iterations, warm cache, single core,
Intel Core i7-12xxx class laptop, Windows 11):

| Operation | Measured |
|---|---|
| `push` | ~0.60 µs/tick |
| `push` with the uint64 range checks removed | ~0.52 µs/tick |
| `pop` (includes `MMAPTickSlot` construction) | ~0.80 µs/tick |
| `struct.pack_into` alone, into a `bytearray` | ~0.10 µs |

The ~0.07 µs the range checks cost is deliberate: it converts a raw
`struct.error` raised from inside the pack into a typed, located failure, and
guarantees no partial slot is written beneath a live head. On a path already
costing 600 ns per push in CPython that is the right trade; in a C++/Rust port
the same validation is effectively free.

**CPython does not achieve sub-microsecond push/pop here.** Any claim of
sub-microsecond tick handoff should be attributed to a systems-language
implementation and verified against a hardware-timestamped measurement rather
than assumed.

## Allocation Discipline

"Zero-copy" here means no intermediate `bytes` slice is materialised between the
mapped page and the `struct` call. It does **not** mean zero allocation: every
`pop` allocates a tuple from `unpack_from`, five scalar objects, and the
`MMAPTickSlot`. `@dataclass(slots=True)` reduces the per-object footprint and
speeds attribute access but does not remove the allocation. The GC-pause-free
property this design is usually sold on is only fully reachable in a language
with manual layout control and pre-allocated pools.

## Sizing and Placement

- Size `capacity` from the burst the consumer must absorb during a volatility
  spike, not from a round number — see `tick-buffering-burst-handling`.
- Instrument the backlog (`len(ring)`) as a live metric. It is the earliest
  signal that the consumer is falling behind, well before drops begin.
- In a native port, pad `write_head` and `read_tail` onto separate 64-byte cache
  lines. Sharing a line makes the producer's and consumer's stores contend for
  the same line (false sharing). This is deliberately *not* done in the Python
  reference: at ~600 ns per operation the interpreter overhead is three orders
  of magnitude larger than the coherence traffic, so the padding would add
  layout complexity for an unmeasurable gain.
