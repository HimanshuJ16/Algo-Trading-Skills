"""
Tests for the Kafka tick distribution engine.

Where a hash is asserted, the expected value comes from an oracle *outside* this
implementation -- Apache Kafka's own published murmur2 test vectors, and a bit-by-bit
CRC-32 reference computed in this file. Asserting a hash against the same code that
produced it would test nothing.
"""
import unittest

from kafka_tick_engine import (
    CLIENT_DEFAULT_PARTITIONER,
    KafkaTickDistributionEngine,
    KafkaTickDistributionReport,
    MarketTickPayload,
    OFFSET_BASIS_COMMITTED,
    PARTITIONER_CRC32,
    PARTITIONER_MURMUR2,
    STATUS_CLOCK_SKEW,
    STATUS_CONSUMER_LAG,
    STATUS_HEALTHY,
    STATUS_OFFSET_INCONSISTENCY,
    STATUS_PARTITION_UNBALANCED,
    STATUS_STALE_TICKS,
    murmur2,
    normalize_symbol_key,
    partition_for_key,
)

#: Published expectations from Apache Kafka's ``UtilsTest.testMurmur2``. These cover
#: every tail-byte branch of the switch fall-through (lengths 2, 3, 6, 24, 26, 47).
KAFKA_MURMUR2_VECTORS = {
    "21": -973932308,
    "foobar": -790332482,
    "a-little-bit-long-string": -985981536,
    "a-little-bit-longer-string": -1486304829,
    "lkjh234lh9fiuh90y23oiuhsafujhadof229phr9h19h89h8": -58897971,
}

NS_PER_MS = 1_000_000
BASE_NS = 1_700_000_000_000_000_000  # a plausible Unix nanosecond timestamp


def reference_crc32(data: bytes) -> int:
    """
    Bit-by-bit IEEE 802.3 CRC-32 (reflected, polynomial 0xEDB88320).

    Independent of ``zlib``; this is the algorithm librdkafka's ``rd_crc32`` computes
    for its ``consistent`` partitioner.
    """
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xEDB88320 if crc & 1 else crc >> 32)
    return crc ^ 0xFFFFFFFF


def tick(symbol: str, timestamp_ns: int = BASE_NS) -> MarketTickPayload:
    return MarketTickPayload(symbol, timestamp_ns, 150.0, 150.05, 100, 100, 150.02, 50)


class TestHashPortability(unittest.TestCase):
    """The two hashes must match the client libraries they claim to reproduce."""

    def test_murmur2_matches_apache_kafka_published_vectors(self):
        for text, expected in KAFKA_MURMUR2_VECTORS.items():
            with self.subTest(text=text):
                self.assertEqual(murmur2(text.encode("utf-8")), expected)

    def test_murmur2_matches_kafka_vector_for_raw_byte_array(self):
        # UtilsTest asserts murmur2(new byte[] {'a','b','c'}) == 479470107
        self.assertEqual(murmur2(bytes([0x61, 0x62, 0x63])), 479470107)

    def test_murmur2_returns_signed_32_bit_value(self):
        # Kafka's Utils.murmur2 returns a Java int; the published vectors are negative,
        # so an implementation returning an unsigned hash would silently mis-partition.
        self.assertLess(murmur2(b"foobar"), 0)

    def test_murmur2_rejects_non_bytes(self):
        with self.assertRaises(TypeError):
            murmur2("AAPL")

    def test_crc32_partitioner_matches_independent_crc32_reference(self):
        for symbol in ("AAPL", "MSFT", "BRK.B", "7203.T"):
            with self.subTest(symbol=symbol):
                key = symbol.encode("utf-8")
                self.assertEqual(
                    partition_for_key(key, 16, PARTITIONER_CRC32),
                    reference_crc32(key) % 16,
                )

    def test_murmur2_partition_uses_documented_positive_mask(self):
        # librdkafka: (rd_murmur2(key) & 0x7fffffff) % partition_cnt
        # kafka-python: idx &= 0x7fffffff; idx %= len(all_partitions)
        key = b"foobar"
        self.assertEqual(
            partition_for_key(key, 16, PARTITIONER_MURMUR2),
            (KAFKA_MURMUR2_VECTORS["foobar"] & 0x7FFFFFFF) % 16,
        )

    def test_unknown_partitioner_rejected(self):
        with self.assertRaises(ValueError):
            partition_for_key(b"AAPL", 16, "fnv1a")

    def test_partition_for_key_rejects_zero_partitions(self):
        # Previously this path raised ZeroDivisionError from the modulo.
        with self.assertRaises(ValueError):
            partition_for_key(b"AAPL", 0, PARTITIONER_CRC32)


