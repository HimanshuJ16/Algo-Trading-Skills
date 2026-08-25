"""Unit tests for historical-tick-data-storage-and-compaction."""
import logging
import struct
import unittest
import zlib

from historical_tick_data_storage_and_compaction import (
    FORMAT_MAGIC,
    FORMAT_VERSION,
    RAW_SIZE_BASIS_CANONICAL_CSV,
    RAW_SIZE_BASIS_MEASURED,
    SIDE_CODES,
    HistoricalTickStorageCompactionEngine,
    RawTickRecord,
    canonical_csv_size_bytes,
)

BASE_NS = 1_700_000_000_000_000_000

MODULE_LOGGER = "historical_tick_data_storage_and_compaction"


def setUpModule():
    """The engine warns by design on precision loss; keep the suite output quiet."""
    logging.getLogger(MODULE_LOGGER).addHandler(logging.NullHandler())
    logging.getLogger(MODULE_LOGGER).propagate = False


#: Header is magic(4) + version(1) + scale(1) + count(8).
HEADER_BYTES = 14
#: Each tick contributes three int64 columns plus a one-byte side code.
BYTES_PER_TICK = 3 * 8 + 1


def cent_priced_ticks(count: int) -> list:
    """
    Ticks whose prices are exact multiples of one cent, so they are exactly
    representable at the 4-decimal default scale and round-trip bit-for-bit.

    Prices are built from integer cents rather than repeated float addition
    (``150.00 + i * 0.05`` accumulates binary error and is *not* exactly
    representable), so expected values can be stated by hand.
    """
    return [
        RawTickRecord(
            timestamp_nanos=BASE_NS + (i * 10_000_000),
            price=(15000 + (i % 10) * 5) / 100.0,
            quantity=100 + (i % 50),
            side="BUY" if i % 2 == 0 else "SELL",
        )
        for i in range(count)
    ]


class TestCanonicalCsvBaseline(unittest.TestCase):
    """The baseline must be measured from the records, never estimated per row."""

    def test_size_is_the_actual_serialized_length(self):
        ticks = [RawTickRecord(1_700_000_000_000_000_000, 150.25, 100, "BUY")]
        # "1700000000000000000,150.2500,100,BUY\n" -- counted by hand:
        # 19 ts + 1 comma + 8 price + 1 comma + 3 qty + 1 comma + 3 side + 1 newline.
        self.assertEqual(canonical_csv_size_bytes(ticks), 19 + 1 + 8 + 1 + 3 + 1 + 3 + 1)

    def test_size_tracks_price_scale(self):
        ticks = [RawTickRecord(BASE_NS, 150.25, 100, "BUY")]
        at_two = canonical_csv_size_bytes(ticks, price_scale_decimals=2)
        at_six = canonical_csv_size_bytes(ticks, price_scale_decimals=6)
        self.assertEqual(at_six - at_two, 4)

    def test_is_not_a_fixed_bytes_per_row_constant(self):
        """Regression: the baseline used to be a hard-coded 60 bytes per tick."""
        short = [RawTickRecord(1, 1.0, 1, "BUY")]
        long_ = [RawTickRecord(BASE_NS, 123456.5, 1_000_000, "UNKNOWN")]
        self.assertNotEqual(canonical_csv_size_bytes(short), canonical_csv_size_bytes(long_))
        self.assertNotEqual(canonical_csv_size_bytes(short), 60)


