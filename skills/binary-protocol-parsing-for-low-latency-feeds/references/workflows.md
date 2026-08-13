# Binary Feed Parsing Workflows

## Ingestion Workflow

1. **Network Interrupt**: NIC receives the UDP multicast packet.
2. **Kernel Bypass**: Where the latency budget justifies it, use a kernel-bypass
   stack (Solarflare OpenOnload, DPDK, or an RDMA/verbs path) to deliver the
   packet into a user-space ring buffer without a copy through the kernel.
3. **Sequencing & Gap Detection**: Read the packet header and validate the
   sequence number. On a gap, invoke the venue's recovery path (A/B line
   arbitration first, then the retransmission/snapshot channel) — see
   `sequence-number-gap-detection-for-feeds`.
4. **Framing**: Read the transport's length prefix for the next message
   (MoldUDP64 message-length for ITCH; the 2-byte message size field for CME
   MDP 3.0) and confirm the whole frame is present in the buffer. Never infer
   a message's length from its assumed type.
5. **Message Dispatch**: Read the leading `MsgType` byte and select the decoder
   from a dispatch table. On an unknown type, advance by exactly the declared
   length and continue with the next message — guessing a layout corrupts
   state, and abandoning the packet drops every message behind it.
6. **Decode at Offset**: Pass the `memoryview` plus the message's offset to the
   decoder. Do not slice the buffer per message: slicing `bytes` copies, which
   is precisely what the zero-copy path exists to avoid. The decoder
   re-validates the type byte so a dispatch bug fails loudly here rather than
   silently downstream.
7. **Failure Classification**: A frame that fails validation is a data
   integrity event, not a routine exception. Increment a counter, log the
   packet sequence number and byte offset, and apply an explicit policy —
   skip the message, or drop and recover the packet. Never let the exception
   terminate the feed thread silently.
8. **Order Book Update**: Apply the decoded message to the normalized limit
   order book, keying prices on integer ticks rather than floats.
9. **Strategy Trigger**: Book updates notify strategies to evaluate signals.
10. **Reconciliation**: Periodically verify book state against the venue's
    snapshot channel — see `market-data-snapshot-plus-delta-reconciliation`.

## Decoder Hardening Workflow

Apply when reviewing or retrofitting an existing parser:

1. Confirm the endianness against the venue spec — not against another venue's
   parser in the same codebase.
2. Assert the compiled struct size against the specification's message length.
3. Assert wire offsets independently of the format string. A test that packs
   and unpacks through the same format string validates self-consistency, not
   conformance to the specification.
4. Add a negative test that feeds a *different, valid* message type into the
   decoder and asserts it raises. This is the single highest-value test in a
   binary parser.
5. Add boundary tests for every field at its width limit (uint48 timestamp
   maximum, uint32 price maximum, uint16 locate maximum).
6. Verify that encode-side helpers reject rather than truncate.

## Performance Tuning Workflow

1. **Measure First**: Establish a per-message baseline with `timeit` (Python)
   or a hardware-timestamped capture before optimizing. Record the CPU, Python
   build, and iteration count alongside the number — a latency figure without
   its method is not reusable.
2. **Profiling**: Use `perf` or eBPF to profile cache misses and branch
   mispredictions on the hot path.
3. **Layout Adjustment**: In C++/Rust, order struct fields so frequently
   accessed fields share a cache line.
4. **Interpreter Escape**: In Python, the validated decode is dominated by
   interpreter overhead, not by `struct` itself (measured on this repo's
   reference implementation: ~186 ns for `unpack_from` alone versus ~1.08 µs
   for the full validated decode on CPython 3.11.15, best of 5 repeats ×
   200k iterations). Moving the hot path to Cython, Rust, or C++ is the
   meaningful optimization; micro-tuning Python around a ~186 ns primitive
   is not.
5. **Build Flags**: In systems languages, compile with `-O3 -march=native` and
   confirm the gain with a re-measurement rather than assuming it.