class TestSymbolKeyNormalization(unittest.TestCase):

    def test_case_and_whitespace_are_canonicalized(self):
        self.assertEqual(normalize_symbol_key("  aapl "), "AAPL")

    def test_normalized_variants_route_identically(self):
        engine = KafkaTickDistributionEngine(num_partitions=16)
        self.assertEqual(
            engine.get_symbol_partition_id(" aapl "),
            engine.get_symbol_partition_id("AAPL"),
        )

    def test_empty_symbol_is_rejected_not_routed_to_partition_zero(self):
        # An empty key is NOT partition 0: librdkafka's default consistent_random
        # partitioner scatters it at random, destroying per-symbol ordering silently.
        for bad in ("", "   ", "\t"):
            with self.subTest(symbol=repr(bad)):
                with self.assertRaises(ValueError):
                    normalize_symbol_key(bad)

    def test_non_string_symbol_is_rejected(self):
        with self.assertRaises(TypeError):
            normalize_symbol_key(None)


class TestMixedClientPartitionerHazard(unittest.TestCase):
    """
    The core defect this engine guards against: confluent-kafka (librdkafka) defaults
    to a CRC32 partitioner while the Java client, kafka-python and aiokafka default to
    murmur2. One symbol, one topic, two partitions.
    """

    def setUp(self):
        self.engine = KafkaTickDistributionEngine(num_partitions=16)

    def test_client_default_table_records_both_families(self):
        self.assertEqual(CLIENT_DEFAULT_PARTITIONER["confluent-kafka"], PARTITIONER_CRC32)
        self.assertEqual(CLIENT_DEFAULT_PARTITIONER["kafka-python"], PARTITIONER_MURMUR2)
        self.assertEqual(CLIENT_DEFAULT_PARTITIONER["java"], PARTITIONER_MURMUR2)

    def test_aapl_lands_on_different_partitions_across_client_families(self):
        key = b"AAPL"
        crc_partition = partition_for_key(key, 16, PARTITIONER_CRC32)
        murmur_partition = partition_for_key(key, 16, PARTITIONER_MURMUR2)
        self.assertNotEqual(
            crc_partition,
            murmur_partition,
            "AAPL must demonstrate the cross-client split this engine warns about.",
        )

    def test_divergence_diagnostic_reports_the_split(self):
        divergence = self.engine.diagnose_partitioner_divergence(["AAPL", "MSFT", "TSLA"])
        self.assertIn("AAPL", divergence)
        crc_partition, murmur_partition = divergence["AAPL"]
        self.assertEqual(crc_partition, partition_for_key(b"AAPL", 16, PARTITIONER_CRC32))
        self.assertEqual(
            murmur_partition, partition_for_key(b"AAPL", 16, PARTITIONER_MURMUR2)
        )

    def test_divergence_diagnostic_only_lists_genuinely_split_symbols(self):
        universe = ["AAPL", "MSFT", "TSLA", "NVDA", "AMZN", "GOOG", "META", "IBM"]
        divergence = self.engine.diagnose_partitioner_divergence(universe)
        for symbol, (crc_partition, murmur_partition) in divergence.items():
            with self.subTest(symbol=symbol):
                self.assertNotEqual(crc_partition, murmur_partition)
        for symbol in universe:
            if symbol not in divergence:
                key = symbol.encode("utf-8")
                self.assertEqual(
                    partition_for_key(key, 16, PARTITIONER_CRC32),
                    partition_for_key(key, 16, PARTITIONER_MURMUR2),
                )

    def test_default_partitioner_preserves_established_crc32_routing(self):
        # Regression guard: the shipped default stays CRC32 (librdkafka-compatible).
        # Silently flipping it would move every symbol on an existing topic.
        self.assertEqual(self.engine.partitioner, PARTITIONER_CRC32)
        self.assertEqual(
            self.engine.get_symbol_partition_id("AAPL"),
            reference_crc32(b"AAPL") % 16,
        )


