# Standards — historical-tick-data-storage-and-compaction

## Configuration defaults (calibrate before use)

These are the reference implementation's defaults, **not** industry standards. No
standards body or regulator publishes a mandatory compression ratio, tiering boundary,
or price scale for tick archives. Calibrate each against your own instruments, query
patterns and storage pricing, and record the rationale.

| Parameter | Default | What it actually does |
|---|---|---|
| `target_min_compression_ratio` | $5.0\times$ | Surfaced on the report as `meets_compression_target`. The engine *reports* the shortfall; it does not refuse to write. Achievable ratios are entirely data-dependent — a quiet symbol with ten distinct prices compresses two orders of magnitude better than a volatile one. |
| `price_scale_decimals` | $4$ | Decimal places retained in the integer-scaled price. Must match the instrument's quoting convention: 4 for most equities and futures, 5 for FX pipettes, 8 for most crypto. Anything finer is quantized and counted as precision loss. |
| `codec` | `auto` | Zstandard when the optional `zstandard` package is installed, otherwise zlib. `'zstd'` raises when the package is absent rather than downgrading silently. |
| `compression_level` | 9 (zlib) / 19 (zstd) | Archival batches are written once and read many times, so compression time is the cheap side of the trade. |
| `hot_tier_max_age_days` | $7$ | Inclusive upper age bound for `HOT_TIER`. |
| `warm_tier_max_age_days` | $90$ | Inclusive upper age bound for `WARM_TIER`; above it, `COLD_TIER`. |

The tier label emitted here is a coarse annotation on a compacted batch, and its 7/90-day
defaults are **not** the same mapping as the cost/retention placement in
`data-retention-policy-and-storage-tiering` (30 / 365 / 2555 days against named cloud
tiers with a price model and retention floors). Where the two disagree, that skill is the
authority for *where an object should live*; this one only records what the compaction job
was told the batch's age was. Reconcile the boundaries deliberately rather than assuming
either set is canonical.

## Engineering conventions (house rules, not external mandates)

| Convention | Rationale |
|---|---|
| Nanosecond timestamps are delta-encoded before compression. | Successive tick timestamps differ by microseconds to milliseconds; the absolute epoch value is ~19 digits of shared prefix that carries no information after the first row. |
| Columns are written column-major, not row-major. | Grouping each field's bytes lets the compressor see runs of similar magnitude instead of high-entropy field alternation. Measured effect on a 20k-tick synthetic batch at the same zlib level: ~4.7% smaller. Real but modest — this is why production archives should use a Parquet writer's per-column encoding rather than a single-blob approximation. |
| Files are partitioned by `symbol/year=YYYY/month=MM/date=YYYY-MM-DD`. | Hive-style directory partitioning lets a date predicate prune directories instead of scanning. |
| Every archive blob carries a magic value and a format version. | A decoder must be able to refuse a blob it does not understand rather than misread it. See `tick-data-schema-versioning`. |
| Every compression ratio is published alongside the baseline it was measured against. | The numerator is the number most often fabricated; a ratio without its basis is not a measurement. |

## Apache Parquet facts (verified against the format specification)

Source: [apache/parquet-format](https://github.com/apache/parquet-format) —
`Encodings.md` and `Compression.md`.

| Fact | Location |
|---|---|
| `DELTA_BINARY_PACKED` supports **INT32 and INT64** only. Differences are taken between consecutive elements; the first element of a block uses the previous block's last value. | `Encodings.md`, DELTA_BINARY_PACKED |
| `BYTE_STREAM_SPLIT` supports **INT32, INT64, FLOAT, DOUBLE, FIXED_LEN_BYTE_ARRAY**. It scatters each value's bytes across K byte-streams to improve downstream compression. | `Encodings.md`, BYTE_STREAM_SPLIT |
| `DELTA_LENGTH_BYTE_ARRAY` "is always preferred over PLAIN for byte array columns". | `Encodings.md`, DELTA_LENGTH_BYTE_ARRAY |
| Compression codecs defined: `UNCOMPRESSED`, `SNAPPY`, `GZIP`, `LZO`, `BROTLI`, `LZ4`, `ZSTD`, `LZ4_RAW`. | `Compression.md` |
| `ZSTD` is based on **RFC 8878**; the Zstandard reference library is authoritative for ambiguities. | `Compression.md`, ZSTD |
| `LZ4` is **deprecated** because of an undocumented framing scheme; writers are "strongly suggested" to deprecate it and move users to `LZ4_RAW`. | `Compression.md`, LZ4 |
| The specification gives **no** codec-choice guidance beyond noting the codecs "cover different areas in the compression ratio / processing cost spectrum". | `Compression.md` |

Implication for this skill: the delta-encoding and columnar-layout steps modelled in
`scripts/` are **already native Parquet features**. In production, configure the
Parquet writer rather than hand-rolling the encoding.

## Zstandard compression levels (verified against the reference tool)

Source: [`zstd(1)` manual](https://github.com/facebook/zstd/blob/dev/programs/zstd.1.md).

- Normal level range is **1–19**, default **3**.
- `--ultra` unlocks levels **20–22**, "using a lot more memory".

The reference implementation defaults to 19 — the top of the normal range — so archival
compression does not silently impose ultra-level memory requirements on whatever
process later decompresses the file.

## Recordkeeping: when compaction stops being a free optimization

**Jurisdiction: United States. Applies to SEC-registered broker-dealers, security-based
swap dealers and major security-based swap participants — not to market-data archives
generally.** Determine which category an archive falls into before compacting it.

SEC Rule 17a-4(f) governs the format in which electronic records required by Rules
17a-3 and 17a-4 are preserved. The 2022 amendments
([Release 34-96034](https://www.sec.gov/files/rules/final/2022/34-96034.pdf), adopted
12 October 2022, effective 3 January 2023) **retained WORM as an option and added an
audit-trail alternative**: an electronic recordkeeping system that permits recreation
of an original record if it is modified or deleted, maintaining a complete time-stamped
audit trail of all modifications and deletions, the date and time of each action, and
where applicable the identity of the person responsible.

A compaction job rewrites and deletes source files by design. Where the archive holds
records covered by these rules, that rewriting must occur inside a WORM or
audit-trailed system. Where it holds vendor market data the firm is not obliged to
preserve, it does not. This skill does not decide the classification for you, and
nothing in the reference implementation enforces it — see
`data-retention-policy-and-storage-tiering`, `record-retention-periods-by-jurisdiction`
and `backtest-audit-trail-for-regulatory-review`.

Other jurisdictions impose their own record-preservation obligations with different
scopes and retention periods; none of them are implemented or checked here.

## Known limitations of the reference implementation

- **It is not a Parquet writer.** The output is an opaque `TKC1` blob — no predicate
  pushdown, no column pruning, no row-group statistics, unreadable by DuckDB, Spark or
  pyarrow.
- **No file I/O and no partitioning.** The partition layout above is guidance; the
  module returns bytes and a report.
- **Batch-oriented and in-memory.** Size a batch to a partition, not to an archive.
- **Lossy above the configured price scale.** Quantization is counted and reported via
  `price_precision_loss_ticks`, never silently swallowed — but it is not reversible.
- **Sorting, gap detection and clock correction are upstream concerns.** The encoder
  rejects an out-of-order batch; it does not repair one.