class TestRoundTrip(unittest.TestCase):
    """A tick archive that cannot be decoded is not an archive."""

    def setUp(self):
        self.engine = HistoricalTickStorageCompactionEngine()

    def test_round_trip_is_exact_for_representable_prices(self):
        ticks = cent_priced_ticks(1000)
        self.assertEqual(self.engine.delta_decode_ticks(
            self.engine.delta_encode_ticks(ticks)), ticks)

    def test_round_trip_preserves_all_three_side_values(self):
        ticks = [
            RawTickRecord(BASE_NS, 10.0, 1, "BUY"),
            RawTickRecord(BASE_NS + 1, 10.0, 2, "SELL"),
            RawTickRecord(BASE_NS + 2, 10.0, 3, "UNKNOWN"),
        ]
        self.assertEqual(
            [t.side for t in self.engine.delta_decode_ticks(
                self.engine.delta_encode_ticks(ticks))],
            ["BUY", "SELL", "UNKNOWN"],
        )

    def test_round_trip_survives_high_priced_instrument(self):
        """Regression: the price delta was an int32, so a $700k share overflowed it."""
        ticks = [
            RawTickRecord(BASE_NS, 712345.6789, 1, "BUY"),
            RawTickRecord(BASE_NS + 1, 712400.0, 2, "SELL"),
        ]
        decoded = self.engine.delta_decode_ticks(self.engine.delta_encode_ticks(ticks))
        self.assertEqual(decoded, ticks)

    def test_round_trip_survives_negative_prices(self):
        """April 2020 WTI settled negative; the archive must keep that session."""
        ticks = [
            RawTickRecord(BASE_NS, -37.63, 1, "SELL"),
            RawTickRecord(BASE_NS + 1, -10.0, 1, "BUY"),
        ]
        self.assertEqual(self.engine.delta_decode_ticks(
            self.engine.delta_encode_ticks(ticks)), ticks)

    def test_round_trip_handles_duplicate_timestamps(self):
        """Two trades in the same nanosecond are legal and must not be rejected."""
        ticks = [
            RawTickRecord(BASE_NS, 10.0, 1, "BUY"),
            RawTickRecord(BASE_NS, 10.5, 2, "SELL"),
        ]
        self.assertEqual(self.engine.delta_decode_ticks(
            self.engine.delta_encode_ticks(ticks)), ticks)

    def test_round_trip_handles_large_quantity(self):
        """Regression: quantity was a uint32, capping at ~4.29e9."""
        ticks = [RawTickRecord(BASE_NS, 1.0, 10_000_000_000, "BUY")]
        self.assertEqual(self.engine.delta_decode_ticks(
            self.engine.delta_encode_ticks(ticks))[0].quantity, 10_000_000_000)

    def test_empty_batch_encodes_to_a_decodable_header(self):
        buffer = self.engine.delta_encode_ticks([])
        self.assertEqual(len(buffer), HEADER_BYTES)
        self.assertEqual(self.engine.delta_decode_ticks(buffer), [])

    def test_decoder_reads_scale_from_the_buffer_not_the_engine(self):
        writer = HistoricalTickStorageCompactionEngine(price_scale_decimals=8)
        ticks = [RawTickRecord(BASE_NS, 0.00012345, 1, "BUY")]
        buffer = writer.delta_encode_ticks(ticks)
        reader = HistoricalTickStorageCompactionEngine(price_scale_decimals=2)
        self.assertEqual(reader.delta_decode_ticks(buffer), ticks)