class TestProducerConfig(unittest.TestCase):
    """
    Keying alone does not preserve order. Kafka: with max.in.flight > 1,
    enable.idempotence=false and retries enabled there is "a risk of message reordering
    after a failed send due to retries". librdkafka ships exactly that by default.
    """

    def test_confluent_config_enforces_all_three_ordering_preconditions(self):
        engine = KafkaTickDistributionEngine(num_partitions=16)
        config = engine.build_producer_config("confluent-kafka")
        self.assertTrue(config["enable.idempotence"])
        self.assertEqual(config["acks"], "all")
        self.assertLessEqual(config["max.in.flight.requests.per.connection"], 5)

    def test_confluent_config_pins_partitioner_rather_than_inheriting_default(self):
        engine = KafkaTickDistributionEngine(num_partitions=16)
        # 'consistent', not the 'consistent_random' default: the latter scatters an
        # empty key at random instead of failing.
        self.assertEqual(
            engine.build_producer_config("confluent-kafka")["partitioner"], "consistent"
        )
        murmur_engine = KafkaTickDistributionEngine(
            num_partitions=16, partitioner=PARTITIONER_MURMUR2
        )
        self.assertEqual(
            murmur_engine.build_producer_config("confluent-kafka")["partitioner"],
            "murmur2",
        )

    def test_confluent_config_carries_batching_settings(self):
        engine = KafkaTickDistributionEngine(
            num_partitions=16, batch_size_bytes=131_072, linger_ms=5
        )
        config = engine.build_producer_config("confluent-kafka")
        self.assertEqual(config["batch.size"], 131_072)
        self.assertEqual(config["linger.ms"], 5)

    def test_kafka_python_config_uses_underscore_dialect(self):
        engine = KafkaTickDistributionEngine(
            num_partitions=16, partitioner=PARTITIONER_MURMUR2
        )
        config = engine.build_producer_config("kafka-python")
        self.assertTrue(config["enable_idempotence"])
        self.assertEqual(config["acks"], "all")
        self.assertLessEqual(config["max_in_flight_requests_per_connection"], 5)
        self.assertEqual(config["linger_ms"], 5)

    def test_crc32_config_refused_for_murmur2_only_client(self):
        # kafka-python/aiokafka expose no CRC32 partitioner setting. Emitting a config
        # anyway would produce the exact cross-client split this engine detects.
        engine = KafkaTickDistributionEngine(
            num_partitions=16, partitioner=PARTITIONER_CRC32
        )
        for client in ("kafka-python", "aiokafka", "java"):
            with self.subTest(client=client):
                with self.assertRaises(ValueError):
                    engine.build_producer_config(client)

    def test_unknown_client_rejected(self):
        engine = KafkaTickDistributionEngine(num_partitions=16)
        with self.assertRaises(ValueError):
            engine.build_producer_config("sarama")


