"""
historical-tick-data-storage-and-compaction: delta-encoding + columnar compaction
reference implementation for historical tick archives.

What this module actually does
------------------------------
It takes a batch of raw tick records and produces a single self-describing binary
blob:

    1. Delta-encode ``timestamp_nanos`` and the integer-scaled price.
    2. Lay the four fields out **column-major** (all timestamp deltas, then all price
       deltas, then all quantities, then all side codes) rather than row-major, so
       bytes with similar magnitude and entropy sit next to each other.
    3. Compress the whole buffer with a general-purpose codec (zlib from the standard
       library; Zstandard when the optional ``zstandard`` package is installed).
    4. Report the compression achieved against an explicitly-identified baseline.

Relationship to Parquet / Zstandard
-----------------------------------
This module is a **stdlib-only reference implementation of the encoding step**, not a
Parquet writer. It produces an opaque ``TKC1`` blob, not a Parquet file: nothing here
is readable by DuckDB, Spark, or pyarrow. It exists to make the encoding mechanics
inspectable and testable without pulling a columnar engine into the repository's
dependency set.

In production, prefer letting Parquet do this natively rather than hand-rolling it --
the Apache Parquet format already specifies the same two techniques:

  * ``DELTA_BINARY_PACKED`` (Parquet ``Encodings.md``) delta-encodes INT32/INT64
    columns, which is what timestamp and scaled-price columns should be.
  * ``BYTE_STREAM_SPLIT`` (ibid.) applies to INT32, INT64, FLOAT, DOUBLE and
    FIXED_LEN_BYTE_ARRAY where a price column is kept as a float.
  * ``ZSTD`` is a first-class Parquet compression codec (Parquet ``Compression.md``,
    based on RFC 8878). Note that plain ``LZ4`` is deprecated there in favour of
    ``LZ4_RAW``.

The delta/columnar layout below mirrors those techniques so the trade-offs are
visible; it does not replace them.

Losslessness
------------
``delta_encode_ticks`` / ``delta_decode_ticks`` round-trip exactly for any price that
is exactly representable at ``price_scale_decimals``. Prices that are *not* (a
5-decimal FX quote at the 4-decimal default, an 8-decimal crypto quote) are quantized
with ROUND_HALF_EVEN and **counted**: the count is surfaced on the report as
``price_precision_loss_ticks`` and logged as a warning. Precision loss in a tick
archive is irreversible, so it is reported rather than swallowed. Set
``price_scale_decimals`` to match the instrument's actual quoting convention.

Deliberate limitations
----------------------
- **No Parquet output, no partitioning, no file I/O.** Partition-layout guidance lives
  in ``references/standards.md``; this module returns bytes and a report.
- **Compression ratios are data-dependent and are measured, never assumed.** The
  headline ratio depends entirely on the baseline it is measured against -- see
  ``raw_size_basis``. A ratio quoted without its baseline is meaningless.
- **Batch-oriented.** The whole batch is held in memory and encoded as one blob;
  size the batch to a partition (e.g. one symbol-day), not to a whole archive.
- **Retention and WORM obligations are out of scope.** Compaction that rewrites or
  deletes source files is unremarkable for vendor market data, but if the archive
  *is* the firm's regulatory books and records the recordkeeping rules govern how it
  may be modified. See ``references/standards.md`` and the
  ``data-retention-policy-and-storage-tiering`` skill.
"""
from __future__ import annotations

import array
import logging
import struct
import sys
import zlib
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: On-disk format identifier. Bump ``FORMAT_VERSION`` on any layout change so a
#: decoder can refuse a blob it does not understand rather than misread it.
FORMAT_MAGIC = b"TKC1"
FORMAT_VERSION = 1

#: magic (4s) | format version (B) | price scale decimals (B) | tick count (Q)
_HEADER = struct.Struct(">4sBBQ")

#: Side codes written to the archive. ``UNKNOWN`` exists so a feed that does not
#: classify aggressor side can still be archived without being mislabelled as SELL.
SIDE_CODES: Dict[str, int] = {"UNKNOWN": 0, "BUY": 1, "SELL": 2}
SIDE_NAMES: Dict[int, str] = {code: name for name, code in SIDE_CODES.items()}

DEFAULT_PRICE_SCALE_DECIMALS = 4
MAX_PRICE_SCALE_DECIMALS = 9

#: zlib accepts 0-9; 9 is maximum compression (archival batches are written once and
#: read many times, so compression time is the cheap side of the trade).
DEFAULT_ZLIB_LEVEL = 9

