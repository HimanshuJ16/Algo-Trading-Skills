# Deep Workflow Reference — binary-protocol-parsing-for-low-latency-feeds

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Protocol Binary Layout Mapping**:
   - Map field offsets, sizes, and endianness (`>` big-endian / `<` little-endian) per exchange binary spec.

2. **Unpack Fixed Binary Frame**:
   - Use zero-copy `struct.unpack_from(">cHHQQcI8sI", raw_bytes)` to extract header, order IDs, quantities, and integer prices.

3. **Fixed-Point Price Scaling**:
   - Convert fixed-point integer prices to float: $\text{Price} = \text{Price}_{\text{int}} / 10^4$.

4. **Dispatch Sub-Microsecond Event**:
   - Pass decoded C-level/dataclass struct directly to orderbook matching engine without dict instantiation.

## Production Implementation Reference

- Reference code: `scripts/binary_parser.py` (`BinaryFeedParserEngine`, `ITCHAddOrderMessage`).
- Automated unit tests: `scripts/test_binary_parser.py`.