class TestPartitionGrowth(unittest.TestCase):

    def test_growth_remaps_symbols_and_reports_blast_radius(self):
        engine = KafkaTickDistributionEngine(num_partitions=16)
        universe = ["AAPL", "MSFT", "TSLA", "NVDA", "AMZN", "GOOG", "META", "IBM"]
        remapped = engine.symbols_remapped_by_partition_growth(universe, 32)

        self.assertTrue(remapped, "Doubling partitions must move at least one symbol.")
        for symbol, (old, new) in remapped.items():
            with self.subTest(symbol=symbol):
                self.assertNotEqual(old, new)
                self.assertEqual(old, reference_crc32(symbol.encode()) % 16)
                self.assertEqual(new, reference_crc32(symbol.encode()) % 32)

    def test_unchanged_partition_count_remaps_nothing(self):
        engine = KafkaTickDistributionEngine(num_partitions=16)
        self.assertEqual(
            engine.symbols_remapped_by_partition_growth(["AAPL", "MSFT"], 16), {}
        )

    def test_invalid_new_partition_count_rejected(self):
        engine = KafkaTickDistributionEngine(num_partitions=16)
        with self.assertRaises(ValueError):
            engine.symbols_remapped_by_partition_growth(["AAPL"], 0)


class TestConstructorValidation(unittest.TestCase):

    def test_zero_partitions_rejected(self):
        # Previously constructed an empty partition map and raised ZeroDivisionError
        # only later, at routing time.
        with self.assertRaises(ValueError):
            KafkaTickDistributionEngine(num_partitions=0)

    def test_negative_partitions_rejected(self):
        with self.assertRaises(ValueError):
            KafkaTickDistributionEngine(num_partitions=-4)

    def test_invalid_scalars_rejected(self):
        cases = [
            {"max_lag_threshold_ticks": -1},
            {"batch_size_bytes": 0},
            {"linger_ms": -5},
            {"partitioner": "fnv1a"},
            {"offset_basis": "LATEST"},
            {"max_tick_age_ms": 0},
            {"partition_skew_threshold": 0.5},
        ]
        for kwargs in cases:
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    KafkaTickDistributionEngine(num_partitions=16, **kwargs)


