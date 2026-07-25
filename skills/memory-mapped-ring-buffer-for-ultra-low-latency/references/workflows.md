# Deep Workflow Reference — memory-mapped-ring-buffer-for-ultra-low-latency

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Shared mmap Buffer Initialization**:
   - Allocate binary file of size $\text{HeaderSize} + C \times \text{SlotSize}$ and wrap in `mmap.mmap`.

2. **Zero-Copy Lock-Free Push**:
   - Pack tick binary struct into byte offset $\text{HeaderSize} + (H \bmod C) \times \text{SlotSize}$ and increment $H$.

3. **Zero-Copy Lock-Free Pop**:
   - If $T < H$, unpack tick struct from offset $\text{HeaderSize} + (T \bmod C) \times \text{SlotSize}$ and increment $T$.

4. **Resource Cleanup**:
   - Close memory map and unmap file descriptors cleanly upon process termination.

## Production Implementation Reference

- Reference code: `scripts/mmap_ring_buffer.py` (`MemoryMappedRingBufferEngine`, `MMAPTickSlot`).
- Automated unit tests: `scripts/test_mmap_ring_buffer.py`.