#: zstd's normal level range is 1-19 (default 3); levels 20-22 require ``--ultra`` and
#: "a lot more memory" (zstd(1) manual). 19 is the top of the normal range.
DEFAULT_ZSTD_LEVEL = 19

#: House defaults for age-based tiering. These are operational choices, not a
#: standard -- calibrate them against actual query patterns and storage pricing.
DEFAULT_HOT_TIER_MAX_AGE_DAYS = 7
DEFAULT_WARM_TIER_MAX_AGE_DAYS = 90

#: Tolerance separating float-representation noise from genuine precision loss when
#: quantizing a price. ``150.00 + 3 * 0.05`` is 150.15000000000002 in binary float and
#: must not be reported as precision loss; a 5-decimal FX quote truncated to 4
#: decimals must be. The relative term is ~4500 double-precision ULPs.
_PRICE_NOISE_RELATIVE = Decimal("1e-12")
_PRICE_NOISE_ABSOLUTE = Decimal("1e-9")

#: Bytes per element in each int64 column.
_INT64_BYTES = 8

#: Baseline the compression ratio is measured against. A ratio is only interpretable
#: alongside this value.
RAW_SIZE_BASIS_MEASURED = "MEASURED_SOURCE"
RAW_SIZE_BASIS_CANONICAL_CSV = "CANONICAL_CSV_BASELINE"


@dataclass
class RawTickRecord:
    timestamp_nanos: int
    price: float
    quantity: int
    side: str                           # 'BUY', 'SELL' or 'UNKNOWN'


@dataclass
class TickStorageCompactionReport:
    symbol: str
    total_ticks_processed: int
    raw_size_bytes: int
    compacted_size_bytes: int
    compression_ratio: float            # raw_size / compacted_size (e.g. 8.5x)
    space_savings_pct: float            # (1 - compacted/raw) * 100
    storage_tier: str                   # 'HOT_TIER', 'WARM_TIER', 'COLD_TIER'
    audit_notes: str
    raw_size_basis: str                 # what raw_size_bytes actually measures
    codec: str                          # codec actually used ('zlib' or 'zstd')
    compression_level: int
    price_scale_decimals: int
    price_precision_loss_ticks: int     # ticks quantized lossily at this scale
    meets_compression_target: bool      # ratio >= target_min_compression_ratio
    format_version: int


def _load_zstd():
    """Return the optional ``zstandard`` module, or ``None`` when it is not installed."""
    try:
        import zstandard  # type: ignore
    except ImportError:
        return None
    return zstandard


def _validate_price_scale(price_scale_decimals: int) -> None:
    if not isinstance(price_scale_decimals, int) or isinstance(price_scale_decimals, bool):
        raise TypeError("price_scale_decimals must be an int.")
    if not 0 <= price_scale_decimals <= MAX_PRICE_SCALE_DECIMALS:
        raise ValueError(
            f"price_scale_decimals must be in [0, {MAX_PRICE_SCALE_DECIMALS}], "
            f"got {price_scale_decimals}."
        )


def canonical_csv_size_bytes(
    ticks: Sequence[RawTickRecord],
    price_scale_decimals: int = DEFAULT_PRICE_SCALE_DECIMALS,
) -> int:
    """
    Size in bytes of the canonical uncompressed CSV rendering of ``ticks``.

    The row format is exactly ``timestamp_nanos,price,quantity,side`` plus a newline,
    with the price rendered at ``price_scale_decimals``, UTF-8 encoded, and no header
    row. This is *measured* by serializing the actual records -- it is not a
    bytes-per-row estimate -- so the resulting compression ratio is reproducible from
    the same input.

    A different source format (JSON with field names, a vendor's fixed-width layout, a
    CSV carrying a symbol column) will have a materially different size. When the
    archive replaces a real file, measure that file and pass its length as
    ``raw_size_bytes`` instead of relying on this baseline.
    """
    _validate_price_scale(price_scale_decimals)
    total = 0
    for tick in ticks:
        row = "{ts},{px:.{d}f},{qty},{side}\n".format(
            ts=tick.timestamp_nanos,
            px=tick.price,
            d=price_scale_decimals,
            qty=tick.quantity,
            side=tick.side,
        )
        total += len(row.encode("utf-8"))
    return total