class TestPublishAndAudit(unittest.TestCase):

    def setUp(self):
        self.engine = KafkaTickDistributionEngine(
            num_partitions=16, max_lag_threshold_ticks=1000
        )

    def test_healthy_stream_processing(self):
        ticks = [
            tick("AAPL", BASE_NS),
            tick("MSFT", BASE_NS),
            tick("AAPL", BASE_NS + 1_000),
        ]
        self.engine.publish_and_audit_ticks(ticks)

        # Drain fully: committed offset == log end offset on every partition once the
        # final tick has been routed. Offsets are applied after routing, so the one
        # extra tick must be accounted for on its own partition.
        final_batch = [tick("AAPL", BASE_NS + 2_000)]
        drained = {
            p: state.log_end_offset
            for p, state in self.engine.partition_states.items()
        }
        drained[self.engine.get_symbol_partition_id("AAPL")] += len(final_batch)

        report = self.engine.publish_and_audit_ticks(
            final_batch, simulated_consumed_offsets=drained
        )

        self.assertEqual(report.status, STATUS_HEALTHY)
        self.assertEqual(report.max_consumer_lag_ticks, 0)
        self.assertEqual(report.offset_basis, OFFSET_BASIS_COMMITTED)

    def test_routing_map_and_counts(self):
        ticks = [tick("AAPL"), tick("MSFT"), tick("AAPL", BASE_NS + 1)]
        report = self.engine.publish_and_audit_ticks(ticks)

        self.assertEqual(report.total_ticks_processed, 3)
        self.assertIn("AAPL", report.symbols_partition_map)
        self.assertIn("MSFT", report.symbols_partition_map)
        aapl_partition = report.symbols_partition_map["AAPL"]
        self.assertEqual(report.partition_tick_counts[aapl_partition], 2)
        self.assertEqual(sum(report.partition_tick_counts.values()), 3)

    def test_consumer_lag_warning_trigger(self):
        ticks = [tick("AAPL", BASE_NS + i) for i in range(2000)]
        report = self.engine.publish_and_audit_ticks(
            ticks, simulated_consumed_offsets={0: 0}
        )

        self.assertEqual(report.status, STATUS_CONSUMER_LAG)
        self.assertEqual(report.max_consumer_lag_ticks, 2000)
        self.assertEqual(
            report.lagging_partition_id, self.engine.get_symbol_partition_id("AAPL")
        )

    def test_lag_exactly_at_threshold_is_not_a_breach(self):
        # The rule is strictly greater-than; the boundary itself stays healthy.
        ticks = [tick("AAPL", BASE_NS + i) for i in range(1000)]
        report = self.engine.publish_and_audit_ticks(ticks)
        self.assertEqual(report.max_consumer_lag_ticks, 1000)
        self.assertEqual(report.status, STATUS_HEALTHY)

    def test_one_tick_over_threshold_breaches(self):
        ticks = [tick("AAPL", BASE_NS + i) for i in range(1001)]
        report = self.engine.publish_and_audit_ticks(ticks)
        self.assertEqual(report.max_consumer_lag_ticks, 1001)
        self.assertEqual(report.status, STATUS_CONSUMER_LAG)

    def test_consumer_ahead_of_log_end_is_flagged_not_clamped_to_healthy(self):
        # Regression: previously max(0, lag) absorbed this and reported HEALTHY, so a
        # monitor wired to the wrong topic or a reset group looked fine forever.
        report = self.engine.publish_and_audit_ticks(
            [tick("AAPL")], simulated_consumed_offsets={0: 5}
        )
        self.assertEqual(report.status, STATUS_OFFSET_INCONSISTENCY)
        self.assertTrue(any("OFFSET_INCONSISTENCY" in w for w in report.warnings))

    def test_offset_inconsistency_outranks_lag_warning(self):
        ticks = [tick("AAPL", BASE_NS + i) for i in range(2000)]
        report = self.engine.publish_and_audit_ticks(
            ticks, simulated_consumed_offsets={0: 7}
        )
        # Both conditions hold; the untrustworthy measurement must win the status and
        # neither warning may be dropped.
        self.assertEqual(report.status, STATUS_OFFSET_INCONSISTENCY)
        self.assertTrue(any("CONSUMER_LAG" in w for w in report.warnings))

    def test_offsets_for_unknown_partition_are_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.publish_and_audit_ticks(
                [tick("AAPL")], simulated_consumed_offsets={99: 0}
            )

    def test_negative_offsets_are_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.publish_and_audit_ticks(
                [tick("AAPL")], simulated_consumed_offsets={0: -1}
            )

    def test_empty_tick_list_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.publish_and_audit_ticks([])

    def test_unroutable_symbol_rejected_before_any_state_mutation(self):
        good = tick("AAPL")
        bad = tick("   ")
        before = {p: s.log_end_offset for p, s in self.engine.partition_states.items()}

        with self.assertRaises(ValueError):
            self.engine.publish_and_audit_ticks([good, bad, good])

        after = {p: s.log_end_offset for p, s in self.engine.partition_states.items()}
        self.assertEqual(before, after, "A rejected batch must not partially publish.")

    def test_non_integer_timestamp_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.publish_and_audit_ticks([tick("AAPL", 1.5)])

    def test_throughput_is_measured_rather_than_fabricated(self):
        # Regression: the field was previously len(ticks) * 100.0 -- an invented number
        # reported under a metric name.
        ticks = [tick("AAPL", BASE_NS + i) for i in range(50)]
        report = self.engine.publish_and_audit_ticks(ticks)
        self.assertNotEqual(report.throughput_ticks_per_sec, len(ticks) * 100.0)
        self.assertGreater(report.throughput_ticks_per_sec, 0.0)


