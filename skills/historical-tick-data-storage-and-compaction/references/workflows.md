# Workflows for Historical Tick Data Storage and Compaction

Full procedure behind `SKILL.md`. Steps 1–6 map to the numbered workflow there.

## 0. Classify the archive before writing any code

Decide, and record, whether the data is **vendor market data** the firm is free to
re-encode at will, or **records the firm is required to preserve**. The second case
constrains how compaction may rewrite or delete source files — see the recordkeeping
section of `references/standards.md`. This classification is not derivable from the
bytes and no code in `scripts/` enforces it.

Then fix the two parameters that cannot be inferred from the data:

- `price_scale_decimals` — from the instrument's quoting convention (4 equities/futures,
  5 FX pipettes, 8 most crypto). Getting this wrong quantizes every tick.
- the batch's age in days — drives tier assignment. There is no default.

## 1. Ingest and validate

Ingest the raw batch as `RawTickRecord(timestamp_nanos, price, quantity, side)`.

Validate before encoding, and fail loudly:

| Condition | Action | Why |
|---|---|---|
| `timestamp_nanos` not an `int`, or negative | raise | A float nanosecond timestamp has already lost precision — 2^53 nanoseconds is ~104 days, so epoch-nanosecond values are past the exactly-representable range of a double. |
| Timestamps strictly decreasing | raise, naming the index and both values | Delta encoding an unsorted batch *succeeds* and decodes cleanly. Sort upstream so the reordering is auditable. |
| Timestamps equal | **accept** | Two trades in the same nanosecond are legal on a fast venue. |
| `price` NaN or ±inf | raise | A non-finite price cannot be scaled to an integer, and silently dropping it leaves a hole. |
| `price` negative | **accept** | April 2020 WTI settled below zero. Rejecting negative prices discards exactly the session a researcher wants. |
| `quantity` not an `int`, or negative | raise | Negative size is not a tick; it is a sign convention leaking in from a position feed. |
| `side` outside `{BUY, SELL, UNKNOWN}` | raise, listing the accepted values | A default-to-SELL mapping corrupts order-flow signals invisibly. `UNKNOWN` exists for feeds that do not classify aggressor side. |

Case is normalized (`"buy"` → `BUY`); anything else is an error, not a guess.

## 2. Delta-encode

For each column, store the first element absolute and subsequent elements as
differences, so the sequence is self-contained:

- $\Delta t_0 = t_0$, $\Delta t_i = t_i - t_{i-1}$.
- $P^{\text{int}}_i = \text{round}_{\text{half-even}}(P_i \times 10^{d})$;
  $\Delta p_0 = P^{\text{int}}_0$, $\Delta p_i = P^{\text{int}}_i - P^{\text{int}}_{i-1}$.

Two things go wrong here in practice:

**Field width.** A 32-bit scaled-price field at $d = 4$ saturates at
$\pm 214{,}748.36$ — fine for most equities, a hard failure for a $\$700{,}000$ share
or an 8-decimal crypto quote. Likewise a 32-bit unsigned quantity caps at ~4.29e9.
Use 64-bit fields; the extra bytes are mostly leading zeros and compress away.

**Distinguishing float noise from precision loss.** Quantize via `Decimal` on the
float's shortest round-trip repr, then compare the discarded fraction against a
noise bound of $|s| \times 10^{-12} + 10^{-9}$ (relative term ≈ 4500 double ULPs):

- A price walk built by repeated addition yields `150.15000000000003`, and
  `0.1 + 0.2` yields `0.30000000000000004`. Residual ~1e-10 — representation noise,
  **not** flagged. Flagging these would make the warning worthless.
- A 5-decimal FX quote at $d = 4$ leaves a residual of ~0.4 scaled units. Real,
  irreversible loss — **flagged**, counted, logged, and surfaced on the report as
  `price_precision_loss_ticks`.

## 3. Lay out columns and compress

Emit, in order:

```
header:  magic "TKC1" (4B) | format version (1B) | price scale (1B) | tick count (8B)
column:  timestamp deltas   -- int64 big-endian, n elements
column:  scaled price deltas -- int64 big-endian, n elements
column:  quantities          -- int64 big-endian, n elements
column:  side codes          -- uint8, n elements
```

Total: $14 + 25n$ bytes before compression.

The magic and version exist so a decoder can refuse an unrecognized blob instead of
misreading it; bump the version on any layout change (`tick-data-schema-versioning`).

Pack the int64 columns with `array.array('q', ...)` plus a `byteswap()` on
little-endian hosts, not `struct.pack('>{n}q', *values)` — the latter materialises one
Python argument per tick, which does not survive this skill's stated multi-million-tick
scale.

Then compress the whole buffer. Record which codec actually ran. A pipeline that
silently falls back from Zstandard to zlib produces an archive whose real codec no
longer matches its documentation, so an explicit `zstd` request raises when the
package is missing rather than downgrading.

## 4. Verify the round trip

Decode and compare field-for-field. This is the only property of an archive that
matters and the one most often left unexecuted.

The decoder reads the price scale from the buffer's own header, not from the engine,
so a blob written at one scale decodes correctly on an engine configured with another.
It rejects a bad magic, an unsupported version, and a truncated or over-long buffer —
guessing at corrupt input yields plausible-looking wrong ticks, which is worse than
failing.

Note that the decoded price is the *canonical* value at the recorded scale. A float
that is a hair off a representable price (`1.23456 + 2 * 0.00001` is
`1.2345799999999999`) decodes to the clean `1.23458` and compares unequal to the input
float even though nothing meaningful was lost. Compare to within half a tick, not with
`==`.

## 5. Measure the ratio against a declared baseline

$$\text{Compression Ratio} = \frac{\text{Raw Size Bytes}}{\text{Compacted Size Bytes}}$$

The denominator is measured for free. The numerator is where this goes wrong: a
constant bytes-per-row estimate is not a measurement, and moving the constant moves the
headline ratio proportionally. Two defensible baselines:

| Basis | When | How |
|---|---|---|
| `MEASURED_SOURCE` | An actual file is being replaced | `os.path.getsize()` on that file, passed as `raw_size_bytes`. The ratio then describes that file. |
| `CANONICAL_CSV_BASELINE` | No source file — the ticks came from an API or a stream | Serialize the records to the canonical CSV form (`timestamp_nanos,price,quantity,side`, price at the configured scale, UTF-8, no header) and measure the result. |

Publish the basis with the ratio; record `meets_compression_target` rather than
storing a threshold nothing checks.

## 6. Tier and report

Assign from the batch's actual age against the configured inclusive bounds
(`age ≤ hot_max` → `HOT_TIER`; `age ≤ warm_max` → `WARM_TIER`; else `COLD_TIER`), then
emit `TickStorageCompactionReport` carrying the sizes, the ratio, the basis, the codec
and level actually used, the price scale, the precision-loss count, the target verdict,
and the format version.

## 7. Promote to a real columnar format

The reference implementation stops here deliberately. For the production archive:

- Write Parquet, and let it do the encoding — `DELTA_BINARY_PACKED` on the INT64
  timestamp and scaled-price columns, `BYTE_STREAM_SPLIT` where a price stays a float,
  `ZSTD` as the codec, `LZ4_RAW` rather than the deprecated `LZ4`.
- Partition Hive-style as `symbol/year=YYYY/month=MM/date=YYYY-MM-DD` so a date
  predicate prunes directories rather than scanning.
- Size row groups so that a typical query reads whole row groups, and keep per-column
  statistics so predicates can skip them.
