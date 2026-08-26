---
name: memory-mapped-ring-buffer-for-ultra-low-latency
description: >-
  Single-producer/single-consumer ring buffer over a shared memory-mapped file,
  for handing ticks between processes without a language-level queue -- fixed
  binary slots, split head/tail index ownership, explicit drop-on-full, and the
  atomicity and memory-ordering limits that decide whether it is safe to use.
domain: algorithmic-trading
subdomain: real-time-architecture
tags:
- real-time-architecture
- memory-mapped
- mmap
- ring-buffer
- shared-memory
- low-latency
- ipc
brokers_frameworks:
- Python mmap
- Python struct
- POSIX shared file mappings
- LMAX Disruptor (design reference)
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

# Memory-Mapped Ring Buffer for Ultra-Low Latency

## Context & Rationale

Standard Python queues (`queue.Queue`, `multiprocessing.Queue`) sit between the
feed handler and the strategy and charge for it: lock acquisition, per-item
object construction, and — for `multiprocessing.Queue` — a pickle round-trip
plus a pipe write on every message. A ring buffer over a shared memory-mapped
file removes all three: both processes map the same pages, and a tick is a
fixed-width byte range at a computed offset.

What that buys is real but narrower than the usual pitch. The dominant risk
here is not slowness — it is a **silently lost or torn tick**. A ring whose two
indices are not owned by exactly one party each will quietly revert a peer's
committed index; a full ring that does not detect fullness will overwrite unread
slots. Neither raises. Both surface later as a strategy acting on a book that
never existed.

## When to Use

Use this skill when designing or reviewing the transport between a market data
process and a consuming process where the queue itself is on the critical path
and you need:

- fixed-width binary slots at computed offsets rather than serialized objects;
- one producer and one consumer, with an explicit policy for what happens when
  the consumer falls behind;
- shared state that survives the consumer restarting without the producer
  stopping.

**When NOT to use it:**

- **More than one producer or more than one consumer.** This design has no
  compare-and-swap to arbitrate concurrent writers of the same index. Two
  producers corrupt the ring. Use a broker or a locked structure — see
  `redis-streams-multi-consumer-tick-fanout` and
  `kafka-based-tick-distribution-at-scale`.
- **Within a single process.** If both sides are threads in one interpreter, a
  bounded `queue.Queue` is simpler, already correct, and the mmap indirection
  buys nothing. See `producer-consumer-tick-pipeline`.
- **Variable-length or unbounded payloads.** Offset arithmetic requires a fixed
  slot width. News text, order book snapshots of varying depth, and JSON do not
  belong here.
- **Durability.** This is a transport, not a log. A crash loses whatever was in
  flight, and the backing file's contents after a crash are not a recovery
  record. For replay and retention see
  `historical-tick-data-storage-and-compaction`.
- **As a substitute for backpressure policy.** The ring tells you it is full;
  it does not tell you what to do about it. See
  `backpressure-drop-degrade-policy` and `tick-buffering-burst-handling`.

## Prerequisites

- A **fixed slot layout** decided up front. The reference implementation uses
  `>QQddd` — 40 bytes: sequence (uint64), timestamp (uint64 ns), bid, ask,
  volume (float64). Endianness is stated explicitly; a format string with no
  prefix uses native alignment and silently changes the slot width.
- **Capacity sized from the burst you must absorb**, not from a round number.
  Total file size is `HeaderSize + capacity × SlotSize`.
- **Python 3.10+** (`@dataclass(slots=True)`).
- **Realistic latency expectations.** Measured on this repo's reference
  implementation (CPython 3.11.4, `timeit`, best of 5 repeats × 200k iterations,
  warm cache, single core, Intel Core i7-12xxx class laptop):
  - `push`: **~0.60 µs/tick** (~0.52 µs with the uint64 range checks removed)
  - `pop`: **~0.80 µs/tick** (includes constructing the `MMAPTickSlot`)

  These are indicative single-machine numbers, not a specification — re-measure
  on your own hardware. **CPython does not reach sub-microsecond round trips
  here.** Genuinely sub-microsecond tick-to-tick handoff needs C++/Rust, where
  this same layout does pay off.

## Workflow

1. **Create in the producer, attach in the consumer.** The creating call
   truncates and zeroes the backing file. If the consumer runs the same
   constructor it wipes the live buffer — on Linux silently, on Windows as an
   `OSError`. Use a distinct `attach()` path that maps without truncating and
   validates a magic number, format version, declared capacity, and file length
   before mapping. Attaching to a stale file from a previous run with a
   different capacity otherwise indexes off the end of the mapping.
2. **Keep the indices monotonic, never modular.** `write_head` and `read_tail`
   are free-running uint64 counters; only the *slot index* is taken
   `mod capacity`. Wrapping the counters themselves breaks both tests that
   depend on them: `head - tail >= capacity` (full) goes negative and
   `tail >= head` (empty) reports permanently empty, so the producer overwrites
   unread slots forever. A uint64 counter at 10M ticks/s lasts ~58,000 years;
   it does not need wrap handling.
3. **Give each index exactly one writer.** The producer writes only
   `write_head`; the consumer writes only `read_tail`; each at its own fixed
   byte offset. Rewriting the whole header from either side means the writer
   also stamps the *other* index from its own stale snapshot, silently
   reverting a tick the peer already committed. This is the single most
   damaging defect in this design and it never raises.