class TestStalenessAudit(unittest.TestCase):

    def test_staleness_check_is_off_unless_a_budget_is_set(self):
        engine = KafkaTickDistributionEngine(num_partitions=16)
        report = engine.publish_and_audit_ticks([tick("AAPL", BASE_NS)], now_ns=BASE_NS)
        self.assertIsNone(report.max_tick_age_ms)
        self.assertEqual(report.status, STATUS_HEALTHY)

    def test_stale_batch_is_flagged_on_time_not_message_count(self):
        engine = KafkaTickDistributionEngine(num_partitions=16, max_tick_age_ms=250.0)
        report = engine.publish_and_audit_ticks(
            [tick("AAPL", BASE_NS)], now_ns=BASE_NS + 900 * NS_PER_MS
        )
        self.assertEqual(report.status, STATUS_STALE_TICKS)
        self.assertAlmostEqual(report.max_tick_age_ms, 900.0, places=6)

    def test_fresh_batch_within_budget_is_healthy(self):
        engine = KafkaTickDistributionEngine(num_partitions=16, max_tick_age_ms=250.0)
        report = engine.publish_and_audit_ticks(
            [tick("AAPL", BASE_NS)], now_ns=BASE_NS + 100 * NS_PER_MS
        )
        self.assertEqual(report.status, STATUS_HEALTHY)
        self.assertAlmostEqual(report.max_tick_age_ms, 100.0, places=6)

    def test_future_dated_tick_is_flagged_as_clock_skew_not_reported_fresh(self):
        # Found by adversarial review: a tick timestamped ahead of this host's clock
        # yields a NEGATIVE age, which can never exceed a positive budget -- so the
        # staleness guard silently switches itself off exactly when the clocks it
        # depends on are wrong.
        engine = KafkaTickDistributionEngine(num_partitions=4, max_tick_age_ms=100.0)
        report = engine.publish_and_audit_ticks(
            [tick("AAPL", BASE_NS)], now_ns=BASE_NS - 5_000 * NS_PER_MS
        )
        self.assertEqual(report.status, STATUS_CLOCK_SKEW)
        self.assertAlmostEqual(report.max_tick_age_ms, -5000.0, places=6)
        self.assertTrue(any("CLOCK_SKEW" in w for w in report.warnings))

    def test_skew_within_tolerance_is_not_flagged(self):
        # Normal PTP/NTP jitter must not fire the alarm on every batch.
        engine = KafkaTickDistributionEngine(
            num_partitions=4, max_tick_age_ms=100.0, clock_skew_tolerance_ms=5.0
        )
        report = engine.publish_and_audit_ticks(
            [tick("AAPL", BASE_NS)], now_ns=BASE_NS - 2 * NS_PER_MS
        )
        self.assertEqual(report.status, STATUS_HEALTHY)
        self.assertAlmostEqual(report.max_tick_age_ms, -2.0, places=6)

    def test_negative_clock_skew_tolerance_rejected(self):
        with self.assertRaises(ValueError):
            KafkaTickDistributionEngine(num_partitions=4, clock_skew_tolerance_ms=-1.0)

    def test_age_is_taken_from_the_oldest_tick_in_the_batch(self):
        engine = KafkaTickDistributionEngine(num_partitions=16, max_tick_age_ms=1000.0)
        ticks = [
            tick("AAPL", BASE_NS + 500 * NS_PER_MS),
            tick("MSFT", BASE_NS),  # oldest
            tick("TSLA", BASE_NS + 800 * NS_PER_MS),
        ]
        report = engine.publish_and_audit_ticks(ticks, now_ns=BASE_NS + 900 * NS_PER_MS)
        self.assertAlmostEqual(report.max_tick_age_ms, 900.0, places=6)


