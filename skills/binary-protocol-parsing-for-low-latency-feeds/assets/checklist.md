# Pre-Flight Checklist for Binary Parsers

- [ ] Network byte order (`>`) is strictly enforced on all multibyte primitive unpacks.
- [ ] 6-byte timestamps (uint48) are correctly handled (e.g., using `int.from_bytes` or bitwise shifts).
- [ ] Prices are parsed as integers and scaled correctly (e.g., `/ 10000.0`).
- [ ] `struct.Struct` is precompiled at class initialization to prevent runtime compilation overhead.
- [ ] Struct payloads are passed via `memoryview` or pointers to prevent copying data arrays.
- [ ] Unit tests cover 100% of parser paths, including short/corrupted payloads.
- [ ] Profiling shows zero dynamic heap allocations per parsed message on the hot path.
- [ ] Alpha/Numeric encodings (e.g., 'B' vs 'S' for Buy/Sell) are correctly converted to ASCII and stripped of null padding.
- [ ] LOB bounds checking is handled gracefully (e.g., order reference IDs are within valid tracked limits).