class TestEncodedLayout(unittest.TestCase):
    """The layout is an on-disk format; its shape is part of the contract."""

    def setUp(self):
        self.engine = HistoricalTickStorageCompactionEngine()

    def test_buffer_size_matches_the_declared_layout(self):
        buffer = self.engine.delta_encode_ticks(cent_priced_ticks(37))
        self.assertEqual(len(buffer), HEADER_BYTES + 37 * BYTES_PER_TICK)

    def test_header_carries_magic_version_scale_and_count(self):
        engine = HistoricalTickStorageCompactionEngine(price_scale_decimals=6)
        magic, version, scale, count = struct.unpack_from(
            ">4sBBQ", engine.delta_encode_ticks(cent_priced_ticks(5)), 0)
        self.assertEqual(magic, FORMAT_MAGIC)
        self.assertEqual(version, FORMAT_VERSION)
        self.assertEqual(scale, 6)
        self.assertEqual(count, 5)

    def test_first_delta_element_is_the_absolute_value(self):
        ticks = [
            RawTickRecord(BASE_NS, 150.25, 7, "BUY"),
            RawTickRecord(BASE_NS + 500, 150.30, 9, "SELL"),
        ]
        buffer = self.engine.delta_encode_ticks(ticks)
        ts_column = struct.unpack_from(">2q", buffer, HEADER_BYTES)
        price_column = struct.unpack_from(">2q", buffer, HEADER_BYTES + 16)
        self.assertEqual(ts_column, (BASE_NS, 500))
        # 150.25 and 150.30 at 4 decimals are 1502500 and 1503000; delta 500.
        self.assertEqual(price_column, (1502500, 500))

    def test_side_codes_form_the_final_column(self):
        ticks = [
            RawTickRecord(BASE_NS, 1.0, 1, "UNKNOWN"),
            RawTickRecord(BASE_NS + 1, 1.0, 1, "BUY"),
            RawTickRecord(BASE_NS + 2, 1.0, 1, "SELL"),
        ]
        buffer = self.engine.delta_encode_ticks(ticks)
        self.assertEqual(
            list(buffer[-3:]),
            [SIDE_CODES["UNKNOWN"], SIDE_CODES["BUY"], SIDE_CODES["SELL"]],
        )

    def test_column_major_beats_row_major_on_the_same_data(self):
        """
        The 'columnar' claim in this skill's name has to be worth something. Compare
        the shipped column-major buffer against a row-major packing of the identical
        deltas at the identical zlib level.
        """
        ticks = cent_priced_ticks(5000)
        column_major = self.engine.delta_encode_ticks(ticks)
        count = len(ticks)
        ts = struct.unpack_from(f">{count}q", column_major, HEADER_BYTES)
        px = struct.unpack_from(f">{count}q", column_major, HEADER_BYTES + count * 8)
        qty = struct.unpack_from(f">{count}q", column_major, HEADER_BYTES + count * 16)
        sides = column_major[HEADER_BYTES + count * 24:]
        row_major = column_major[:HEADER_BYTES] + b"".join(
            struct.pack(">qqqB", a, b, c, d) for a, b, c, d in zip(ts, px, qty, sides))
        self.assertEqual(len(row_major), len(column_major))
        self.assertLess(
            len(zlib.compress(column_major, 9)), len(zlib.compress(row_major, 9)))


class TestDecoderRejectsMalformedInput(unittest.TestCase):
    """Guessing at a corrupt archive yields plausible wrong ticks -- worse than failing."""

    def setUp(self):
        self.engine = HistoricalTickStorageCompactionEngine()
        self.buffer = self.engine.delta_encode_ticks(cent_priced_ticks(10))

    def test_rejects_buffer_shorter_than_the_header(self):
        with self.assertRaises(ValueError):
            self.engine.delta_decode_ticks(self.buffer[:HEADER_BYTES - 1])

    def test_rejects_wrong_magic(self):
        with self.assertRaises(ValueError):
            self.engine.delta_decode_ticks(b"XXXX" + self.buffer[4:])

    def test_rejects_unsupported_format_version(self):
        corrupted = bytearray(self.buffer)
        corrupted[4] = FORMAT_VERSION + 1
        with self.assertRaises(ValueError):
            self.engine.delta_decode_ticks(bytes(corrupted))

    def test_rejects_truncated_payload(self):
        with self.assertRaises(ValueError):
            self.engine.delta_decode_ticks(self.buffer[:-1])

    def test_rejects_trailing_garbage(self):
        with self.assertRaises(ValueError):
            self.engine.delta_decode_ticks(self.buffer + b"\x00")

    def test_rejects_unknown_side_code(self):
        corrupted = bytearray(self.buffer)
        corrupted[-1] = 99
        with self.assertRaises(ValueError):
            self.engine.delta_decode_ticks(bytes(corrupted))


