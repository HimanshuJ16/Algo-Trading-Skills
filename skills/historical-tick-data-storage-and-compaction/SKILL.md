---
name: historical-tick-data-storage-and-compaction
description: >-
  Use when designing or auditing a historical tick archive — delta-encoding timestamps and prices, laying columns out for compression, choosing a Parquet codec, and tiering partitions by age — so the archive stays small, stays losslessly decodable, and reports a compression ratio that is measured rather than assumed.
domain: Data Management Global
subdomain: Historical Tick Storage & Compaction Architecture
tags: ["tick-storage", "parquet", "delta-encoding", "zstandard", "columnar-compression", "storage-tiering", "data-compaction"]
brokers_frameworks: ["Apache Parquet", "PyArrow / DuckDB", "Zstandard (RFC 8878)", "Python Standard Library"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when designing high-frequency tick databases, backtest data repositories, and cloud storage compaction pipelines. High-frequency tick data (millions of raw trade/quote records per day per symbol) is dominated by two columns — a 64-bit nanosecond timestamp and a price — that are individually high-entropy but whose *successive differences* are tiny. Delta encoding exposes that structure, a columnar layout lets the compressor see it, and a general-purpose codec exploits it.

The reference implementation in `scripts/` is a **stdlib-only model of the encoding step**, not a Parquet writer. It produces an opaque `TKC1` blob that no query engine can read. Use it to reason about and test the encoding mechanics; use a real Parquet writer to produce the archive.

## When NOT to Use

- **As a substitute for a Parquet writer.** Parquet already specifies these techniques natively — `DELTA_BINARY_PACKED` for INT32/INT64 columns, `BYTE_STREAM_SPLIT` for float columns, `ZSTD` as a codec. Hand-rolling them gives up predicate pushdown, column pruning, row-group statistics, and readability by DuckDB/Spark/pyarrow, in exchange for nothing.
- **On live or streaming ticks.** This is a batch encoder: the entire batch is held in memory and emitted as one blob. Size a batch to a partition (one symbol-day), not to a stream. For live fan-out see `kafka-based-tick-distribution-at-scale` and `tick-buffering-burst-handling`.
- **To decide what may be deleted or rewritten.** Compaction and retention are different questions. If the archive *is* the firm's regulatory books and records rather than vendor market data, the recordkeeping rules constrain how it may be modified — see the Common Pitfalls entry below and `data-retention-policy-and-storage-tiering`.
- **On a price whose quoting convention exceeds the configured scale.** The default is 4 decimals. A 5-decimal FX quote or an 8-decimal crypto quote will be quantized; the loss is counted and reported, but it is not recoverable. Set `price_scale_decimals` first.
- **On an unsorted or gappy batch.** Delta encoding assumes a monotonic sequence. Ordering and gap detection belong upstream — see `sequence-number-gap-detection-for-feeds` and `clock-skew-correction-for-tick-timestamps`.

## Prerequisites

- Raw tick records (`timestamp_nanos`, `price`, `quantity`, `side`), **sorted by non-decreasing timestamp**.
- The instrument's quoting convention, expressed as `price_scale_decimals` (4 for most equities and futures, 5 for FX pipettes, 8 for most crypto).
- The age of the batch in days, for tier assignment. There is no default — a guessed age produces a confident-looking tier that can be wrong by two tiers.
- A baseline to measure compression against: the measured byte size of the source file being replaced, or the module's canonical CSV baseline.

## Workflow

1. **Validate the batch before encoding anything.**
   - Reject non-monotonic timestamps. Encoding an out-of-order batch *succeeds* and decodes cleanly — which is exactly the danger: the archive then preserves an ordering the feed never had. Sort upstream, deliberately, so the reordering is auditable.
   - Reject non-finite prices and negative quantities. Accept *negative prices*: the April 2020 WTI expiry settled below zero, and an archive that rejects negative prices discards the session a researcher most wants.
   - Reject an unrecognized `side` rather than defaulting it. A `1 if side == "BUY" else 2` mapping silently records every unparsed value as SELL. Use an explicit `UNKNOWN` code when the feed does not classify aggressor side.

2. **Delta-encode timestamps and integer-scaled prices.**
   - $\Delta t_i = t_i - t_{i-1}$ for $i \ge 1$; the first element is stored absolute so the sequence is self-contained.
   - $\Delta p_i = P^{\text{int}}_i - P^{\text{int}}_{i-1}$, where $P^{\text{int}} = \text{round}(P \times 10^{d})$ at $d$ = `price_scale_decimals`.
   - **Decision point — width the delta fields for the instrument, not the sample.** A 32-bit scaled-price field at 4 decimals saturates at $\pm 214{,}748.36$. That is fine for most equities and a hard crash for a $700{,}000$ Berkshire A share or an 8-decimal crypto quote. Use 64-bit fields.
   - **Decision point — distinguish float noise from real precision loss.** A price walk built by repeated addition lands on `150.15000000000003`; that is representation noise and must not be flagged. A 5-decimal quote truncated to 4 decimals is real, irreversible loss and must be. Compare the discarded fraction against a float-noise bound, and count and report what crosses it.

3. **Lay the columns out column-major, then compress.**
   - Emit all timestamp deltas, then all price deltas, then all quantities, then all side codes. Row-major interleaving puts a timestamp delta next to a price delta next to a quantity, so the compressor sees high-entropy alternation instead of runs of similar magnitude.
   - **Decision point — the codec must be recorded, never inferred.** A pipeline that silently falls back from Zstandard to zlib when a package is missing writes an archive whose actual codec no longer matches its documentation. Fail, or record what was actually used.
   - Compression Ratio $= \dfrac{\text{Raw Size Bytes}}{\text{Compacted Size Bytes}}$ — and see step 5, because the numerator is where this goes wrong.

4. **Verify the round trip before trusting the archive.** Decode the blob and compare against the input. An encoder without a decoder is not an archive, and a compaction pipeline whose losslessness has never been executed is an untested claim about irreplaceable data.

5. **Report the ratio together with its baseline.** A compression ratio is a statement about two numbers, and the raw-size number is the one that is usually fabricated. Measure it — from the source file being replaced, or by actually serializing the canonical text form — and record on the report *which* baseline was used. Assert the target ratio explicitly rather than storing a threshold nothing checks.

6. **Assign the storage tier from the batch's actual age** and emit a structured `TickStorageCompactionReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Quoting a compression ratio against an assumed raw size.** "60 bytes per tick" times the tick count is not a measurement of anything; multiply by a different guess and the same archive is 5× or 15×. The ratio inherits all of its meaning from its denominator *and* its numerator — measure the source file, or serialize the canonical form and measure that, and publish which one you used.
- **Documenting a codec you do not run.** An archive labelled Parquet/Zstandard but written as a hand-rolled zlib blob cannot be opened by DuckDB, cannot be column-pruned, and cannot be handed to a vendor. Downstream consumers will plan against the label, not the bytes.
- **Shipping an encoder with no decoder.** Losslessness is the only property of an archive that actually matters, and it is the one most often never executed. If nothing decodes the blob in CI, the pipeline is a claim, not a guarantee.
- **Silently coercing an unrecognized `side`.** A vendor that emits `"B"`/`"S"`, or `""` for auction prints, gets every one of those ticks archived as SELL. The corruption is invisible until a researcher builds an order-flow-imbalance signal on it years later.
- **Silently truncating price precision.** Archiving a 5-decimal FX feed at 4 decimals throws away the pipette on every tick. Nothing errors, the ratio looks *better*, and the discarded digit is gone for good.
- **Delta-encoding an unsorted batch.** It succeeds. The deltas go negative, the decode reproduces them faithfully, and the archive now asserts a tick ordering the market never had.
- **Storing ticks in uncompressed CSV or JSON.** Both re-encode a 64-bit integer timestamp as ~19 ASCII digits per row and repeat field names per record in the JSON case, before any of the delta structure is exposed.
- **Un-partitioned storage layout.** One giant file per symbol forces a full scan for a single day's ticks; partition by `symbol/year=YYYY/month=MM/date=YYYY-MM-DD` so a date predicate prunes directories.
- **Treating compaction as unconstrained because it is "just an optimization".** For vendor market data it usually is. For records that are a US broker-dealer's own books and records, SEC Rule 17a-4(f) governs the preservation format: the 2022 amendments (Release 34-96034, effective 3 January 2023) retained WORM and added an audit-trail alternative requiring a complete time-stamped audit trail of every modification and deletion. A compaction job that rewrites and deletes source files without that audit trail is a compliance problem, not a storage optimization. Confirm which category your archive falls into *before* the first compaction run.

## Verification

- Round trip: encode a batch and decode it, and assert field-for-field equality. Cover a $712{,}345.6789$ price (overflows a 32-bit scaled-price field), a negative price ($-37.63$), a $10^{10}$ quantity (overflows a 32-bit unsigned field), duplicate nanosecond timestamps, and all three side values.
- Baseline honesty: assert `raw_size_bytes` equals `canonical_csv_size_bytes(ticks)` when no explicit size is given, that it equals the supplied value when one is, and that `raw_size_basis` distinguishes the two. Assert the baseline is *not* a fixed bytes-per-row constant.
- Layout: assert the buffer is exactly $14 + n \times 25$ bytes, that the header carries the magic, format version, price scale and tick count, and that the first element of each delta column is the absolute value.
- Columnar payoff: repack the identical deltas row-major and assert the column-major buffer compresses smaller at the same zlib level.
- Precision: assert a repeated-addition price and `0.1 + 0.2` are *not* flagged, that `1.23456` at 4 decimals *is*, and that the same quote at 5 decimals is lossless and round-trips.
- Negative checks: an out-of-order timestamp, an unrecognized `side`, a `NaN` price, a negative quantity, a missing `age_days`, a non-positive `raw_size_bytes`, and a decoder fed a bad magic / bad version / truncated / over-long buffer must each raise.
- Target enforcement: assert `meets_compression_target` is `True` against a 5.0× target and `False` against a target one above the achieved ratio.
- Run `python scripts/test_historical_tick_data_storage_and_compaction.py` and confirm 100% pass rate. The Zstandard test skips cleanly when the optional `zstandard` package is absent.

## Related Skills

- `data-retention-policy-and-storage-tiering`
- `tick-data-schema-versioning`
- `historical-order-book-reconstruction-from-message-logs`
- `backtest-database-schema-for-point-in-time-queries`
- `clock-skew-correction-for-tick-timestamps`
