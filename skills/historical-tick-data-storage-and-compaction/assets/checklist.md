# Pre-Flight / Sign-off Checklist — historical-tick-data-storage-and-compaction

## Classification (do this first)
- [ ] The archive has been classified as vendor market data **or** as records the firm is required to preserve, and the classification is recorded.
- [ ] If it holds preserved records, compaction runs inside a WORM or audit-trailed system — a job that rewrites and deletes source files is not a free optimization there (see `references/standards.md`).

## Input data
- [ ] Ticks are sorted by **non-decreasing** `timestamp_nanos`; the sort happened upstream and is auditable.
- [ ] Equal timestamps are accepted (same-nanosecond trades are legal), strictly decreasing ones are rejected.
- [ ] `timestamp_nanos` is an `int`, not a float — epoch nanoseconds exceed the exactly-representable range of a double.
- [ ] Non-finite prices and negative quantities are rejected before encoding.
- [ ] **Negative prices are accepted** — the April 2020 WTI expiry must survive the archive.
- [ ] `side` is validated against `{BUY, SELL, UNKNOWN}` and never defaulted; an unparsed value raises rather than being recorded as SELL.

## Encoding
- [ ] Nanosecond timestamps and integer-scaled prices are delta-encoded, first element absolute.
- [ ] `price_scale_decimals` matches the instrument's quoting convention (4 equities/futures, 5 FX pipettes, 8 most crypto).
- [ ] Scaled-price and quantity fields are **64-bit** — a 32-bit scaled price at 4 decimals saturates at $\pm 214{,}748.36$.
- [ ] Float representation noise (`0.1 + 0.2`, a repeated-addition price walk) is *not* reported as precision loss.
- [ ] Genuine precision loss **is** counted, logged, and surfaced as `price_precision_loss_ticks` — it is irreversible.
- [ ] Columns are written column-major, not row-major.
- [ ] The blob carries a magic value and a format version, and the version is bumped on any layout change.

## Compression
- [ ] The codec actually used is recorded on the report — no silent fallback from Zstandard to zlib.
- [ ] Compression level is deliberate (9 zlib / 19 zstd by default; zstd 20–22 need `--ultra` and much more memory).
- [ ] The raw-size baseline is **measured**, never a bytes-per-row constant, and `raw_size_basis` says which baseline was used.
- [ ] `meets_compression_target` is asserted rather than the target being stored and ignored.

## Losslessness
- [ ] The round trip is executed in CI: encode, decode, compare field-for-field.
- [ ] Round-trip coverage includes a high-priced instrument, a negative price, a quantity above $2^{32}$, duplicate timestamps, and all three side values.
- [ ] The decoder rejects a bad magic, an unsupported format version, and a truncated or over-long buffer.
- [ ] Decoded prices are compared to within half a tick, not with `==`.

## Storage layout
- [ ] Files are partitioned by `symbol/year=YYYY/month=MM/date=YYYY-MM-DD`.
- [ ] Storage tiers (Hot, Warm, Cold) are assigned from the batch's **actual** age — no default age is guessed.
- [ ] Tier boundaries have been calibrated against real query patterns and storage pricing, not adopted as standards.

## Scope
- [ ] It is understood that `scripts/` is a stdlib-only model of the encoding step, not a Parquet writer — the `TKC1` blob is unreadable by DuckDB, Spark and pyarrow.
- [ ] The production archive uses a real Parquet writer with `DELTA_BINARY_PACKED` / `BYTE_STREAM_SPLIT` / `ZSTD`, and `LZ4_RAW` rather than the deprecated `LZ4`.

## Testing
- [ ] Automated Testing: Run `python -m unittest discover -s skills/historical-tick-data-storage-and-compaction/scripts` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