class TestTickValidation(unittest.TestCase):
    """Malformed ticks must fail loudly; silent coercion corrupts the archive."""

    def setUp(self):
        self.engine = HistoricalTickStorageCompactionEngine()

    def test_rejects_out_of_order_timestamps(self):
        ticks = [
            RawTickRecord(BASE_NS + 100, 1.0, 1, "BUY"),
            RawTickRecord(BASE_NS, 1.0, 1, "SELL"),
        ]
        with self.assertRaises(ValueError):
            self.engine.delta_encode_ticks(ticks)

    def test_rejects_unrecognised_side_instead_of_coercing_to_sell(self):
        """Regression: `1 if side == 'BUY' else 2` labelled every odd value SELL."""
        for bad_side in ("SEL", "", "buyy", "N/A"):
            with self.subTest(side=bad_side):
                with self.assertRaises(ValueError):
                    self.engine.delta_encode_ticks(
                        [RawTickRecord(BASE_NS, 1.0, 1, bad_side)])

    def test_accepts_lowercase_side(self):
        decoded = self.engine.delta_decode_ticks(
            self.engine.delta_encode_ticks([RawTickRecord(BASE_NS, 1.0, 1, "buy")]))
        self.assertEqual(decoded[0].side, "BUY")

    def test_rejects_non_finite_price(self):
        for bad_price in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(price=bad_price):
                with self.assertRaises(ValueError):
                    self.engine.delta_encode_ticks(
                        [RawTickRecord(BASE_NS, bad_price, 1, "BUY")])

    def test_rejects_negative_quantity(self):
        with self.assertRaises(ValueError):
            self.engine.delta_encode_ticks([RawTickRecord(BASE_NS, 1.0, -5, "BUY")])

    def test_rejects_negative_timestamp(self):
        with self.assertRaises(ValueError):
            self.engine.delta_encode_ticks([RawTickRecord(-1, 1.0, 1, "BUY")])

    def test_rejects_non_integer_timestamp(self):
        with self.assertRaises(TypeError):
            self.engine.delta_encode_ticks(
                [RawTickRecord(float(BASE_NS), 1.0, 1, "BUY")])

    def test_error_message_names_the_offending_index(self):
        ticks = cent_priced_ticks(5) + [RawTickRecord(BASE_NS + 10**9, 1.0, -1, "BUY")]
        with self.assertRaises(ValueError) as ctx:
            self.engine.delta_encode_ticks(ticks)
        self.assertIn("tick[5]", str(ctx.exception))


class TestPricePrecisionLoss(unittest.TestCase):
    """Precision lost in an archive is unrecoverable, so it must be reported."""

    def test_float_noise_is_not_reported_as_precision_loss(self):
        """
        A price walk built by repeated addition drifts off the exact decimal
        (``150.0`` plus 0.05 three times is ``150.15000000000003``), and ``0.1 + 0.2``
        is famously ``0.30000000000000004``. Neither is real precision loss; flagging
        them would make the warning useless.
        """
        engine = HistoricalTickStorageCompactionEngine()
        walked = 150.0
        for _ in range(3):
            walked += 0.05
        self.assertNotEqual(walked, 150.15)

        for price in (walked, 0.1 + 0.2):
            with self.subTest(price=repr(price)):
                _, lost = engine.encode_with_precision_report(
                    [RawTickRecord(BASE_NS, price, 1, "BUY")])
                self.assertEqual(lost, 0)

    def test_five_decimal_fx_quote_at_four_decimals_is_reported(self):
        engine = HistoricalTickStorageCompactionEngine()
        _, lost = engine.encode_with_precision_report(
            [RawTickRecord(BASE_NS, 1.23456, 1_000_000, "UNKNOWN")])
        self.assertEqual(lost, 1)

    def test_same_quote_at_five_decimals_is_lossless(self):
        engine = HistoricalTickStorageCompactionEngine(price_scale_decimals=5)
        ticks = [RawTickRecord(BASE_NS, 1.23456, 1_000_000, "UNKNOWN")]
        buffer, lost = engine.encode_with_precision_report(ticks)
        self.assertEqual(lost, 0)
        self.assertEqual(engine.delta_decode_ticks(buffer), ticks)

    def test_eight_decimal_crypto_quote_at_four_decimals_is_reported(self):
        engine = HistoricalTickStorageCompactionEngine()
        _, lost = engine.encode_with_precision_report(
            [RawTickRecord(BASE_NS, 0.00012345, 1, "BUY")])
        self.assertEqual(lost, 1)

    def test_loss_is_surfaced_on_the_report_and_logged(self):
        engine = HistoricalTickStorageCompactionEngine()
        ticks = [RawTickRecord(BASE_NS + i, 1.23456, 1, "BUY") for i in range(3)]
        with self.assertLogs(
                MODULE_LOGGER, level=logging.WARNING):
            report = engine.compact_and_archive_ticks("EURUSD", ticks, age_days=1)
        self.assertEqual(report.price_precision_loss_ticks, 3)
        self.assertIn("lost price precision", report.audit_notes)

    def test_encode_is_stateless_across_calls(self):
        """The loss count is returned, not accumulated on the engine."""
        engine = HistoricalTickStorageCompactionEngine()
        lossy = [RawTickRecord(BASE_NS, 1.23456, 1, "BUY")]
        clean = [RawTickRecord(BASE_NS, 1.2345, 1, "BUY")]
        self.assertEqual(engine.encode_with_precision_report(lossy)[1], 1)
        self.assertEqual(engine.encode_with_precision_report(clean)[1], 0)
        self.assertEqual(engine.encode_with_precision_report(lossy)[1], 1)