class TestPartitionSkewAudit(unittest.TestCase):

    def test_hot_partition_is_flagged_when_the_universe_is_wide_enough(self):
        engine = KafkaTickDistributionEngine(
            num_partitions=4, max_lag_threshold_ticks=10_000_000
        )
        ticks = [tick("HOT", BASE_NS + i) for i in range(400)]
        ticks += [
            tick(f"SYM{i:03d}", BASE_NS + i) for i in range(20)
        ]
        report = engine.publish_and_audit_ticks(ticks)

        self.assertTrue(report.skew_audit_applicable)
        self.assertGreater(report.partition_skew_ratio, 2.0)
        self.assertEqual(report.status, STATUS_PARTITION_UNBALANCED)

    def test_skew_suppressed_when_symbols_cannot_fill_the_partitions(self):
        # Two symbols across 16 partitions MUST leave 14 empty. That is arithmetic,
        # not a defect, and must not raise a hot-partition alarm.
        engine = KafkaTickDistributionEngine(
            num_partitions=16, max_lag_threshold_ticks=10_000_000
        )
        ticks = [tick("AAPL", BASE_NS + i) for i in range(500)]
        ticks += [tick("MSFT", BASE_NS + i) for i in range(500)]
        report = engine.publish_and_audit_ticks(ticks)

        self.assertFalse(report.skew_audit_applicable)
        self.assertGreater(report.partition_skew_ratio, 2.0)
        self.assertEqual(report.status, STATUS_HEALTHY)
        self.assertIn("Skew audit suppressed", report.audit_notes)

    def test_even_spread_is_not_flagged(self):
        engine = KafkaTickDistributionEngine(
            num_partitions=2, max_lag_threshold_ticks=10_000_000
        )
        symbols = [f"SYM{i:03d}" for i in range(200)]
        report = engine.publish_and_audit_ticks(
            [tick(s, BASE_NS + i) for i, s in enumerate(symbols)]
        )
        self.assertTrue(report.skew_audit_applicable)
        self.assertLessEqual(report.partition_skew_ratio, 2.0)
        self.assertEqual(report.status, STATUS_HEALTHY)


class TestUpstreamOrdering(unittest.TestCase):

    def test_decreasing_event_timestamp_is_surfaced(self):
        # Partition ordering preserves arrival order; it cannot repair a feed that
        # already delivered ticks out of order.
        engine = KafkaTickDistributionEngine(num_partitions=16)
        ticks = [
            tick("AAPL", BASE_NS + 1_000),
            tick("AAPL", BASE_NS),           # goes backwards
            tick("MSFT", BASE_NS),
        ]
        report = engine.publish_and_audit_ticks(ticks)
        self.assertEqual(report.out_of_order_symbols, ["AAPL"])
        self.assertTrue(any("UPSTREAM_OUT_OF_ORDER" in w for w in report.warnings))

    def test_monotonic_stream_reports_no_disorder(self):
        engine = KafkaTickDistributionEngine(num_partitions=16)
        report = engine.publish_and_audit_ticks(
            [tick("AAPL", BASE_NS + i) for i in range(10)]
        )
        self.assertEqual(report.out_of_order_symbols, [])

    def test_equal_timestamps_are_not_disorder(self):
        # Two ticks in the same nanosecond are common on a batched feed and are not
        # evidence of reordering.
        engine = KafkaTickDistributionEngine(num_partitions=16)
        report = engine.publish_and_audit_ticks([tick("AAPL"), tick("AAPL")])
        self.assertEqual(report.out_of_order_symbols, [])

    def test_disorder_is_tracked_across_successive_batches(self):
        engine = KafkaTickDistributionEngine(num_partitions=16)
        engine.publish_and_audit_ticks([tick("AAPL", BASE_NS + 5_000)])
        report = engine.publish_and_audit_ticks([tick("AAPL", BASE_NS)])
        self.assertEqual(report.out_of_order_symbols, ["AAPL"])


class TestReportContract(unittest.TestCase):

    def test_report_is_the_documented_type_and_records_its_own_configuration(self):
        engine = KafkaTickDistributionEngine(
            num_partitions=8, partitioner=PARTITIONER_MURMUR2
        )
        report = engine.publish_and_audit_ticks([tick("AAPL")])
        self.assertIsInstance(report, KafkaTickDistributionReport)
        self.assertEqual(report.num_partitions, 8)
        self.assertEqual(report.partitioner, PARTITIONER_MURMUR2)
        # assigned_partition_id documents the LAST tick only -- assert that meaning.
        self.assertEqual(
            report.assigned_partition_id, engine.get_symbol_partition_id("AAPL")
        )


if __name__ == "__main__":
    unittest.main()
