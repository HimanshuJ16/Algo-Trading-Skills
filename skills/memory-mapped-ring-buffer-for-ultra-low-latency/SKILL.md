---
name: memory-mapped-ring-buffer-for-ultra-low-latency
description: >-
  Use when operating sub-microsecond market data pipelines to execute zero-copy memory-mapped file (mmap) ring buffers, eliminating heap allocations, garbage collection pauses, and language-level queue locks.
domain: algorithmic-trading
subdomain: real-time-architecture
tags: ["real-time-architecture", "memory-mapped", "mmap", "ring-buffer", "zero-copy", "low-latency", "ipc"]
brokers_frameworks: ["mmap Ring Buffer", "Python mmap Engine"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when building ultra-low-latency tick ingestion or inter-process communication (IPC) components. Standard Python object queues (`queue.Queue`, `multiprocessing.Queue`) introduce lock contention, object instantiation overhead, and non-deterministic Garbage Collection (GC) pauses. A memory-mapped (`mmap`) ring buffer uses shared memory files with fixed-size binary slots, enabling $O(1)$ zero-copy reads and writes with sub-microsecond latency.

## Prerequisites

- Fixed binary slot size specification (e.g. 40 bytes per tick slot).
- Pre-allocated backing file path and buffer capacity $C$ (e.g. 10,000 slots).

## Workflow

1. **Allocate Memory-Mapped Shared Buffer**:
   - Create backing file of size $S = \text{HeaderSize} + C \times \text{SlotSize}$ and wrap in `mmap.mmap`.

2. **Initialize Fixed Header**:
   - Store header metadata: `capacity` ($C$), `write_head` ($H$), `read_tail` ($T$).

3. **Zero-Copy Binary Write (`push_tick`)**:
   - Calculate byte offset $O_{\text{write}} = \text{HeaderSize} + (H \bmod C) \times \text{SlotSize}$.
   - Pack fixed 40-byte binary tick directly into `mmap` slice and advance $H = (H + 1) \bmod 2^{32}$.

4. **Zero-Copy Binary Read (`pop_tick`)**:
   - If $T \ne H$, read binary slice at offset $O_{\text{read}} = \text{HeaderSize} + (T \bmod C) \times \text{SlotSize}$, unpack struct, and advance $T$.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Buffer Overwrite Without Tail Advance**: Writing faster than reader tail can consume without overflow detection, overwriting unread tick slots.
- **Variable-Length Binary Slots**: Attempting to store variable-length string fields without fixed-width padding, breaking offset arithmetic.
- **Unflushed Memory Pages**: Relying on OS asynchronous page flush when explicit `msync` / file flush is required for multi-process safety.

## Verification

- Allocate mmap ring buffer with 1,000 slots, write 100 ticks, and verify exact FIFO read order and zero-copy byte offsets.
- Verify buffer full / buffer empty boundary conditions.
- Run `python scripts/test_mmap_ring_buffer.py` and confirm 100% pass rate.

## Related Skills

- `binary-protocol-parsing-for-low-latency-feeds`
- `producer-consumer-tick-pipeline`
- `feed-handler-cpu-pinning-and-numa-awareness`
---