class TestCompactionReport(unittest.TestCase):

    def setUp(self):
        self.engine = HistoricalTickStorageCompactionEngine(target_min_compression_ratio=5.0)
        self.ticks = cent_priced_ticks(1000)

    def test_ratio_is_measured_against_the_declared_basis(self):
        report = self.engine.compact_and_archive_ticks("AAPL", self.ticks, age_days=100)
        self.assertEqual(report.raw_size_basis, RAW_SIZE_BASIS_CANONICAL_CSV)
        self.assertEqual(
            report.raw_size_bytes, canonical_csv_size_bytes(self.ticks, 4))
        self.assertAlmostEqual(
            report.compression_ratio,
            round(report.raw_size_bytes / report.compacted_size_bytes, 2),
            places=2,
        )

    def test_explicit_raw_size_overrides_the_baseline_and_is_flagged(self):
        report = self.engine.compact_and_archive_ticks(
            "AAPL", self.ticks, age_days=100, raw_size_bytes=400_000)
        self.assertEqual(report.raw_size_bytes, 400_000)
        self.assertEqual(report.raw_size_basis, RAW_SIZE_BASIS_MEASURED)
        self.assertAlmostEqual(
            report.compression_ratio,
            round(400_000 / report.compacted_size_bytes, 2), places=2)

    def test_savings_pct_is_consistent_with_the_ratio(self):
        report = self.engine.compact_and_archive_ticks("AAPL", self.ticks, age_days=100)
        expected = (1.0 - report.compacted_size_bytes / report.raw_size_bytes) * 100.0
        self.assertAlmostEqual(report.space_savings_pct, expected, places=2)

    def test_compacted_size_is_the_actual_compressed_length(self):
        # Pinned to zlib: the assertion compares against a specific codec's output, so
        # it must not depend on whether the optional zstandard package is installed.
        engine = HistoricalTickStorageCompactionEngine(codec="zlib", compression_level=9)
        report = engine.compact_and_archive_ticks("AAPL", self.ticks, age_days=100)
        expected = len(zlib.compress(engine.delta_encode_ticks(self.ticks), 9))
        self.assertEqual(report.compacted_size_bytes, expected)

    def test_target_ratio_is_enforced_rather_than_ignored(self):
        """Regression: target_min_compression_ratio was stored and never used."""
        report = self.engine.compact_and_archive_ticks("AAPL", self.ticks, age_days=100)
        self.assertTrue(report.meets_compression_target)
        self.assertIn("MET", report.audit_notes)

        strict = HistoricalTickStorageCompactionEngine(
            target_min_compression_ratio=report.compression_ratio + 1.0)
        strict_report = strict.compact_and_archive_ticks("AAPL", self.ticks, age_days=100)
        self.assertFalse(strict_report.meets_compression_target)
        self.assertIn("NOT MET", strict_report.audit_notes)

    def test_report_records_the_codec_actually_used(self):
        report = self.engine.compact_and_archive_ticks("AAPL", self.ticks, age_days=100)
        self.assertIn(report.codec, ("zlib", "zstd"))
        self.assertEqual(report.codec, self.engine.codec)
        self.assertIn(report.codec, report.audit_notes)

    def test_rejects_empty_batch(self):
        with self.assertRaises(ValueError):
            self.engine.compact_and_archive_ticks("AAPL", [], age_days=1)

    def test_rejects_non_positive_raw_size(self):
        for bad in (0, -1):
            with self.subTest(raw_size_bytes=bad):
                with self.assertRaises(ValueError):
                    self.engine.compact_and_archive_ticks(
                        "AAPL", self.ticks, age_days=1, raw_size_bytes=bad)


