# Binary Feed Parsing Workflows

## Ingestion Workflow
1. **Network Interrupt**: NIC receives UDP multicast packet.
2. **Kernel Bypass**: Use Solarflare/Mellanox kernel bypass (e.g., OpenOnload, DPDK) to read packet directly into user-space ring buffer.
3. **Sequencing & Gap Detection**: Read the packet header. Validate sequence number to ensure no packet drops. If dropped, invoke TCP recovery workflow.
4. **Message Dispatch**: Iterate through messages within the payload. Use the leading `MsgType` byte to switch/dispatch to specific unpackers (e.g., `unpack_itch_add_order`).
5. **Zero-Copy Parse**: Pass a `memoryview` or pointer of the message offset directly to the unpacker.
6. **Order Book Update**: The resulting `ITCHAddOrderMessage` triggers an update to the normalized Limit Order Book (LOB).
7. **Strategy Trigger**: LOB updates notify strategies to evaluate alpha signals.

## Performance Tuning Workflow
1. **Profiling**: Use `perf` or eBPF to profile cache misses and branch mispredictions in the hot path.
2. **Layout Adjustment**: Re-order struct fields in C++/Rust to ensure high-frequency accessed fields sit on the same cache line.
3. **JIT/AOT Optimization**: In Python, use PyPy or Cython; in systems languages, use `-O3 -march=native`.