def _pack_int64_column(values: Sequence[int]) -> bytes:
    """
    Pack ``values`` as a big-endian int64 column.

    Uses ``array`` rather than ``struct.pack('>{n}q', *values)`` because the latter
    materialises one Python argument per tick, and this module's stated scale is
    multi-million-tick batches.
    """
    column = array.array("q", values)
    if sys.byteorder == "little":
        column.byteswap()
    return column.tobytes()


def _unpack_int64_column(buffer: bytes, offset: int, count: int) -> List[int]:
    """Inverse of :func:`_pack_int64_column`."""
    column = array.array("q")
    column.frombytes(buffer[offset:offset + count * _INT64_BYTES])
    if sys.byteorder == "little":
        column.byteswap()
    return column.tolist()


class HistoricalTickStorageCompactionEngine:
    """
    Delta-encodes and compacts historical tick batches, and assigns an age-based
    storage tier.

    The engine holds configuration only, so one instance can be reused across symbols.
    """

    def __init__(
        self,
        target_min_compression_ratio: float = 5.0,
        price_scale_decimals: int = DEFAULT_PRICE_SCALE_DECIMALS,
        codec: str = "auto",
        compression_level: Optional[int] = None,
        hot_tier_max_age_days: int = DEFAULT_HOT_TIER_MAX_AGE_DAYS,
        warm_tier_max_age_days: int = DEFAULT_WARM_TIER_MAX_AGE_DAYS,
    ) -> None:
        """
        Args:
            target_min_compression_ratio: Ratio the archive is expected to reach.
                Surfaced on the report as ``meets_compression_target``; the engine
                reports the shortfall, it does not refuse to write.
            price_scale_decimals: Decimal places retained in the integer-scaled price.
                Must match the instrument's quoting convention -- 4 suits most equity
                and futures quotes, FX pipettes need 5, crypto often needs 8.
            codec: ``'auto'`` (Zstandard when the optional ``zstandard`` package is
                installed, otherwise zlib), ``'zlib'``, or ``'zstd'``. ``'zstd'``
                raises if the package is absent rather than silently downgrading --
                a silent downgrade would change the archive's codec without a trace.
            compression_level: Codec level; defaults to 9 for zlib and 19 for zstd.
            hot_tier_max_age_days: Upper age bound (inclusive) for ``HOT_TIER``.
            warm_tier_max_age_days: Upper age bound (inclusive) for ``WARM_TIER``.
        """
        if target_min_compression_ratio <= 0:
            raise ValueError("target_min_compression_ratio must be positive.")
        _validate_price_scale(price_scale_decimals)
        if codec not in ("auto", "zlib", "zstd"):
            raise ValueError(f"codec must be 'auto', 'zlib' or 'zstd', got {codec!r}.")
        if hot_tier_max_age_days < 0:
            raise ValueError("hot_tier_max_age_days must be non-negative.")
        if warm_tier_max_age_days <= hot_tier_max_age_days:
            raise ValueError(
                "warm_tier_max_age_days must exceed hot_tier_max_age_days "
                f"(got {warm_tier_max_age_days} <= {hot_tier_max_age_days})."
            )

        self.target_min_compression_ratio = target_min_compression_ratio
        self.price_scale_decimals = price_scale_decimals
        self.hot_tier_max_age_days = hot_tier_max_age_days
        self.warm_tier_max_age_days = warm_tier_max_age_days

        self._zstd = _load_zstd()
        if codec == "zstd" and self._zstd is None:
            raise RuntimeError(
                "codec='zstd' requires the optional 'zstandard' package "
                "(pip install zstandard). Use codec='zlib' or codec='auto' to fall "
                "back to the standard library."
            )
        self.codec = ("zstd" if self._zstd is not None else "zlib") if codec == "auto" else codec

        default_level = DEFAULT_ZSTD_LEVEL if self.codec == "zstd" else DEFAULT_ZLIB_LEVEL
        self.compression_level = default_level if compression_level is None else compression_level
        if self.codec == "zlib" and not 0 <= self.compression_level <= 9:
            raise ValueError("zlib compression_level must be in [0, 9].")

    # ------------------------------------------------------------------ encoding

    def _quantize_price(self, price: float, index: int) -> Tuple[int, bool]:
        """
        Convert ``price`` to its integer representation at ``price_scale_decimals``.

        Returns the scaled integer and whether the discarded fraction exceeded
        float-representation noise. Quantizes via ``Decimal`` on the shortest
        round-trip repr of the float, so ``150.00 + 3 * 0.05`` scales to ``1501500``
        instead of tripping on its binary representation.

        Negative prices are permitted: settlement prices genuinely went negative in
        the April 2020 WTI expiry, and an archive that rejects them loses exactly the
        session a researcher most wants.
        """
        if price != price or price in (float("inf"), float("-inf")):
            raise ValueError(f"tick[{index}]: price must be finite, got {price!r}.")

        scaled = Decimal(repr(float(price))).scaleb(self.price_scale_decimals)
        quantized = scaled.to_integral_value(rounding=ROUND_HALF_EVEN)
        residual = abs(scaled - quantized)
        noise_bound = abs(scaled) * _PRICE_NOISE_RELATIVE + _PRICE_NOISE_ABSOLUTE
        return int(quantized), residual > noise_bound

    def _validate_tick(
        self,
        tick: RawTickRecord,
        index: int,
        prev_timestamp: Optional[int],
    ) -> None:
        if not isinstance(tick.timestamp_nanos, int) or isinstance(tick.timestamp_nanos, bool):
            raise TypeError(
                f"tick[{index}]: timestamp_nanos must be an int, got "
                f"{type(tick.timestamp_nanos).__name__}."
            )
        if tick.timestamp_nanos < 0:
            raise ValueError(f"tick[{index}]: timestamp_nanos must be non-negative.")
        if prev_timestamp is not None and tick.timestamp_nanos < prev_timestamp:
            # Delta encoding an out-of-order batch succeeds and decodes cleanly, which
            # is exactly why it is dangerous: the archive then preserves an ordering
            # the feed never had. Sort upstream so the reordering is auditable.
            raise ValueError(
                f"tick[{index}]: timestamps must be non-decreasing "
                f"({tick.timestamp_nanos} < {prev_timestamp}). Sort the batch before "
                "encoding; do not let the archive imply an order the feed lacked."
            )
        if not isinstance(tick.quantity, int) or isinstance(tick.quantity, bool):
            raise TypeError(
                f"tick[{index}]: quantity must be an int, got "
                f"{type(tick.quantity).__name__}."
            )
        if tick.quantity < 0:
            raise ValueError(
                f"tick[{index}]: quantity must be non-negative, got {tick.quantity}."
            )
        if not isinstance(tick.side, str) or tick.side.upper() not in SIDE_CODES:
            raise ValueError(
                f"tick[{index}]: side must be one of {sorted(SIDE_CODES)}, got "
                f"{tick.side!r}. An unrecognised side is not silently recorded as "
                "SELL -- use 'UNKNOWN' when the feed does not classify aggressor side."
            )

    def delta_encode_ticks(self, ticks: Sequence[RawTickRecord]) -> bytes:
        """
        Delta-encode ``ticks`` into a self-describing column-major binary buffer.

        See :meth:`encode_with_precision_report` when the count of lossily-quantized
        prices is needed alongside the buffer.

        Layout: a 14-byte header (magic, format version, price scale, tick count)
        followed by four columns -- timestamp deltas, scaled price deltas, quantities
        (all int64, big-endian) and side codes (uint8). The first element of each
        delta column is the absolute value, so the sequence is self-contained.

        Column-major is what makes the ``columnar`` claim in this skill's name true
        rather than decorative. Row-major interleaving puts a timestamp delta next to
        a price delta next to a quantity, so the compressor sees high-entropy
        alternation; grouping each field's bytes lets it see runs of similar
        magnitude. On a 20k-tick synthetic batch at the same zlib level, column-major
        produced ~4.7% less output than row-major -- a real but modest gain, which is
        why production archives should use a Parquet writer's per-column encoding
        rather than this single-blob approximation.

        Raises:
            TypeError / ValueError: on a malformed tick -- see ``_validate_tick``.
        """
        return self.encode_with_precision_report(ticks)[0]

    def encode_with_precision_report(
        self,
        ticks: Sequence[RawTickRecord],
    ) -> Tuple[bytes, int]:
        """
        Delta-encode ``ticks`` and report how many prices were quantized lossily.

        Returns ``(buffer, price_precision_loss_ticks)``. The count is returned rather
        than stashed on the instance so a single engine can be shared across threads
        compacting different symbols without the counts racing.
        """
        header = _HEADER.pack(
            FORMAT_MAGIC, FORMAT_VERSION, self.price_scale_decimals, len(ticks)
        )
        if not ticks:
            return header, 0

        precision_loss_ticks = 0
        timestamp_deltas: List[int] = []
        price_deltas: List[int] = []
        quantities: List[int] = []
        side_codes = bytearray()

        prev_timestamp: Optional[int] = None
        prev_price_int = 0

        for index, tick in enumerate(ticks):
            self._validate_tick(tick, index, prev_timestamp)

            timestamp_deltas.append(
                tick.timestamp_nanos if prev_timestamp is None
                else tick.timestamp_nanos - prev_timestamp
            )
            prev_timestamp = tick.timestamp_nanos

            price_int, lossy = self._quantize_price(tick.price, index)
            precision_loss_ticks += int(lossy)
            price_deltas.append(price_int if index == 0 else price_int - prev_price_int)
            prev_price_int = price_int

            quantities.append(tick.quantity)
            side_codes.append(SIDE_CODES[tick.side.upper()])

        if precision_loss_ticks:
            logger.warning(
                "%d of %d ticks lost price precision at %d decimals; the discarded "
                "digits are not recoverable from this archive. Raise "
                "price_scale_decimals to match the instrument's quoting convention.",
                precision_loss_ticks, len(ticks), self.price_scale_decimals,
            )

        buffer = b"".join((
            header,
            _pack_int64_column(timestamp_deltas),
            _pack_int64_column(price_deltas),
            _pack_int64_column(quantities),
            bytes(side_codes),
        ))
        return buffer, precision_loss_ticks

    def delta_decode_ticks(self, buffer: bytes) -> List[RawTickRecord]:
        """
        Reconstruct the tick batch from a buffer produced by :meth:`delta_encode_ticks`.

        Round-trips exactly for any price exactly representable at the scale recorded
        in the buffer's own header. The scale is read from the buffer, not from the
        engine, so a blob written at one scale decodes correctly on an engine
        configured with another.

        The decoded price is always the canonical value at that scale. Feeding in a
        float that is a hair off a representable price (``1.23456 + 2 * 0.00001``
        evaluates to ``1.2345799999999999``) returns the clean ``1.23458``, which
        compares unequal to the input float even though nothing meaningful was lost.
        Compare decoded prices to within half a tick, not with ``==``.

        Raises:
            ValueError: on a wrong magic, an unsupported format version, or a
                truncated/oversized buffer. A decoder that guesses at malformed input
                produces plausible-looking wrong ticks, which is worse than failing.
        """
        if len(buffer) < _HEADER.size:
            raise ValueError(
                f"Buffer too short to contain a {_HEADER.size}-byte header "
                f"(got {len(buffer)} bytes)."
            )
        magic, version, price_scale_decimals, count = _HEADER.unpack_from(buffer, 0)
        if magic != FORMAT_MAGIC:
            raise ValueError(f"Not a tick archive buffer: bad magic {magic!r}.")
        if version != FORMAT_VERSION:
            raise ValueError(
                f"Unsupported tick archive format version {version} "
                f"(this decoder supports {FORMAT_VERSION})."
            )
        _validate_price_scale(price_scale_decimals)

        expected = _HEADER.size + count * (3 * _INT64_BYTES + 1)
        if len(buffer) != expected:
            raise ValueError(
                f"Truncated or oversized buffer: header declares {count} ticks "
                f"({expected} bytes expected), got {len(buffer)} bytes."
            )
        if count == 0:
            return []

        offset = _HEADER.size
        timestamp_deltas = _unpack_int64_column(buffer, offset, count)
        offset += count * _INT64_BYTES
        price_deltas = _unpack_int64_column(buffer, offset, count)
        offset += count * _INT64_BYTES
        quantities = _unpack_int64_column(buffer, offset, count)
        offset += count * _INT64_BYTES
        side_codes = buffer[offset:offset + count]

        divisor = float(10 ** price_scale_decimals)
        ticks: List[RawTickRecord] = []
        timestamp = 0
        price_int = 0
        for index in range(count):
            timestamp = (
                timestamp_deltas[index] if index == 0
                else timestamp + timestamp_deltas[index]
            )
            price_int = (
                price_deltas[index] if index == 0
                else price_int + price_deltas[index]
            )
            code = side_codes[index]
            if code not in SIDE_NAMES:
                raise ValueError(f"tick[{index}]: unknown side code {code} in archive.")
            ticks.append(RawTickRecord(
                timestamp_nanos=timestamp,
                price=price_int / divisor,
                quantity=quantities[index],
                side=SIDE_NAMES[code],
            ))
        return ticks

    # --------------------------------------------------------------- compaction

    def _compress(self, payload: bytes) -> bytes:
        if self.codec == "zstd":
            return self._zstd.ZstdCompressor(level=self.compression_level).compress(payload)
        return zlib.compress(payload, self.compression_level)

    def _assign_tier(self, age_days: int) -> str:
        if age_days <= self.hot_tier_max_age_days:
            return "HOT_TIER"
        if age_days <= self.warm_tier_max_age_days:
            return "WARM_TIER"
        return "COLD_TIER"

    def compact_and_archive_ticks(
        self,
        symbol: str,
        ticks: Sequence[RawTickRecord],
        age_days: Optional[int] = None,
        raw_size_bytes: Optional[int] = None,
    ) -> TickStorageCompactionReport:
        """
        Delta-encode, compact, and tier a tick batch.

        Args:
            symbol: Instrument the batch belongs to (reporting only).
            ticks: Non-empty batch, sorted by non-decreasing ``timestamp_nanos``.
            age_days: Age of the data, driving tier assignment. **Required** -- there
                is no safe default, and a guessed age produces a confident-looking
                tier assignment that can be wrong by two tiers.
            raw_size_bytes: Measured size of the source representation this archive
                replaces. Supply it whenever a real file is being replaced; the ratio
                then describes that file. When omitted, the canonical CSV baseline
                (:func:`canonical_csv_size_bytes`) is measured from the records
                instead, and the choice is recorded on the report as
                ``raw_size_basis``.

        Returns:
            A :class:`TickStorageCompactionReport`. ``compression_ratio`` is only
            interpretable together with ``raw_size_basis``.

        Raises:
            TypeError / ValueError: on an empty batch, a missing/negative
                ``age_days``, a non-positive ``raw_size_bytes``, or a malformed tick.
        """
        if not ticks:
            raise ValueError("Ticks list cannot be empty.")
        if age_days is None:
            raise ValueError(
                "age_days is required: tier assignment has no safe default. Pass the "
                "actual age of the batch in days."
            )
        if not isinstance(age_days, int) or isinstance(age_days, bool):
            raise TypeError(f"age_days must be an int, got {type(age_days).__name__}.")
        if age_days < 0:
            raise ValueError(f"age_days must be non-negative, got {age_days}.")

        delta_binary, precision_loss_ticks = self.encode_with_precision_report(ticks)
        compressed_binary = self._compress(delta_binary)
        compacted_size = len(compressed_binary)

        if raw_size_bytes is None:
            raw_size = canonical_csv_size_bytes(ticks, self.price_scale_decimals)
            raw_size_basis = RAW_SIZE_BASIS_CANONICAL_CSV
        else:
            if not isinstance(raw_size_bytes, int) or isinstance(raw_size_bytes, bool):
                raise TypeError("raw_size_bytes must be an int.")
            if raw_size_bytes <= 0:
                raise ValueError(f"raw_size_bytes must be positive, got {raw_size_bytes}.")
            raw_size = raw_size_bytes
            raw_size_basis = RAW_SIZE_BASIS_MEASURED

        comp_ratio = round(raw_size / float(compacted_size), 2)
        savings_pct = round((1.0 - (compacted_size / float(raw_size))) * 100.0, 2)
        meets_target = comp_ratio >= self.target_min_compression_ratio
        tier = self._assign_tier(age_days)

        notes = (
            f"TICK COMPACTION COMPLETE [{symbol} - {tier}]: Processed {len(ticks):,} ticks. "
            f"Raw Size = {raw_size:,} B ({raw_size_basis}) -> Compacted Size = "
            f"{compacted_size:,} B via {self.codec} level {self.compression_level}. "
            f"Compression Ratio = {comp_ratio}x ({savings_pct:.1f}% space savings); "
            f"target {self.target_min_compression_ratio}x "
            f"{'MET' if meets_target else 'NOT MET'}."
        )
        if precision_loss_ticks:
            notes += (
                f" WARNING: {precision_loss_ticks:,} tick(s) lost price precision at "
                f"{self.price_scale_decimals} decimals."
            )
        logger.info(notes)

        return TickStorageCompactionReport(
            symbol=symbol,
            total_ticks_processed=len(ticks),
            raw_size_bytes=raw_size,
            compacted_size_bytes=compacted_size,
            compression_ratio=comp_ratio,
            space_savings_pct=savings_pct,
            storage_tier=tier,
            audit_notes=notes,
            raw_size_basis=raw_size_basis,
            codec=self.codec,
            compression_level=self.compression_level,
            price_scale_decimals=self.price_scale_decimals,
            price_precision_loss_ticks=precision_loss_ticks,
            meets_compression_target=meets_target,
            format_version=FORMAT_VERSION,
        )