class TestStorageTiering(unittest.TestCase):

    def setUp(self):
        self.engine = HistoricalTickStorageCompactionEngine()
        self.ticks = cent_priced_ticks(50)

    def _tier(self, age_days, engine=None):
        engine = engine or self.engine
        return engine.compact_and_archive_ticks("AAPL", self.ticks, age_days=age_days).storage_tier

    def test_default_boundaries_are_inclusive_upper_bounds(self):
        self.assertEqual(self._tier(0), "HOT_TIER")
        self.assertEqual(self._tier(7), "HOT_TIER")
        self.assertEqual(self._tier(8), "WARM_TIER")
        self.assertEqual(self._tier(90), "WARM_TIER")
        self.assertEqual(self._tier(91), "COLD_TIER")

    def test_boundaries_are_configurable(self):
        engine = HistoricalTickStorageCompactionEngine(
            hot_tier_max_age_days=1, warm_tier_max_age_days=30)
        self.assertEqual(self._tier(1, engine), "HOT_TIER")
        self.assertEqual(self._tier(2, engine), "WARM_TIER")
        self.assertEqual(self._tier(31, engine), "COLD_TIER")

    def test_age_days_is_required(self):
        """Regression: age_days defaulted to 30, silently asserting WARM_TIER."""
        with self.assertRaises(ValueError):
            self.engine.compact_and_archive_ticks("AAPL", self.ticks)

    def test_rejects_negative_age(self):
        with self.assertRaises(ValueError):
            self.engine.compact_and_archive_ticks("AAPL", self.ticks, age_days=-1)

    def test_rejects_overlapping_tier_boundaries(self):
        with self.assertRaises(ValueError):
            HistoricalTickStorageCompactionEngine(
                hot_tier_max_age_days=90, warm_tier_max_age_days=7)


class TestEngineConfiguration(unittest.TestCase):

    def test_rejects_unknown_codec(self):
        with self.assertRaises(ValueError):
            HistoricalTickStorageCompactionEngine(codec="brotli")

    def test_explicit_zstd_never_silently_downgrades(self):
        """
        Either zstandard is installed and the engine uses it, or it is absent and the
        engine refuses. What it must never do is quietly write zlib while the report
        claims zstd.
        """
        try:
            engine = HistoricalTickStorageCompactionEngine(codec="zstd")
        except RuntimeError:
            self.skipTest("optional 'zstandard' package not installed")
        self.assertEqual(engine.codec, "zstd")
        ticks = cent_priced_ticks(100)
        report = engine.compact_and_archive_ticks("AAPL", ticks, age_days=1)
        self.assertEqual(report.codec, "zstd")
        self.assertEqual(engine.delta_decode_ticks(engine.delta_encode_ticks(ticks)), ticks)

    def test_auto_codec_resolves_to_a_concrete_codec(self):
        engine = HistoricalTickStorageCompactionEngine(codec="auto")
        self.assertIn(engine.codec, ("zlib", "zstd"))

    def test_rejects_out_of_range_zlib_level(self):
        with self.assertRaises(ValueError):
            HistoricalTickStorageCompactionEngine(codec="zlib", compression_level=10)

    def test_rejects_invalid_price_scale(self):
        for bad in (-1, 10):
            with self.subTest(price_scale_decimals=bad):
                with self.assertRaises(ValueError):
                    HistoricalTickStorageCompactionEngine(price_scale_decimals=bad)

    def test_rejects_non_positive_target_ratio(self):
        with self.assertRaises(ValueError):
            HistoricalTickStorageCompactionEngine(target_min_compression_ratio=0.0)


if __name__ == "__main__":
    unittest.main()
