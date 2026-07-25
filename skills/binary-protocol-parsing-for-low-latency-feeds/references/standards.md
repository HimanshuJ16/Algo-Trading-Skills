# Quantitative Engineering Standards

## Binary Protocol Implementation Standards
- **Endianness Compliance**: Always explicitly define endianness (typically big-endian for network protocols). In Python's `struct`, enforce `>` for big-endian.
- **Zero Allocations**: Once the engine warms up, the parser must allocate **zero** memory on the heap per message. Use object pools (e.g., ring buffers of pre-allocated message structs).
- **Latency Budget**: Message parsing (from byte to normalized struct) must complete in under 500 nanoseconds for Python (using cython/struct.Struct) and under 50 nanoseconds in C++/Rust.
- **Float Avoidance**: Never use floating-point types for incoming financial prices. Strictly use integers scaled by the protocol's denominator (e.g., ITCH 10,000 scale). Convert to float only if absolutely required by downstream alpha models.
- **Branch Prediction**: Avoid `if-else` branches on the hot path. Use lookup tables (arrays of function pointers) mapping `MsgType` to parser routines.