4. **Push: check fullness, write the payload, then advance the head.** If
   `head - tail >= capacity`, drop the tick and count the drop — do not block
   and do not overwrite. Advancing the head before the payload is written
   publishes a slot the consumer may read half-formed.
5. **Pop: read the payload, then advance the tail.** Advancing first releases
   the slot back to the producer while it is still being read, which yields a
   torn tick rather than a dropped one.
6. **Validate at the boundary, not in the middle.** Reject out-of-range
   sequence numbers and timestamps before packing, so a caller sees a typed
   error instead of a raw `struct.error` from inside the pack, and no partial
   slot is written under a live head.
7. **Own the file's lifetime explicitly.** Only the process that created the
   backing file may unlink it. A consumer's `close()` deleting the file is how
   an attached reader destroys the producer's buffer.
8. **Do not call `msync`/`flush` for visibility.** See Common Pitfalls — it is
   a durability call, and on this path it is a blocking syscall that buys
   nothing.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Rewriting the whole header on every push and pop.** The producer's push
  stamps `read_tail` from its own stale read, and the consumer's pop stamps
  `write_head` from its own — so whichever commits second reverts the other's
  index. A tick that was published and counted disappears, the ring reports a
  shorter backlog than it holds, and the slot is reused. Write only the index
  you own.
- **Believing `msync`/`flush` is what makes a write visible to the peer.** It
  is not. A shared file mapping is coherent by definition: POSIX specifies that
  with `MAP_SHARED`, "write references shall change the underlying object," and
  Linux `mmap(2)` states updates "are visible to other processes mapping the
  same region." `msync` controls *durability to disk*. Putting it on the hot
  path adds a blocking syscall to the exact code you built this buffer to make
  fast, and fixes nothing.
- **Assuming the GIL, or x86, makes the index updates atomic across
  processes.** The GIL serializes bytecode inside one interpreter and says
  nothing across processes. CPython's `struct` packs a big-endian `Q` through a
  byte-level fill (`_PyLong_AsByteArray`), not a single 8-byte store, so a peer
  can in principle observe a torn index. Lamport's bounded-buffer proof requires
  atomic, sequentially consistent index reads and writes; CPython supplies
  neither. Port to C++/Rust with acquire/release semantics for a real hot path,
  or accept a lock.
- **Running the creating constructor in the consumer.** It zeroes the live
  buffer. On Linux this succeeds and silently discards every unread tick; on
  Windows it fails with `OSError: [Errno 22]` because the file is already
  mapped. Neither is a good way to find out.
- **Mapping copy-on-write by accident.** `mmap.ACCESS_COPY` produces a mapping
  where every write is invisible to the peer. Nothing raises — you just get a
  consumer that never sees a tick. Pass `access=mmap.ACCESS_WRITE` explicitly
  rather than relying on the platform default.
- **`capacity=0`.** It maps cleanly and then reports full on every push
  (`0 - 0 >= 0`), silently dropping 100% of ticks behind a log line. Validate
  capacity as a positive integer at construction.
- **Deleting the backing file in `close()` regardless of who created it.** The
  consumer detaching then destroys the producer's buffer.
- **Reading "zero-copy" as "zero-allocation" in CPython.** No intermediate
  `bytes` slice is created, but every `pop` allocates a tuple plus five scalar
  objects plus the slot object. The GC pressure this buffer is sold as
  eliminating is reduced, not removed. That claim only becomes true in a
  systems language.
- **Variable-length fields in a fixed slot.** Storing an unpadded symbol string
  breaks offset arithmetic for every subsequent slot. Pad to a fixed width and
  assert the width in a test.
- **Treating the ring as a validator.** NaN and infinity round-trip intact by
  design. The consumer must reject nonsensical quotes; the transport will not.

## Verification

- Run `python -m unittest discover -s skills/memory-mapped-ring-buffer-for-ultra-low-latency/scripts`.
- Assert `SLOT_SIZE == 40` and `HEADER_SIZE == 32`, and that both format
  strings begin with `>`, so native alignment padding cannot silently resize a
  slot.
- Push more than `capacity` ticks while draining, using a count that is *not* a
  multiple of capacity, and confirm strict FIFO across the wrap.
- Confirm a full ring returns `False` and that the rejected tick did not
  overwrite the slot at the tail.
- Interleave a peer's operation between a caller's index read and its commit
  (attach a second handle and inject at that point) and confirm neither index
  is reverted. Both regression tests in `scripts/` fail against a whole-header
  write and pass against split ownership.
- Confirm `attach()` rejects a missing file, a foreign file, a truncated file,
  and an unknown format version.
- Confirm a caller-supplied backing file survives `close()` and that a
  generated temp file does not.
- Round-trip uint64 boundary values (`2**64 - 1`) and float64 extremes
  (`1e308`, `5e-324`) unchanged.

## Related Skills

- `binary-protocol-parsing-for-low-latency-feeds` — decoding the exchange frames
  that get written into these slots.
- `producer-consumer-tick-pipeline` — the in-process queue this replaces only
  when the two sides are separate processes.
- `backpressure-drop-degrade-policy` — deciding what to do when the ring reports
  full; this skill only detects it.
- `tick-buffering-burst-handling` — sizing capacity against real burst
  behaviour rather than a round number.
- `feed-handler-cpu-pinning-and-numa-awareness` — placing producer and consumer
  so the shared pages stay local.
- `redis-streams-multi-consumer-tick-fanout` — the fan-out case this design
  explicitly cannot serve.
